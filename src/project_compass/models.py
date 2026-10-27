from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class FactKind(str, Enum):
    GOAL = "goal"
    TASK = "task"
    DECISION = "decision"
    ASSUMPTION = "assumption"


class FactStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    CONFLICTED = "conflicted"


class EvidenceRelationStatus(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"


class EvidenceRelationMethod(str, Enum):
    EXPLICIT = "explicit"
    DETERMINISTIC = "deterministic"
    JEV = "jev"
    HUMAN = "human"


@dataclass(frozen=True)
class EvidenceEvent:
    id: str
    session_id: str
    author: str
    kind: str
    content: str
    observed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["observed_at"] = self.observed_at.isoformat()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceEvent":
        return cls(
            id=value["id"],
            session_id=value["session_id"],
            author=value["author"],
            kind=value["kind"],
            content=value["content"],
            observed_at=datetime.fromisoformat(value["observed_at"]),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class CandidateFact:
    id: str
    kind: FactKind
    subject_key: str
    value: str
    status: FactStatus
    evidence_ids: tuple[str, ...]
    author: str
    observed_at: datetime
    session_id: str | None = None
    quote: str | None = None
    source_ref: str | None = None
    claim_type: str | None = None
    reported_by: str | None = None
    task_state: str | None = None


@dataclass(frozen=True)
class EvidenceCatalogEntry:
    id: str
    kind: str
    author: str
    observed_at: datetime
    source_ref: str | None
    content_preview: str
    candidate_fact_ids: tuple[str, ...] = ()
    confirmed_fact_ids: tuple[str, ...] = ()
    classification: str = "unclassified"
    command_preview: str | None = None
    command_origin: str | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "author": self.author,
            "observed_at": self.observed_at.isoformat(),
            "source_ref": self.source_ref,
            "content_preview": self.content_preview,
            "candidate_fact_ids": list(self.candidate_fact_ids),
            "confirmed_fact_ids": list(self.confirmed_fact_ids),
            "classification": self.classification,
            **({'command_preview': self.command_preview, 'command_origin': self.command_origin,
                'exit_code': self.exit_code} if self.command_preview is not None else {}),
        }


@dataclass(frozen=True)
class EvidenceRelation:
    id: str
    fact_id: str
    evidence_id: str
    relation: str
    status: EvidenceRelationStatus
    method: EvidenceRelationMethod
    score: float | None
    basis: str
    reviewed_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fact_id": self.fact_id,
            "evidence_id": self.evidence_id,
            "relation": self.relation,
            "status": self.status.value,
            "method": self.method.value,
            "score": self.score,
            "basis": self.basis,
            "reviewed_by": self.reviewed_by,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceRelation":
        return cls(
            id=value["id"],
            fact_id=value["fact_id"],
            evidence_id=value["evidence_id"],
            relation=value["relation"],
            status=EvidenceRelationStatus(value["status"]),
            method=EvidenceRelationMethod(value["method"]),
            score=value.get("score"),
            basis=value["basis"],
            reviewed_by=value.get("reviewed_by"),
        )

