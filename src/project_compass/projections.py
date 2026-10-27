from __future__ import annotations

from .fact_engine import derive_facts
from .models import EvidenceEvent
from .native_codex import is_platform_control
from .source_currentness import source_states
from .candidate_extraction import source_member
from .directions import direction_projection, direction_suggestion


def build_views(events: list[EvidenceEvent], accepted_fact_ids=(), *, session_members=None, fact_reviews=()) -> dict:
    facts = derive_facts(events)
    sources = {event.id: event for event in events}

    def source_context(fact):
        source = sources.get(fact.evidence_ids[0]) if fact.evidence_ids else None
        return {'observed_at': fact.observed_at.isoformat(), 'session_id': fact.session_id,
                'direction_suggestion':direction_suggestion(fact.quote or fact.value) if fact.kind.value=='decision' else None,
                'source_kind': source.kind if source is not None else None}

    states = source_states(events, facts)
    accepted = set(accepted_fact_ids)
    personal: dict[str, dict] = {}
    raw_events = [event for event in events if event.kind not in {'fact_proposal', 'file_observation'} and not is_platform_control(event.content, kind=event.kind)]
    for event in raw_events:
        if event.kind in {'file_snapshot', 'file_unavailable', 'git_commit'}:
            owner = 'project-documents'
        else:
            owner = source_member(event, session_members=session_members)
        member = personal.setdefault(owner, {'member_id': owner, 'facts': [], 'evidence_ids': []})
        member['evidence_ids'].append(event.id)
    for fact in facts:
        member = personal.setdefault(fact.author, {"member_id": fact.author, "facts": [], "evidence_ids": []})
        member["facts"].append({"id": fact.id, "kind": fact.kind.value, "subject_key": fact.subject_key, "value": fact.value, "status": fact.status.value, "evidence_ids": list(fact.evidence_ids), "quote": fact.quote, "source_ref": fact.source_ref, "claim_type": fact.claim_type, "reported_by": fact.reported_by, "task_state": fact.task_state})
        member["evidence_ids"].extend(fact.evidence_ids)
        member['facts'][-1]['source_state'] = states[fact.id]
        member['facts'][-1].update(source_context(fact))
    for member in personal.values():
        member["evidence_ids"] = sorted(set(member["evidence_ids"]))
        member['directions']=direction_projection(facts,fact_reviews,events,member_id=member['member_id'])
    team_facts = [
        {
            "id": fact.id,
            "kind": fact.kind.value,
            "subject_key": fact.subject_key,
            "value": fact.value,
            "status": "accepted" if fact.id in accepted else fact.status.value,
            "author": fact.author,
            "evidence_ids": list(fact.evidence_ids),
            "quote": fact.quote,
            "source_ref": fact.source_ref,
            "claim_type": fact.claim_type,
            "reported_by": fact.reported_by,
            "task_state": fact.task_state,
            'source_state': states[fact.id],
            **source_context(fact),
        }
        for fact in facts
    ]
    by_subject: dict[str, list[dict]] = {}
    for fact in team_facts:
        by_subject.setdefault(fact["subject_key"], []).append(fact)
    conflicts = [
        {
            "subject_key": subject_key,
            "sides": [
                {
                    "fact_id": fact["id"],
                    "value": fact["value"],
                    "status": fact["status"],
                    "author": fact["author"],
                    "evidence_ids": fact["evidence_ids"],
                    'source_state': fact['source_state'],
                    'observed_at': fact['observed_at'],
                    'source_kind': fact['source_kind'],
                    'session_id': fact['session_id'],
                }
                for fact in subject_facts
            ],
        }
        for subject_key, subject_facts in by_subject.items()
        if len({fact["value"] for fact in subject_facts}) > 1
    ]
    team = {"facts": team_facts, "evidence_ids": sorted({event.id for event in raw_events}), "conflicts": conflicts}
    team['directions']=direction_projection(facts,fact_reviews,events)
    return {"personal": personal, "team": team, "evidence_count": len(raw_events)}
