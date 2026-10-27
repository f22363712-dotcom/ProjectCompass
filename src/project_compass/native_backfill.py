from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class NativeBackfillProgress:
    """Small restart-safe receipt for raw native history backfill."""

    def __init__(self, directory: str | Path):
        self.path = Path(directory) / "native-backfill.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": 1, "batches_completed": 0, "state": "not_started"}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError(f"invalid native backfill progress: {self.path}")
        return value

    def save(self, *, state: str, batch_count: int, remaining_tool_pairs: int,
             remaining_commands: int, source_signature: list[tuple[str, int, int]] | None = None) -> dict[str, Any]:
        if state not in {"not_started", "pending", "complete"}:
            raise ValueError("invalid native backfill state")
        previous = self.load()
        value = {
            "schema_version": 1,
            "state": state,
            "batches_completed": int(previous.get("batches_completed", 0)) + (1 if batch_count else 0),
            "last_batch_count": int(batch_count),
            "remaining_tool_pairs": int(max(0, remaining_tool_pairs)),
            "remaining_commands": int(max(0, remaining_commands)),
            "last_batch_at": datetime.now(timezone.utc).isoformat() if batch_count else previous.get("last_batch_at"),
            "source_signature": source_signature or previous.get("source_signature", []),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return value
