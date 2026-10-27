from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import json
from typing import Protocol
from .native_codex import is_platform_control
from .native_command_trace import CAPTURE as NATIVE_COMMAND_CAPTURE

from .models import (
    CandidateFact,
    EvidenceCatalogEntry,
    EvidenceEvent,
    EvidenceRelation,
    EvidenceRelationMethod,
    EvidenceRelationStatus,
    FactKind,
)


@dataclass(frozen=True)
class RelationSuggestion:
    method: EvidenceRelationMethod
    score: float | None
    basis: str


class RelationProvider(Protocol):
    def suggest(self, fact: CandidateFact, evidence: EvidenceEvent) -> RelationSuggestion | None:
        """Return a bounded suggestion for an eligible fact/evidence pair."""


class DeterministicLinkProvider:
    """Offline, auditable relation suggestions used by default."""

    def suggest(self, fact: CandidateFact, evidence: EvidenceEvent) -> RelationSuggestion | None:
        explicit_ids = _explicit_ids(evidence)
        if fact.id in explicit_ids or evidence.id in fact.evidence_ids:
            return RelationSuggestion(EvidenceRelationMethod.EXPLICIT, 1.0, "explicit-reference")

        value = fact.value
        if value and value.casefold() in evidence.content.casefold():
            return RelationSuggestion(EvidenceRelationMethod.DETERMINISTIC, 0.9, "exact-text-match")

        if evidence.kind in {"test_result", "tool_result"} and fact.kind is FactKind.TASK:
            if evidence.author == fact.author and evidence.session_id == fact.session_id:
                return RelationSuggestion(EvidenceRelationMethod.DETERMINISTIC, 0.45, "same-session-result")
        return None


@dataclass(frozen=True)
class EvidenceIndex:
    catalog: tuple[EvidenceCatalogEntry, ...]
    candidate_relations: tuple[EvidenceRelation, ...]
    confirmed_relations: tuple[EvidenceRelation, ...] = ()

    @property
    def catalog_by_id(self) -> dict[str, EvidenceCatalogEntry]:
        return {entry.id: entry for entry in self.catalog}

    def with_confirmed(self, relation: EvidenceRelation) -> "EvidenceIndex":
        if relation.status is not EvidenceRelationStatus.CONFIRMED:
            raise ValueError("confirmed index entries must have confirmed status")
        candidate_id = relation.id.removesuffix(":confirmed")
        candidates = {candidate.id: candidate for candidate in self.candidate_relations}
        if candidate_id not in candidates:
            raise ValueError("confirmed relation must be copied from a candidate in this index")
        candidate = candidates[candidate_id]
        if relation.id != candidate.id + ":confirmed":
            raise ValueError("confirmed relation id must be derived from the candidate id")
        if relation.method is not EvidenceRelationMethod.HUMAN or not relation.reviewed_by:
            raise ValueError("confirmed relation must include a human reviewer")
        if (
            relation.fact_id != candidate.fact_id
            or relation.evidence_id != candidate.evidence_id
            or relation.relation != candidate.relation
            or relation.score != candidate.score
            or relation.basis != candidate.basis
        ):
            raise ValueError("confirmed relation must preserve the candidate relation fields")
        if any(existing.id == relation.id for existing in self.confirmed_relations):
            raise ValueError("confirmed relation already exists")
        catalog = tuple(
            replace(
                entry,
                confirmed_fact_ids=tuple(sorted(set(entry.confirmed_fact_ids) | ({relation.fact_id} if entry.id == relation.evidence_id else set()))),
            )
            for entry in self.catalog
        )
        return EvidenceIndex(catalog, self.candidate_relations, self.confirmed_relations + (relation,))

    def with_confirmed_relations(self, relations: list[EvidenceRelation] | tuple[EvidenceRelation, ...]) -> "EvidenceIndex":
        updated = self
        for relation in relations:
            if any(existing.id == relation.id for existing in updated.confirmed_relations):
                continue
            try:
                updated = updated.with_confirmed(relation)
            except ValueError:
                # A rescan can leave a historical confirmation without its old candidate.
                # Keep the audit record, but do not project stale state into the current board.
                continue
        return updated

    def to_dict(self) -> dict[str, list[dict]]:
        return {
            "evidence": [entry.to_dict() for entry in self.catalog],
            "candidate_relations": [relation.to_dict() for relation in self.candidate_relations],
            "confirmed_relations": [relation.to_dict() for relation in self.confirmed_relations],
        }


def _classification(event: EvidenceEvent) -> str:
    if event.metadata.get("facts"):
        return "fact_source"
    if event.kind == "test_result":
        return "test_result"
    if event.kind in {"tool_result", "tool_call", "command_execution"}:
        return "execution_result"
    if event.kind == "git_commit":
        return "change"
    return "unclassified"


def _preview(content: str, limit: int = 240) -> str:
    compact = " ".join(content.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def command_context(event):
    if event.kind not in {'command_execution', 'tool_call', 'tool_result', 'test_result'}:
        return {}
    command = event.metadata.get('command')
    if isinstance(command, str):
        text = command
    elif isinstance(command, list) and command and all(isinstance(part, str) for part in command):
        text = json.dumps(command, ensure_ascii=False)
    else:
        return {}
    if not text.strip():
        return {}
    if len(text) > 1600:
        text = text[:800] + ' … ' + text[-797:]
    meta = event.metadata
    agent = meta.get('actor_type') == 'agent' or meta.get('origin') == 'agent_submission' or meta.get('capture') == 'agent-http-v1'
    origin = 'agent_report' if agent else 'native_trace' if event.kind == 'command_execution' and meta.get('capture') == NATIVE_COMMAND_CAPTURE else 'reported'
    return {'command_preview': text, 'command_origin': origin,
            'exit_code': meta.get('exit_code') if type(meta.get('exit_code')) is int else None}


def _explicit_ids(event: EvidenceEvent) -> set[str]:
    values: set[str] = set()
    for key in ("supports_fact_ids", "fact_refs"):
        raw = event.metadata.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, (list, tuple, set)):
            values.update(str(item) for item in raw)
    return values


def _eligible_pairs(events: list[EvidenceEvent], facts: list[CandidateFact]) -> list[tuple[CandidateFact, EvidenceEvent]]:
    pairs: list[tuple[CandidateFact, EvidenceEvent]] = []
    if not events or not facts:
        return pairs
    events_by_id = {event.id: event for event in events}
    sessions = [fact.session_id or next(
            (events_by_id[source_id].session_id for source_id in fact.evidence_ids if source_id in events_by_id),
            None,
        ) for fact in facts]
    task_counts = Counter((fact.author, session) for fact, session in zip(facts, sessions) if fact.kind is FactKind.TASK)
    # Per-read preparation preserves input order and duplicate entries. No state
    # survives this call, so changed source text/metadata cannot reuse old matches.
    prepared = [(event, _explicit_ids(event), event.content.casefold()) for event in events]
    for fact, fact_session in zip(facts, sessions):
        needle = fact.value.casefold() if fact.value else ''
        matching_fact = replace(fact, session_id=fact_session) if fact.session_id is None else fact
        for event, explicit_ids, content in prepared:
            if event.id in fact.evidence_ids:
                pairs.append((matching_fact, event))
                continue
            if fact.id in explicit_ids:
                pairs.append((matching_fact, event))
                continue
            if event.kind in {"test_result", "tool_result"} and fact.kind is FactKind.TASK:
                same_author = event.author == fact.author
                same_session = event.session_id == fact_session
                if same_author and same_session and task_counts[event.author, event.session_id] == 1:
                    pairs.append((matching_fact, event))
                elif needle and needle in content:
                    pairs.append((matching_fact, event))
                continue
            if needle and needle in content:
                pairs.append((matching_fact, event))
    return pairs


def build_evidence_index(
    events: list[EvidenceEvent],
    facts: list[CandidateFact] | None = None,
    provider: RelationProvider | None = None,
) -> EvidenceIndex:
    from .fact_engine import derive_facts

    facts = list(facts) if facts is not None else derive_facts(events)
    events = [event for event in events if event.kind not in {"fact_proposal", "file_observation"} and not is_platform_control(event.content, kind=event.kind)]
    deterministic = DeterministicLinkProvider()
    selected_provider = provider or deterministic
    relations: list[EvidenceRelation] = []
    relation_keys: set[tuple[str, str]] = set()
    for fact, event in _eligible_pairs(events, facts):
        fallback = deterministic.suggest(fact, event)
        if fallback is None:
            continue
        suggestion = selected_provider.suggest(fact, event) or fallback
        key = (fact.id, event.id)
        if key in relation_keys:
            continue
        relation_keys.add(key)
        relations.append(EvidenceRelation(
            id=f"relation-{fact.id}-{event.id}",
            fact_id=fact.id,
            evidence_id=event.id,
            relation="supports",
            status=EvidenceRelationStatus.CANDIDATE,
            method=suggestion.method,
            score=suggestion.score,
            basis=suggestion.basis,
        ))

    candidate_links: dict[str, list[str]] = {event.id: [] for event in events}
    for relation in relations:
        candidate_links.setdefault(relation.evidence_id, []).append(relation.fact_id)
    catalog = tuple(
        EvidenceCatalogEntry(
            id=event.id,
            kind=event.kind,
            author=event.author,
            observed_at=event.observed_at,
            source_ref=event.metadata.get("source_ref"),
            content_preview=_preview((command.get('command_preview', '') + '\n' + event.content) if event.kind == 'command_execution' else event.content),
            candidate_fact_ids=tuple(sorted(set(candidate_links.get(event.id, [])))),
            confirmed_fact_ids=(),
            classification=_classification(event),
            **command,
        )
        for event, command in ((event, command_context(event)) for event in events)
    )
    return EvidenceIndex(catalog, tuple(relations))


def confirm_relation(relation: EvidenceRelation, reviewer: str) -> EvidenceRelation:
    if relation.status is not EvidenceRelationStatus.CANDIDATE:
        raise ValueError("only candidate relations can be confirmed")
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    return EvidenceRelation(
        id=relation.id + ":confirmed",
        fact_id=relation.fact_id,
        evidence_id=relation.evidence_id,
        relation=relation.relation,
        status=EvidenceRelationStatus.CONFIRMED,
        method=EvidenceRelationMethod.HUMAN,
        score=relation.score,
        basis=relation.basis,
        reviewed_by=reviewer,
    )
