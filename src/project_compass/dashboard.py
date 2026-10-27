from __future__ import annotations

from pathlib import Path

from .evidence_linking import build_evidence_index
from .fact_engine import derive_facts
from .models import EvidenceEvent, EvidenceRelation
from .projections import build_views


def build_dashboard_state(
    events: list[EvidenceEvent],
    project_path: str | None = None,
    confirmed_relations: list[EvidenceRelation] | tuple[EvidenceRelation, ...] = (),
    review_history: list[dict] | tuple[dict, ...] = (),
    fact_reviews: list[dict] | tuple[dict, ...] = (),
    *, session_members=None,
) -> dict:
    """Build the read-only state consumed by the personal/team dashboard."""
    facts = derive_facts(events)
    accepted_by_subject: dict[str, str] = {}
    for item in fact_reviews:
        if item.get("action") == "accept_fact" and item.get("fact_id"):
            accepted_by_subject[str(item.get("subject_key", item["fact_id"]))] = item["fact_id"]
    accepted_fact_ids = list(accepted_by_subject.values())
    views = build_views(events, accepted_fact_ids=accepted_fact_ids, session_members=session_members, fact_reviews=fact_reviews)
    index = build_evidence_index(events, facts).with_confirmed_relations(confirmed_relations)
    return {
        "project": {
            "name": Path(project_path).name if project_path else "Project Compass",
            "path": project_path,
            "evidence_count": views["evidence_count"],
            "fact_count": len(facts),
        },
        "personal": views["personal"],
        "team": views["team"],
        "review_history": list(review_history),
        "fact_reviews": list(fact_reviews),
        **index.to_dict(),
    }
