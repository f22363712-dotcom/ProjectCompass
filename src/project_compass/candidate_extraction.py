from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from hashlib import sha256
import json

from .models import EvidenceEvent, FactKind
from .native_codex import is_platform_control
from .extraction_progress import source_key


def source_claim_type(source: EvidenceEvent) -> str:
    if source.metadata.get('actor_type') == 'agent':
        return 'assistant_report'
    if source.kind in {'tool_call', 'tool_result', 'test_result', 'command_execution', 'file_change'}:
        return 'execution_record'
    if source.kind == 'user_message':
        text = source.content.strip()
        opening, closing = '<send_user_message_question_reply>', '</send_user_message_question_reply>'
        if text.startswith(opening) and text.endswith(closing):
            try:
                records = json.loads(text[len(opening):-len(closing)])
                if isinstance(records, list) and records and all(isinstance(row, dict)
                        and isinstance(row.get('question'), str) and isinstance(row.get('answer'), str) for row in records):
                    return 'conversation_exchange'
            except ValueError:
                pass
    role = source.metadata.get("role")
    return "assistant_report" if role == "assistant" or source.kind == "assistant_message" else "user_statement" if role == "user" or source.kind == "user_message" else "document_statement"


def source_member(source, fallback=None, session_members=None):
    return str(source.metadata.get('member_id') or (session_members or {}).get(source.session_id) or fallback or source.author)


def validate_candidates(events: list[EvidenceEvent], payload: dict, member_id: str | None = None, *, source_windows=None, session_members=None) -> list[EvidenceEvent]:
    """Validate extractive model output without editing or accepting its sources."""
    sources = {event.id: event for event in events if event.kind != "fact_proposal" and not is_platform_control(event.content, kind=event.kind)}
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("extraction must contain candidates")
    if len(payload["candidates"]) > 24:
        raise ValueError("too many extraction candidates")
    proposals = {}
    for item in payload["candidates"]:
        if not isinstance(item, dict):
            raise ValueError("candidate must be an object")
        source = sources.get(item.get("source_id"))
        quote = item.get("quote")
        topic = item.get("subject_key")
        state = item.get("task_state", "unknown")
        if source is None or not isinstance(quote, str) or not quote.strip() or len(quote) > 1600 or quote not in source.content:
            raise ValueError("candidate requires an exact quote in an existing source")
        kind = FactKind(item.get("kind"))
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 160:
            raise ValueError("candidate requires a bounded subject key")
        if state not in {"pending", "active", "blocked", "reported_complete", "unknown"}:
            raise ValueError("invalid reported task state")
        claim_type = source_claim_type(source)
        low, high = source_windows.get(source.id, (-1, -1)) if source_windows is not None else (0, len(source.content))
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= len(source.content) or quote not in source.content[low:high]:
            raise ValueError('candidate quote is outside the selected source window')
        start = low + source.content[low:high].index(quote)
        fact = {"kind": kind.value, "subject_key": f"{kind.value}:{topic}", "value": quote,
                "quote": quote, "source_id": source.id, "source_start": start, "source_end": start + len(quote),
                "claim_type": claim_type, "task_state": state}
        identity = sha256(json.dumps(fact, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        proposals[identity] = EvidenceEvent(
            f"proposal-{identity}", source.session_id, source.author, "fact_proposal", quote, source.observed_at,
            {"facts": [fact], "member_id": source_member(source, member_id, session_members) if source.kind.endswith("_message") else "project-documents",
             "source_ref": source.metadata.get("source_ref"), "derivation": "validated-extractive-v2"},
        )
    return list(proposals.values())


def current_file_snapshots(events: list[EvidenceEvent]) -> list[EvidenceEvent]:
    sources = {event.id: event for event in events if event.kind == "file_snapshot"}
    latest_files = {}
    for event in events:
        if event.kind == "file_snapshot":
            key = event.metadata.get("source_ref", event.id)
            latest_files[key] = event
        elif event.kind == "file_observation":
            source = sources.get(event.metadata.get("source_id"))
            if source is not None:
                latest_files[source.metadata.get("source_ref", source.id)] = source
        elif event.kind == 'file_unavailable':
            latest_files.pop(event.metadata.get('source_ref'), None)
    return list(latest_files.values())


def extraction_sources(events):
    messages = [event for event in events if event.kind in {'assistant_message', 'user_message'}
                and not is_platform_control(event.content, kind=event.kind)]
    return [event for event in [*current_file_snapshots(events), *messages] if event.content.strip()]


def coverage_summary(events, cursors):
    sources = {event.id: event for event in extraction_sources(events)}
    for event in sources.values():
        end = cursors.get(source_key(event), 0)
        if type(end) is not int or not 0 <= end <= len(event.content):
            raise ValueError('extraction cursor exceeds its immutable source')
    remaining = [max(0, len(event.content.rstrip()) - cursors.get(source_key(event), 0)) for event in sources.values()]
    return {'eligible_sources': len(sources), 'pending_sources': sum(n > 0 for n in remaining),
            'pending_chars': sum(remaining)}


def focused_extraction_sources(events, member_id=None, *, session_members=None):
    if member_id is None:
        return events
    if not isinstance(member_id, str) or not member_id.strip() or len(member_id) > 160:
        raise ValueError('a bounded known member identity is required')
    sources = [event for event in extraction_sources(events)
               if event.kind in {'user_message', 'assistant_message'}
               and source_member(event, session_members=session_members) == member_id]
    if not sources:
        raise ValueError('member has no eligible public conversation')
    return sources


def select_batch(events: list[EvidenceEvent], max_chars: int = 18000, *, cursors=None, prefer_user=False) -> list[EvidenceEvent]:
    """Prefer current context, recent reviews and messages, with explicit prefix bounds."""
    if max_chars < 1:
        raise ValueError("batch limit must be positive")
    cursors = cursors or {}
    coverage_summary(events, cursors)
    messages = [event for event in events if event.kind in {"assistant_message", "user_message"}
                and not is_platform_control(event.content, kind=event.kind) and cursors.get(source_key(event), 0) < len(event.content.rstrip())]

    def priority(event):
        path = str(event.metadata.get("source_ref", "")).replace("\\", "/").lower()
        rank = 0 if "/reviews/" in path else 1 if path.endswith("context.md") else 2 if "current-behavior" in path else 3 if path.endswith("readme.md") else 4 if "backlog" in path else 5
        modified = event.metadata.get("source_modified_at")
        timestamp = datetime.fromisoformat(modified).timestamp() if modified else event.observed_at.timestamp()
        return rank, -timestamp, event.id

    result = []
    remaining = max_chars
    documents = sorted([event for event in current_file_snapshots(events)
                        if cursors.get(source_key(event), 0) < len(event.content.rstrip())], key=priority)
    # One latest review comes first; remaining reviews follow goal/current context.
    reviews = [event for event in documents if "/reviews/" in str(event.metadata.get("source_ref", "")).replace("\\", "/")]
    documents = reviews[:1] + [event for event in documents if event not in reviews] + reviews[1:]
    documents.sort(key=lambda event: cursors.get(source_key(event), 0) > 0)
    messages = sorted(messages, key=lambda event: event.observed_at, reverse=True)
    messages.sort(key=lambda event: (cursors.get(source_key(event), 0) > 0,
        prefer_user and source_claim_type(event) not in {'user_statement', 'conversation_exchange'}))
    groups = [(documents, max_chars * 2 // 3 if messages else max_chars), (messages, max_chars)]
    for group, budget in groups:
        for event in group:
            if remaining <= 0 or budget <= 0:
                break
            start = cursors.get(source_key(event), 0)
            if start >= len(event.content.rstrip()):
                continue
            end = min(len(event.content), start + min(3000, remaining, budget))
            text = event.content[start:end]
            if not text.strip():
                while start < len(event.content) and event.content[start].isspace():
                    start += 1
                end = min(len(event.content), start + min(3000, remaining, budget))
                text = event.content[start:end]
            if text.strip():
                result.append(replace(event, content=text, metadata={**event.metadata,
                    '_extraction_window_start': start, '_extraction_window_end': end}))
                remaining -= len(text)
                budget -= len(text)
    return result
