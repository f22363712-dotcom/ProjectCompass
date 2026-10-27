from __future__ import annotations

from collections import defaultdict
from hashlib import sha1

from .models import CandidateFact, EvidenceEvent, FactKind, FactStatus
from .candidate_extraction import source_claim_type


def derive_facts(events: list[EvidenceEvent]) -> list[CandidateFact]:
    raw: list[CandidateFact] = []
    sources = {event.id: event for event in events if event.kind != "fact_proposal"}
    for event in events:
        for index, item in enumerate(event.metadata.get("facts", [])):
            source = event
            if event.kind == "fact_proposal":
                source = sources.get(item.get("source_id"))
                quote = item.get("quote")
                start, end = item.get("source_start"), item.get("source_end")
                if source is None or not isinstance(quote, str) or not quote.strip() or not isinstance(start, int) or not isinstance(end, int) or source.content[start:end] != quote or item.get("value") != quote:
                    raise ValueError("invalid persisted proposal source span")
            kind = FactKind(item["kind"])
            value = str(item["value"])
            if "status" in item:
                value = f"{value} [{item['status']}]"
            digest = sha1(f"{event.id}:{index}".encode(), usedforsecurity=False).hexdigest()[:10]
            raw.append(CandidateFact(
                id=f"fact-{digest}", kind=kind, subject_key=item["subject_key"],
                value=value, status=FactStatus.PROPOSED, evidence_ids=(source.id,),
                author=str(event.metadata.get("member_id", source.metadata.get("member_id", source.author))), observed_at=source.observed_at, session_id=source.session_id,
                quote=item.get("quote"), source_ref=source.metadata.get("source_ref"),
                claim_type=source_claim_type(source) if event.kind == "fact_proposal" else item.get("claim_type"), reported_by=source.author, task_state=item.get("task_state"),
            ))
    by_subject: dict[str, list[CandidateFact]] = defaultdict(list)
    for fact in raw:
        by_subject[fact.subject_key].append(fact)
    result: list[CandidateFact] = []
    for fact in raw:
        values = {candidate.value for candidate in by_subject[fact.subject_key]}
        status = FactStatus.CONFLICTED if len(values) > 1 else fact.status
        result.append(CandidateFact(**{**fact.__dict__, "status": status}))
    return result
