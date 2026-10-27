"""Deterministic document-version correspondence, independent of owner truth."""
from .candidate_extraction import current_file_snapshots
from .native_codex import is_platform_control


def source_states(events, facts):
    sources = {event.id: event for event in events}
    current = {event.metadata.get('source_ref', event.id): event for event in current_file_snapshots(events)}
    unavailable = {}
    for event in events:
        if event.kind == 'file_unavailable':
            unavailable[event.metadata.get('source_ref')] = event.id
        elif event.kind in {'file_snapshot', 'file_observation'}:
            unavailable.pop(event.metadata.get('source_ref'), None)
    result = {}
    for fact in facts:
        status, replacements, observations = 'recorded', [], []
        for evidence_id in fact.evidence_ids:
            source = sources.get(evidence_id)
            if source is None:
                status = 'unavailable'
            elif is_platform_control(source.content, kind=source.kind):
                status = 'unavailable'
            elif source.kind == 'file_snapshot':
                ref = source.metadata.get('source_ref', source.id)
                latest = current.get(ref)
                if latest is None:
                    status = 'unavailable'
                    if ref in unavailable:
                        observations.append(unavailable[ref])
                elif latest.id != source.id:
                    if status != 'unavailable':
                        status = 'superseded'
                    replacements.append(latest.id)
                elif status == 'recorded':
                    status = 'current'
        result[fact.id] = {'status': status, 'replacement_ids': sorted(set(replacements)),
                           'observation_ids': sorted(set(observations))}
    return result


def needs_source_review(state):
    return state['status'] in {'superseded', 'unavailable'}
