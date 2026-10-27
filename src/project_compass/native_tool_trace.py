"""Observed native exec text pairs; no reasoning or semantic task promotion."""
from collections import Counter
from datetime import datetime
from hashlib import sha256
import re

from .models import EvidenceEvent


CAPTURE = 'native-public-exec-pairs-v1'
LOCAL_TOOLS = {'exec_command', 'write_stdin', 'apply_patch', 'view_image'}
JS_PART = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'|`(?:\\[\s\S]|[^`\\])*`|[A-Za-z_$][A-Za-z0-9_$]*|[^\s]')
PRIVATE_INPUT = re.compile(r'\.env\b|auth\.json|id_rsa|id_ed25519|credential|Get-Clipboard|clipboard\.read|os\.environ|process\.env|Get-ChildItem\s+Env:|printenv|adb\s+pair', re.I)
PRIVATE_TEXT = re.compile(r'://[^/\s]+:[^@/\s]+@|pairing\s*(?:code|pin)', re.I)
SENSITIVE_SWITCH = re.compile(r'^(?:--?|/)(?:password|passwd|token|secret|client[-_]?secret|(?:api|access|auth|refresh)[-_]?(?:key|token)|authorization|cookie)(?:[:=].*)?$', re.I)


def has_sensitive_switch(text):
    return any(SENSITIVE_SWITCH.fullmatch(word) for word in re.split(r'''[\s'";(),]+''', text))


def direct_local_calls(script):
    """Conservative syntax filter, not a JavaScript interpreter or sandbox.

    Strings/comments cannot name a tool. Dynamic templates, escaped identifiers,
    aliases and computed references are outside this adapter's supported subset.
    """
    tokens = []
    for part in JS_PART.findall(script):
        if part.startswith(('//', '/*')):
            continue
        if part[0] in {'"', "'", '`'}:
            if len(part) == 1 or (part[0] == '`' and '${' in part):
                return False
            tokens.append('<literal>')
        else:
            tokens.append(part)
    if any(token in {'\\', 'globalThis', 'eval', 'Function', 'Reflect', 'require', 'import', 'constructor', '__proto__'} for token in tokens):
        return False
    references = [i for i, token in enumerate(tokens) if token == 'tools']
    return bool(references) and all(
        tokens[i+1:i+2] == ['.'] and len(tokens) > i+3 and
        tokens[i+2] in LOCAL_TOOLS and tokens[i+3] == '('
        for i in references)


class ToolTraceCollector:
    def __init__(self, thread_id, member_id, sensitive, *, max_pairs=200):
        if type(max_pairs) is not int or not 1 <= max_pairs <= 1000:
            raise ValueError('tool pair limit must be between1 and1000')
        self.thread, self.member, self.sensitive, self.limit = thread_id, member_id, sensitive, max_pairs
        self.calls, self.outputs, self.counts = {}, {}, Counter()

    def add(self, row, path, number):
        item = row['payload']
        kind = item.get('type')
        if kind not in {'custom_tool_call', 'custom_tool_call_output'}:
            return False
        call_id = item.get('call_id')
        if not isinstance(call_id, str) or not call_id or len(call_id) > 256:
            raise ValueError('native tool requires bounded call identity')
        stamp = row.get('timestamp')
        try:
            timestamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')) if isinstance(stamp, str) else None
            if timestamp is None or timestamp.tzinfo is None:
                raise ValueError('timezone required')
        except ValueError:
            raise ValueError('native tool requires public timestamp') from None
        reason = None
        if kind == 'custom_tool_call':
            text, name = item.get('input'), item.get('name')
            if not isinstance(text, str) or not isinstance(name, str):
                raise ValueError('native tool call requires text input and name')
            target = self.calls
        else:
            name, target = None, self.outputs
            value = item.get('output')
            if isinstance(value, str):
                text = value
            elif isinstance(value, list) and all(isinstance(block, dict) for block in value):
                if any(block.get('type') == 'input_image' for block in value):
                    text, reason = None, 'binary'
                elif all(block.get('type') == 'input_text' and isinstance(block.get('text'), str) for block in value):
                    text = '\n'.join(block['text'] for block in value)
                else:
                    text, reason = None, 'unsupported'
            else:
                text, reason = None, 'unsupported'
        record = {'text': text, 'name': name, 'reason': reason, 'timestamp': timestamp, 'source_ref': f'{path}:{number}'}
        previous = target.get(call_id)
        if previous is not None:
            if any(previous[k] != record[k] for k in ('text', 'name', 'reason', 'timestamp')):
                raise ValueError('conflicting native tool record identity')
            self.counts['duplicate_tool_records'] += 1
        else:
            target[call_id] = record
        return True

    def validate_existing(self, events):
        # Validate observed identities before eligibility/window filtering, including
        # rewrites that would otherwise disappear behind the privacy filter.
        for event in events:
            if event.session_id != self.thread or event.metadata.get('capture') != CAPTURE:
                continue
            records = self.calls if event.kind == 'tool_call' else self.outputs
            record = records.get(event.metadata.get('native_call_id'))
            if record is None:
                continue  # Missing historical files are not deletions.
            if (record['text'] != event.content or record['timestamp'] != event.observed_at or
                record['reason'] is not None or (event.kind == 'tool_call' and record['name'] != 'exec')):
                raise ValueError('conflicting native tool record identity')

    def finish(self, *, existing_call_ids=(), backfill_limit=None):
        counts = self.counts.copy()
        pairs = []
        for call_id, call in self.calls.items():
            result = self.outputs.get(call_id)
            if result is None:
                counts['pending_tool_calls'] += 1
                continue
            if len(call['text']) > 16000:
                counts['skipped_tool_oversize'] += 1
                continue
            if call['name'] != 'exec' or not direct_local_calls(call['text']):
                counts['skipped_tool_unsupported'] += 1
                continue
            if result['reason']:
                counts['skipped_tool_' + result['reason']] += 1
                continue
            texts = [call['text'], result['text']]
            if len(texts[1]) > 48000:
                counts['skipped_tool_oversize'] += 1
                continue
            if PRIVATE_INPUT.search(call['text']) or has_sensitive_switch(call['text']) or any(self.sensitive.search(text) or PRIVATE_TEXT.search(text) for text in texts):
                counts['skipped_tool_sensitive'] += 1
                continue
            if not all(text.strip() for text in texts):
                counts['skipped_tool_unsupported'] += 1
                continue
            pairs.append((call_id, call, result))
        pairs.sort(key=lambda pair: (pair[1]['timestamp'], pair[0]))
        existing = set(existing_call_ids or ())
        if backfill_limit is not None:
            if type(backfill_limit) is not int or not 1 <= backfill_limit <= 1000:
                raise ValueError('backfill pair limit must be between1 and1000')
            candidates = [pair for pair in pairs if pair[0] not in existing]
            selected = candidates[:backfill_limit]
        else:
            selected = pairs[-self.limit:]
        events = []
        for call_id, call, result in selected:
            suffix = sha256((self.thread + ':' + call_id).encode()).hexdigest()[:24]
            ids = [f'codex-tool-call-{suffix}', f'codex-tool-result-{suffix}']
            for side, record in enumerate((call, result)):
                events.append(EvidenceEvent(ids[side], self.thread, 'codex', 'tool_call' if side == 0 else 'tool_result',
                    record['text'], record['timestamp'], {'source_ref': record['source_ref'], 'member_id': self.member,
                    'thread_id': self.thread, 'tool': 'functions.exec', 'native_call_id': call_id,
                    'paired_evidence_id': ids[1-side], 'capture': CAPTURE,
                    'public_text_sha256': sha256(record['text'].encode()).hexdigest()}))
        status = {key: counts[key] for key in ('duplicate_tool_records', 'pending_tool_calls', 'skipped_tool_unsupported',
                  'skipped_tool_binary', 'skipped_tool_sensitive', 'skipped_tool_oversize')}
        remaining = max(0, len(pairs) - len(existing) - len(selected)) if backfill_limit is not None else max(0, len(pairs)-self.limit)
        status.update({'tools_enabled': True, 'max_tool_pairs': self.limit, 'eligible_tool_pairs': len(pairs),
            'captured_tool_pairs': len(selected), 'older_tool_pairs_not_in_window': remaining,
            'unmatched_tool_results': len(self.outputs.keys() - self.calls.keys())})
        return events, status
