"""Bounded public typed command completions, with no test/task interpretation."""
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from .models import EvidenceEvent
from .native_tool_trace import PRIVATE_INPUT, PRIVATE_TEXT, has_sensitive_switch

CAPTURE = 'native-public-command-completions-v1'
PUBLIC_KEYS = ('command', 'cwd', 'source', 'status', 'aggregated_output', 'exit_code', 'duration')


class CommandTraceCollector:
    def __init__(self, thread_id, member_id, project_path, sensitive, *, max_events=200):
        if type(max_events) is not int or not 1 <= max_events <= 1000:
            raise ValueError('native command limit must be between1 and1000')
        self.thread, self.member, self.project = thread_id, member_id, Path(project_path).resolve(strict=True)
        self.sensitive, self.limit = sensitive, max_events
        self.records, self.counts = {}, Counter()

    def add(self, row, path, number):
        payload = row.get('payload', {})
        if row.get('type') != 'event_msg' or not isinstance(payload, dict) or payload.get('type') != 'item_completed':
            return False
        item = payload.get('item', {})
        if not isinstance(item, dict) or item.get('type') != 'CommandExecution':
            return False
        identity = item.get('id')
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            raise ValueError('native command requires bounded identity')
        try:
            stamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('timezone required')
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValueError('native command requires public timestamp') from None
        public = {key: item.get(key) for key in PUBLIC_KEYS}
        fingerprint = sha256(json.dumps([stamp.isoformat(), public], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        record = {'public': public, 'timestamp': stamp, 'fingerprint': fingerprint, 'source_ref': f'{path}:{number}'}
        previous = self.records.get(identity)
        if previous is not None:
            if previous['fingerprint'] != fingerprint:
                old = previous['public']
                transition = (old['status'] in ('in_progress', 'running') and
                    public['status'] in ('in_progress', 'running', 'completed', 'failed') and
                    stamp >= previous['timestamp'] and
                    all(old[key] == public[key] for key in ('command', 'cwd', 'source')))
                if not transition:
                    raise ValueError('conflicting native command identity')
                self.records[identity] = record
            else:
                self.counts['duplicate_native_commands'] += 1
        else:
            self.records[identity] = record
        return True

    def validate_existing(self, events):
        for event in events:
            if event.session_id != self.thread or event.metadata.get('capture') != CAPTURE:
                continue
            record = self.records.get(event.metadata.get('native_item_id'))
            if record is not None and record['fingerprint'] != event.metadata.get('native_public_sha256'):
                raise ValueError('conflicting native command identity')

    def _project_cwd(self, raw):
        if not isinstance(raw, str):
            raise ValueError('native command requires local working directory')
        uri = urlsplit(raw)
        if uri.scheme == 'file':
            if uri.netloc or uri.query or uri.fragment:
                raise ValueError('native command requires local working directory')
            raw = unquote(uri.path)
            if re.match(r'^/[A-Za-z]:/', raw):
                raw = raw[1:]
        elif uri.scheme and not re.match(r'^[A-Za-z]:[\\/]', raw):
            raise ValueError('native command requires local working directory')
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError('native command requires absolute working directory')
        return path.resolve().is_relative_to(self.project)

    def finish(self, *, existing_native_ids=(), backfill_limit=None):
        counts, eligible = self.counts.copy(), []
        for identity, record in self.records.items():
            item = record['public']
            if not isinstance(item['status'], str) or not isinstance(item['source'], str):
                raise ValueError('invalid native command status/source')
            if item['status'] not in {'completed', 'failed'}:
                counts['pending_native_commands'] += 1
                continue
            command, output, duration = item['command'], item['aggregated_output'], item['duration']
            if (not isinstance(command, list) or not command or
                any(not isinstance(part, str) for part in command) or type(item['exit_code']) is not int or
                not isinstance(output, str) or not isinstance(duration, dict) or
                set(duration) != {'secs', 'nanos'} or any(type(v) is not int for v in duration.values()) or
                duration['secs'] < 0 or not 0 <= duration['nanos'] < 1000000000):
                raise ValueError('invalid completed native command fields')
            if not self._project_cwd(item['cwd']):
                counts['skipped_native_command_outside_project'] += 1
                continue
            if item['source'] not in {'unified_exec_startup', 'unified_exec_interaction'}:
                counts['skipped_native_command_unsupported'] += 1
                continue
            command_text = json.dumps(command, ensure_ascii=False)
            if len(command_text) > 16000 or len(output) > 48000:
                counts['skipped_native_command_oversize'] += 1
                continue
            if '\x00' in command_text or '\x00' in output or any('\x00' in part for part in command):
                counts['skipped_native_command_nontext'] += 1
                continue
            if (any(has_sensitive_switch(part) for part in command) or
                self.sensitive.search(identity) or PRIVATE_TEXT.search(identity) or
                PRIVATE_INPUT.search(command_text) or self.sensitive.search(command_text) or
                self.sensitive.search(output) or PRIVATE_TEXT.search(command_text) or PRIVATE_TEXT.search(output) or
                self.sensitive.search(item['cwd']) or PRIVATE_TEXT.search(item['cwd'])):
                counts['skipped_native_command_sensitive'] += 1
                continue
            eligible.append((identity, record))
        eligible.sort(key=lambda pair: (pair[1]['timestamp'], pair[0]))
        existing = set(existing_native_ids or ())
        if backfill_limit is not None:
            if type(backfill_limit) is not int or not 1 <= backfill_limit <= 1000:
                raise ValueError('backfill command limit must be between1 and1000')
            candidates = [pair for pair in eligible if pair[0] not in existing]
            selected = candidates[:backfill_limit]
        else:
            selected = eligible[-self.limit:]
        events = []
        for identity, record in selected:
            item = record['public']
            suffix = sha256((self.thread + ':' + identity).encode()).hexdigest()[:24]
            events.append(EvidenceEvent('codex-command-' + suffix, self.thread, 'codex', 'command_execution',
                item['aggregated_output'], record['timestamp'], {
                    'source_ref': record['source_ref'], 'member_id': self.member, 'thread_id': self.thread,
                    'native_item_id': identity, 'capture': CAPTURE, 'command': item['command'],
                    'cwd': item['cwd'], 'execution_status': item['status'], 'exit_code': item['exit_code'],
                    'duration': item['duration'], 'native_source': item['source'],
                    'native_public_sha256': record['fingerprint'],
                    'public_text_sha256': sha256(item['aggregated_output'].encode()).hexdigest()}))
        status = {key: counts[key] for key in ('duplicate_native_commands', 'pending_native_commands',
            'skipped_native_command_outside_project', 'skipped_native_command_unsupported',
            'skipped_native_command_oversize', 'skipped_native_command_nontext', 'skipped_native_command_sensitive')}
        remaining = max(0, len(eligible) - len(existing) - len(selected)) if backfill_limit is not None else max(0, len(eligible)-self.limit)
        status.update({'max_native_commands': self.limit, 'eligible_native_commands': len(eligible),
            'captured_native_commands': len(selected), 'older_native_commands_not_in_window': remaining})
        return events, status
