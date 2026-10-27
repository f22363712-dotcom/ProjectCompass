from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import UUID

from .models import EvidenceEvent
from .native_tool_trace import CAPTURE, ToolTraceCollector
from .native_command_trace import CAPTURE as COMMAND_CAPTURE, CommandTraceCollector
from .native_backfill import NativeBackfillProgress


SENSITIVE = re.compile(
    r"配对码|API.?KEY|Bearer\s|sk-[A-Za-z0-9]|密码|私钥|<environment_context>|<system-reminder>"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:Authorization|Cookie|Set-Cookie)\s*[:=]"
    r"|(?<![A-Z0-9_])(?:AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|AWS_SESSION_TOKEN|GITHUB_TOKEN|GH_TOKEN"
    r"|[A-Z0-9_]*(?:PASSWORD|PASSWD|TOKEN|SECRET|ACCESS_TOKEN|AUTH_TOKEN|REFRESH_TOKEN|CLIENT_SECRET|SECRET_KEY))\s*['\"]?\s*[:=]",
    re.I,
)


def _message_key(role, timestamp, content):
    return role, timestamp.isoformat(), content


def is_platform_control(content: str, *, kind: str | None = None) -> bool:
    if kind is not None and kind not in {'user_message', 'assistant_message', 'agent_message'}:
        return False
    text = content.strip()
    opening = re.match(r'<(?P<tag>external_codex_apps_[a-z_]+|codex_internal_context)(?:\s+[^<>]*)?>', text)
    if opening is None:
        return False
    closing = '</' + opening['tag'] + '>'
    # An earlier close can be followed by ordinary speech or another quotation.
    return text.endswith(closing) and text.find(closing, opening.end()) == len(text) - len(closing)


class NativeCodexIntake:
    """Experimental, explicit project-bound public-message reader, with no model calls."""

    def __init__(self, sessions_root, project_path, thread_id, member_id, *, include_tools=False, max_tool_pairs=200, backfill_batch_size=50):
        self.root = Path(sessions_root).resolve(strict=True)
        self.project = Path(project_path).resolve(strict=True)
        self.thread_id = str(UUID(thread_id))
        if self.thread_id != thread_id or not member_id.strip():
            raise ValueError("canonical thread UUID and member identity are required")
        self.member_id = member_id.strip()
        if type(backfill_batch_size) is not int or not 1 <= backfill_batch_size <= 1000:
            raise ValueError('backfill batch size must be between1 and1000')
        self.include_tools, self.max_tool_pairs = include_tools, max_tool_pairs
        self.backfill_batch_size = backfill_batch_size
        self.signature = None
        self.captured_ids = set()
        self.status = {"enabled": True, "thread_id": self.thread_id, "member_id": self.member_id}

    def sync(self, workspace, events):
        paths = sorted(self.root.rglob(f"*{self.thread_id}*.jsonl"))
        if not paths:
            raise ValueError("selected Codex thread has no local rollout files")
        if len(paths) > 32:
            raise ValueError("selected Codex thread exceeds 32 rollout files")
        signature = []
        for path in paths:
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("selected rollout resolves outside session root")
            stat = path.stat()
            if stat.st_size > 128 * 1024 * 1024:
                raise ValueError("selected rollout exceeds the 128 MiB read limit")
            signature.append((str(path), stat.st_size, stat.st_mtime_ns))
        progress = NativeBackfillProgress(workspace.state_path.parent)
        progress_state = progress.load()
        existing_tool_ids = {event.metadata.get('native_call_id') for event in events if event.metadata.get('capture') == CAPTURE}
        existing_command_ids = {event.metadata.get('native_item_id') for event in events if event.metadata.get('capture') == COMMAND_CAPTURE}
        needs_backfill = self.include_tools and progress_state.get('state') == 'pending'
        if signature == self.signature and not needs_backfill:
            linked = {item.get('source_id') for event in events if event.kind == 'fact_proposal' for item in event.metadata.get('facts', [])}
            self.status['messages_without_candidates'] = len(self.captured_ids - linked)
            return {**self.status, "updated": False, "new_messages": 0, 'new_tool_records': 0, 'new_native_commands': 0}
        known = {}
        for event in events:
            if event.session_id == self.thread_id and event.kind in {"user_message", "assistant_message"}:
                role = event.metadata.get("role", event.kind.removesuffix("_message"))
                known[_message_key(role, event.observed_at, event.content)] = event
        captured = {}
        counts = Counter()
        tools = ToolTraceCollector(self.thread_id, self.member_id, SENSITIVE, max_pairs=self.max_tool_pairs) if self.include_tools else None
        commands = CommandTraceCollector(self.thread_id, self.member_id, self.project, SENSITIVE) if self.include_tools else None
        for path in paths:
            with path.open(encoding="utf-8") as handle:
                try:
                    header = json.loads(next(handle))
                except (ValueError, StopIteration):
                    raise ValueError(f"invalid native session header: {path.name}") from None
                meta = header.get("payload", {}) if isinstance(header, dict) else {}
                if not isinstance(meta, dict) or not isinstance(header, dict) or header.get("type") != "session_meta" or meta.get("id") != self.thread_id or not isinstance(meta.get("cwd"), str) or Path(meta["cwd"]).resolve() != self.project:
                    raise ValueError(f"native session project/thread mismatch: {path.name}")
                for number, line in enumerate(handle, 2):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        if not line.endswith("\n"):
                            counts["pending_tail_lines"] += 1
                            break
                        raise ValueError(f"invalid complete native record at {path.name}:{number}") from None
                    if not isinstance(row, dict):
                        raise ValueError(f"invalid native record shape at {path.name}:{number}")
                    if row.get("type") != "response_item":
                        if commands is not None and commands.add(row, path, number):
                            continue
                        counts["skipped_other_records"] += 1
                        continue
                    item = row.get("payload", {})
                    if not isinstance(item, dict):
                        raise ValueError(f"invalid native payload shape at {path.name}:{number}")
                    if item.get("type") != "message":
                        if tools is not None and tools.add(row, path, number):
                            continue
                        counts["skipped_other_records"] += 1
                        continue
                    role = item.get("role")
                    if role not in {"user", "assistant"} or item.get("channel") not in {None, "commentary", "final"} or item.get("phase") not in {None, "commentary", "final", "final_answer"}:
                        counts["skipped_nonpublic"] += 1
                        continue
                    parts = item.get("content", [])
                    if not isinstance(parts, list) or any(not isinstance(part, dict) or not isinstance(part.get("text", ""), str) for part in parts):
                        raise ValueError(f"invalid native message content at {path.name}:{number}")
                    content = "\n".join(part.get("text", "") for part in parts if part.get("type") in {"input_text", "output_text", "text"})
                    if not content.strip():
                        continue
                    if is_platform_control(content):
                        counts["skipped_control"] += 1
                        continue
                    if SENSITIVE.search(content):
                        counts["skipped_sensitive"] += 1
                        continue
                    try:
                        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                        if timestamp.tzinfo is None:
                            raise ValueError("timezone required")
                    except (KeyError, ValueError, TypeError):
                        raise ValueError(f"native message requires timestamp at {path.name}:{number}") from None
                    key = _message_key(role, timestamp, content)
                    if key in captured:
                        counts["duplicate_messages"] += 1
                        continue
                    if key in known:
                        captured[key] = known[key]
                        continue
                    identity = sha256((self.thread_id + timestamp.isoformat() + role + content).encode()).hexdigest()[:24]
                    event_id = f"codex-native-{identity}"
                    captured[key] = EvidenceEvent(event_id, self.thread_id, self.member_id if role == "user" else "codex", role + "_message", content, timestamp,
                        {"source_ref": f"{path}:{number}", "thread_id": self.thread_id, "role": role,
                         "member_id": self.member_id, "phase": item.get("phase"), "capture": "native-public-messages-only"})
        message_events = list(captured.values())
        if tools is not None:
            tools.validate_existing(events)
            commands.validate_existing(events)
        backfill = self.include_tools and bool(existing_tool_ids or existing_command_ids or progress_state.get('state') == 'pending')
        tool_events, tool_status = tools.finish(
            existing_call_ids=existing_tool_ids, backfill_limit=self.backfill_batch_size
        ) if tools is not None and backfill else (tools.finish() if tools is not None else ([], {'tools_enabled': False}))
        command_events, command_status = commands.finish(
            existing_native_ids=existing_command_ids, backfill_limit=self.backfill_batch_size
        ) if commands is not None and backfill else (commands.finish() if commands is not None else ([], {}))
        known_by_id = {event.id: event for event in events}
        tool_events = [known_by_id.get(event.id, event) for event in tool_events]
        command_events = [known_by_id.get(event.id, event) for event in command_events]
        imported = [*message_events, *tool_events, *command_events]
        existing_ids = {event.id for event in events}
        new_messages = sum(event.id not in existing_ids for event in message_events)
        new_tools = sum(event.id not in existing_ids for event in tool_events)
        new_commands = sum(event.id not in existing_ids for event in command_events)
        # Do not advance the signature if validation/persistence fails.
        if new_messages or new_tools or new_commands or not workspace.exists():
            workspace.save(imported)
            events[:] = workspace.load()
        backfill_count = new_tools + new_commands
        remaining_tools = tool_status.get('older_tool_pairs_not_in_window', 0)
        remaining_commands = command_status.get('older_native_commands_not_in_window', 0)
        progress_value = progress.save(
            state='pending' if remaining_tools or remaining_commands else ('complete' if backfill or backfill_count else 'not_started'),
            batch_count=backfill_count,
            remaining_tool_pairs=remaining_tools,
            remaining_commands=remaining_commands,
            source_signature=signature,
        )
        self.signature = signature
        self.captured_ids = {event.id for event in message_events}
        linked_sources = {item.get("source_id") for event in events if event.kind == "fact_proposal" for item in event.metadata.get("facts", [])}
        self.status = {"enabled": True, "thread_id": self.thread_id, "member_id": self.member_id,
                       "files": len(paths), "captured_messages": len(message_events), "new_messages": new_messages,
                       'new_tool_records': new_tools, 'stored_tool_records': sum(event.session_id == self.thread_id and event.metadata.get('capture') == CAPTURE for event in events),
                       'new_native_commands': new_commands, 'stored_native_commands': sum(event.session_id == self.thread_id and event.metadata.get('capture') == COMMAND_CAPTURE for event in events),
                       **tool_status, **command_status,
                       "messages_without_candidates": sum(event.id not in linked_sources for event in message_events),
                       "skipped_sensitive": counts["skipped_sensitive"], "skipped_nonpublic": counts["skipped_nonpublic"],
                       "skipped_control": counts["skipped_control"],
                       "skipped_other_records": counts["skipped_other_records"], "duplicate_messages": counts["duplicate_messages"],
                       "pending_tail_lines": counts["pending_tail_lines"], "updated": bool(new_messages or new_tools or new_commands),
                       "scope": 'public messages, bounded exec text pairs and typed command completions; filtered records omitted' if self.include_tools else "public messages only; sensitive/environment messages and tool payloads excluded",
                       "last_read_at": datetime.now(timezone.utc).isoformat(),
                       "backfill": {**progress_value, "batch_size": self.backfill_batch_size,
                                    "enabled": bool(self.include_tools),
                                    "raw_only": True}}
        return dict(self.status)
