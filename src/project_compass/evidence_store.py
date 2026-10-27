from __future__ import annotations

import json
from pathlib import Path

from .models import EvidenceEvent
from .codex_adapter import CodexAdapter


class EvidenceStore:
    def __init__(self, events: list[EvidenceEvent] | None = None) -> None:
        self._events: list[EvidenceEvent] = []
        self._ids: set[str] = set()
        for event in events or []:
            self.append(event)

    def append(self, event: EvidenceEvent) -> None:
        if event.id in self._ids:
            raise ValueError(f"duplicate evidence id: {event.id}")
        self._events.append(event)
        self._ids.add(event.id)

    def list(self) -> list[EvidenceEvent]:
        return list(self._events)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps([event.to_dict() for event in self._events], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "EvidenceStore":
        values = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(values, list):
            raise ValueError("evidence file must contain a JSON array")
        return cls([EvidenceEvent.from_dict(value) for value in values])

    @classmethod
    def load_jsonl(cls, path: str | Path) -> "EvidenceStore":
        return cls(CodexAdapter.from_jsonl(path))
