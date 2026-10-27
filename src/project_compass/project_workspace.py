from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_store import EvidenceStore
from .models import EvidenceEvent, EvidenceRelation


class ProjectWorkspace:
    """Local registry for projects scanned by ProjectCompass."""

    def __init__(self, workspace_root: str | Path, project_path: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.project_path = Path(project_path).expanduser().resolve(strict=True)
        if not self.project_path.is_dir():
            raise NotADirectoryError(f"project path is not a directory: {self.project_path}")
        self.project_id = hashlib.sha1(str(self.project_path).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]

    @property
    def state_path(self) -> Path:
        return self.workspace_root / "projects" / self.project_id / "state.json"

    @property
    def registry_path(self) -> Path:
        return self.workspace_root / "registry.json"

    def exists(self) -> bool:
        return self.state_path.is_file()

    def save(self, events: list[EvidenceEvent], metadata: dict[str, Any] | None = None, *, complete_scan: bool = False) -> Path:
        saved_at = datetime.now(timezone.utc).isoformat()
        incoming = EvidenceStore(events).list()
        previous = self._read_state() if self.exists() else None
        merged = EvidenceStore(self.load() if previous is not None else [])
        known = {event.id: event for event in merged.list()}
        current_files = {}
        for event in merged.list():
            if event.kind == "file_snapshot":
                current_files[event.metadata.get("source_ref")] = event.id
            elif event.kind == "file_observation":
                current_files[event.metadata.get("source_ref")] = event.metadata.get("source_id")
            elif event.kind == 'file_unavailable':
                current_files[event.metadata.get('source_ref')] = None
        for event in incoming:
            existing = known.get(event.id)
            if existing is not None:
                if (existing.content, existing.kind, existing.author, existing.session_id) != (event.content, event.kind, event.author, event.session_id):
                    raise ValueError(f"evidence id already exists with different content: {event.id}")
                source_ref = event.metadata.get("source_ref")
                if event.kind == "file_snapshot" and current_files.get(source_ref) != event.id:
                    digest = hashlib.sha256(f"{saved_at}:{source_ref}:{event.id}".encode()).hexdigest()[:24]
                    merged.append(EvidenceEvent(f"observation-{digest}", "project-scan", "scanner", "file_observation", "",
                        datetime.fromisoformat(saved_at), {"source_ref": source_ref, "source_id": event.id}))
                    current_files[source_ref] = event.id
                continue
            merged.append(event)
            known[event.id] = event
            if event.kind == "file_snapshot":
                current_files[event.metadata.get("source_ref")] = event.id
        if complete_scan:
            present = {event.metadata.get('source_ref') for event in incoming if event.kind == 'file_snapshot'}
            for source_ref, source_id in current_files.items():
                if source_id and source_ref not in present:
                    digest = hashlib.sha256(f'{saved_at}:{source_ref}:unavailable'.encode()).hexdigest()[:24]
                    merged.append(EvidenceEvent(f'unavailable-{digest}', 'project-scan', 'scanner', 'file_unavailable',
                        f'File not available in the latest complete scan: {source_ref}', datetime.fromisoformat(saved_at),
                        {'source_ref': source_ref, 'source_id': source_id, 'reason': 'not_present_in_complete_scan'}))
        record = {
            "schema_version": 1,
            "project_id": self.project_id,
            "project_path": str(self.project_path),
            "saved_at": saved_at,
            "metadata": dict(metadata or {}),
            "events": [event.to_dict() for event in merged.list()],
        }
        if previous is not None:
            record["confirmed_relations"] = list(previous.get("confirmed_relations", []))
            record["review_history"] = list(previous.get("review_history", []))
            record["fact_reviews"] = list(previous.get("fact_reviews", []))
        # The registry is a discovery index, not the canonical evidence ledger.
        # Validate and atomically publish it before the canonical commit point.
        # A broken/unwritable registry must not leave live and persisted truth apart.
        registry = self._read_registry()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        registry[self.project_id] = {
            "project_id": self.project_id,
            "project_path": str(self.project_path),
            "project_name": self.project_path.name or str(self.project_path),
            "state_path": str(self.state_path),
            "saved_at": saved_at,
        }
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        registry_temporary = self.registry_path.with_suffix('.json.tmp')
        registry_temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf8')
        registry_temporary.replace(self.registry_path)
        temporary.replace(self.state_path)
        return self.state_path

    def load(self) -> list[EvidenceEvent]:
        if not self.exists():
            raise FileNotFoundError(
                f"no saved ProjectCompass state for {self.project_path}; run --scan first"
            )
        record = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("events"), list):
            raise ValueError(f"invalid ProjectCompass state: {self.state_path}")
        if record.get("project_id") != self.project_id:
            raise ValueError(f"state belongs to a different project: {self.state_path}")
        return EvidenceStore([EvidenceEvent.from_dict(value) for value in record["events"]]).list()

    def metadata(self) -> dict[str, Any]:
        if not self.exists():
            raise FileNotFoundError(
                f"no saved ProjectCompass state for {self.project_path}; run --scan first"
            )
        record = json.loads(self.state_path.read_text(encoding="utf-8"))
        return {
            "project_id": record.get("project_id"),
            "project_path": record.get("project_path"),
            "project_name": self.project_path.name or str(self.project_path),
            "saved_at": record.get("saved_at"),
        }

    def save_review_state(
        self,
        confirmed_relations: list[EvidenceRelation],
        review_history: list[dict[str, Any]],
        fact_reviews: list[dict[str, Any]] | None = None,
    ) -> Path:
        record = self._read_state()
        record["confirmed_relations"] = [relation.to_dict() for relation in confirmed_relations]
        record["review_history"] = [dict(item) for item in review_history]
        record["fact_reviews"] = [dict(item) for item in (fact_reviews or [])]
        self._write_state(record)
        return self.state_path

    def load_review_state(self) -> tuple[list[EvidenceRelation], list[dict[str, Any]], list[dict[str, Any]]]:
        record = self._read_state()
        raw_relations = record.get("confirmed_relations", [])
        raw_history = record.get("review_history", [])
        raw_fact_reviews = record.get("fact_reviews", [])
        if not isinstance(raw_relations, list) or not isinstance(raw_history, list) or not isinstance(raw_fact_reviews, list):
            raise ValueError(f"invalid ProjectCompass review state: {self.state_path}")
        return (
            [EvidenceRelation.from_dict(value) for value in raw_relations],
            [dict(item) for item in raw_history],
            [dict(item) for item in raw_fact_reviews],
        )

    def _read_state(self) -> dict[str, Any]:
        if not self.exists():
            raise FileNotFoundError(
                f"no saved ProjectCompass state for {self.project_path}; run --scan first"
            )
        record = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("events"), list):
            raise ValueError(f"invalid ProjectCompass state: {self.state_path}")
        if record.get("project_id") != self.project_id:
            raise ValueError(f"state belongs to a different project: {self.state_path}")
        return record

    def _write_state(self, record: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {}
        value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid ProjectCompass registry: {self.registry_path}")
        return value
