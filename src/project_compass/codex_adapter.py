from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import EvidenceEvent


SUPPORTED_TYPES = {"assistant_message", "user_message", "tool_call", "tool_result", "test_result", "file_change"}


class CodexAdapter:
    @staticmethod
    def from_jsonl(path: str | Path) -> list[EvidenceEvent]:
        events: list[EvidenceEvent] = []
        for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid Codex JSONL at line {line_number}: {exc.msg}") from exc
            events.append(CodexAdapter.normalize(payload, line_number))
        return events

    @staticmethod
    def normalize(payload: dict[str, Any], line_number: int = 0) -> EvidenceEvent:
        if not isinstance(payload, dict):
            raise ValueError(f"Codex event at line {line_number} must be an object")
        kind = payload.get("type")
        if kind not in SUPPORTED_TYPES:
            raise ValueError(f"unsupported Codex event type at line {line_number}: {kind}")
        event_id = payload.get("id") or hashlib.sha1(json.dumps(payload, sort_keys=True).encode(), usedforsecurity=False).hexdigest()[:16]
        metadata = dict(payload.get("metadata", {}))
        for key in ("tool", "command", "exit_code", "files"):
            if key in payload:
                metadata[key] = payload[key]
        timestamp = payload.get("timestamp") or payload.get("observed_at")
        if timestamp is None:
            observed_at = datetime.now(timezone.utc)
            metadata["inferred_timestamp"] = True
        else:
            observed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return EvidenceEvent(
            id=str(event_id),
            session_id=str(payload.get("session_id", "codex-unknown-session")),
            author=str(payload.get("author", "codex")),
            kind=kind,
            content=str(payload.get("content", "")),
            observed_at=observed_at,
            metadata=metadata,
        )
