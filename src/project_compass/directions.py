"""Explicit direction proposals and owner history; silence is not abandonment."""
from dataclasses import replace
import re
import unicodedata
from hashlib import sha256

from .models import FactKind
from .source_currentness import source_states, needs_source_review
from .native_codex import is_platform_control
from .literal_relevance import literal_terms, rank_literal


DIRECTION_ACTIONS={'confirm_abandoned':'abandoned','confirm_deferred':'deferred','restore_direction':'active'}
_CUES={
    'abandoned':re.compile(r'放弃|舍弃|不再(?:采用|推进|开发|实现)|取消(?:该|这个|旧)?(?:方案|计划)|\b(?:abandon(?:ed)?|discard(?:ed)?)\b'),
    'deferred':re.compile(r'暂缓|暂不(?:采用|推进|开发|实现|接入|做)|延后|推迟|延期|\b(?:defer(?:red)?|postpone(?:d)?|on hold)\b'),
}


def direction_suggestion(quote):
    text=unicodedata.normalize('NFKC',quote).casefold()
    # Deliberately conservative: never classify a question, quoted Q&A choice,
    # explicit negation, or multiple different dispositions as one direction.
    if re.search(r'[?？]|是否|要不要|不要|不能|不应|没有|未曾|尚未|未决定|无需|无须|不必|建议|考虑|如果|一旦|'
                 r'不(?:会|想|打算|准备|应该|需要)?(?:放弃|舍弃|暂缓|延后|推迟|延期)|'
                 r'同时[^。\n]{0,24}(?:继续推进|保留|采用)|'
                 r'\b(?:not|never|without|if|would|might|consider)\b',text):return None
    states=[state for state,pattern in _CUES.items() if pattern.search(text)]
    return states[0] if len(states)==1 else None


def scan_explicit_directions(events,*,session_members=None,cursors=None):
    """One bounded rule pass. Cursors describe literal rules, never semantics."""
    from .candidate_extraction import validate_candidates
    from .fact_engine import derive_facts
    from .extraction_progress import source_key
    cursors=dict(cursors or {})
    covered=[(f.evidence_ids,f.quote or f.value) for f in derive_facts(events) if f.kind is FactKind.DECISION]
    public=[e for e in events if e.kind in {'assistant_message','user_message'}
        and e.metadata.get('phase') not in {'analysis','reasoning'} and not is_platform_control(e.content,kind=e.kind)]
    keyed=[(source,source_key(source)) for source in sorted(public,key=lambda e:(e.observed_at,e.id))]
    for source,key in keyed:
        if type(cursors.get(key,0)) is not int or not 0<=cursors.get(key,0)<=len(source.content):
            raise ValueError('direction rule cursor exceeds its immutable source')
    declarations=re.compile(r'决定(?:放弃|舍弃|暂缓)|(?:继续|仍然|仍|明确)(?:放弃|舍弃|暂缓)|(?:会|将)保留为暂缓项|'
                            r'^(?:暂缓|放弃|舍弃)\S{2}|\b(?:we|i)\s+(?:have\s+)?(?:decided\s+to\s+)?(?:abandon|defer|postpone)\b',re.I)
    rows=[];updates={};visited=0;read_chars=0
    for source,key in keyed:
        start=cursors.get(key,0)
        if start==len(source.content):continue
        end=min(len(source.content),start+6000)
        text=source.content[start:end]
        visited+=1;read_chars+=len(text)
        # Revisit the final1600 chars next time. A complete eligible statement
        # crossing that boundary is seen in at least one intact window.
        next_cursor=end if end==len(source.content) else end-1600
        for span in re.finditer(r'[^\n。]+。?',text):
            absolute_start=start+span.start();absolute_end=start+span.end()
            if absolute_start and source.content[absolute_start-1] not in '\n。':continue
            if absolute_end<len(source.content) and source.content[absolute_end-1]!='。' and source.content[absolute_end]!='\n':continue
            quote=span.group().strip()
            if not 3<=len(quote)<=1600 or not declarations.search(quote) or not direction_suggestion(quote):continue
            if any(source.id in ids and quote in existing for ids,existing in covered):continue
            rows.append({'source_id':source.id,'kind':'decision','subject_key':'explicit-direction:'+sha256(quote.encode()).hexdigest()[:16],
                'quote':quote,'task_state':'unknown'})
            covered.append(([source.id],quote))
            if len(rows)==12:
                next_cursor=absolute_end
                break
        updates[key]=next_cursor
        if len(rows)==12 or visited==200:break
    merged={**cursors,**updates}
    total=sum(len(source.content) for source,key in keyed)
    checked=sum(merged.get(key,0) for source,key in keyed)
    complete=sum(merged.get(key,0)==len(source.content) for source,key in keyed)
    proposals=[replace(proposal,metadata={**proposal.metadata,'derivation':'literal-direction-v1'})
        for proposal in validate_candidates(events,{'candidates':rows},session_members=session_members)]
    return proposals,updates,{'enabled':True,'rule':'literal-direction-v1','eligible_sources':len(keyed),
        'completed_sources':complete,'pending_sources':len(keyed)-complete,'scanned_chars':checked,
        'total_chars':total,'batch_sources':visited,'batch_read_chars':read_chars,'semantic_coverage':False,
        'max_statement_chars':1600}


def propose_explicit_directions(events,*,session_members=None):
    """Compatibility helper for one pass; persisted intake uses explicit cursors."""
    return scan_explicit_directions(events,session_members=session_members)[0]


def direction_review_fields(action,fact,reviews):
    state=DIRECTION_ACTIONS[action]
    latest=next((r for r in reversed(reviews) if r.get('action')=='accept_fact' and r.get('subject_key',r.get('fact_id'))==fact.subject_key),None)
    if fact.kind is not FactKind.DECISION:
        raise ValueError('an explicit decision source is required')
    if action=='restore_direction':
        if not latest or latest.get('fact_id')!=fact.id or latest.get('direction_state') not in {'abandoned','deferred'}:
            raise ValueError('only the currently confirmed abandoned or deferred direction can be restored')
    else:
        reviewed=any(r.get('action')=='accept_fact' and r.get('fact_id')==fact.id for r in reviews)
        direction_reviewed=any(r.get('action')=='accept_fact' and r.get('fact_id')==fact.id and r.get('direction_state') in DIRECTION_ACTIONS.values() for r in reviews)
        if direction_reviewed or reviewed and latest and latest.get('fact_id')!=fact.id:
            raise ValueError('previously reviewed direction requires a new explicit declaration; old confirmation cannot be replayed')
        if direction_suggestion(fact.quote or fact.value)!=state:
            raise ValueError('matching explicit abandonment or deferral source is required')
    if latest and latest.get('fact_id')==fact.id and latest.get('direction_state')==state:
        raise ValueError('direction state already confirmed')
    return {'direction_state':state,'direction_action':action}


def direction_projection(facts,reviews,events,*,member_id=None):
    sources={e.id:e for e in events}
    by_id={f.id:f for f in facts}
    states=source_states(events,facts)
    def record(fact):
        source=sources.get(fact.evidence_ids[0]) if fact.evidence_ids else None
        return {'fact_id':fact.id,'subject_key':fact.subject_key,'quote':fact.quote or fact.value,
            'member_id':fact.author,'claim_type':fact.claim_type,'evidence_ids':list(fact.evidence_ids),
            'observed_at':fact.observed_at.isoformat(),'source_kind':source.kind if source else None,
            'source_state':states[fact.id]}
    candidates=[]
    for fact in facts:
        suggestion=direction_suggestion(fact.quote or fact.value) if fact.kind is FactKind.DECISION else None
        if suggestion and (member_id is None or fact.author==member_id):
            candidates.append({**record(fact),'direction_state':suggestion,'status':'proposed'})
    latest={}
    for i,review in enumerate(reviews):
        if review.get('action')=='accept_fact':latest[review.get('subject_key',review.get('fact_id'))]=i
    history=[]
    for i,review in enumerate(reviews):
        fact=by_id.get(review.get('fact_id'))
        state=review.get('direction_state')
        if review.get('action')!='accept_fact' or state not in DIRECTION_ACTIONS.values() or not fact or fact.kind is not FactKind.DECISION:
            continue
        if member_id is not None and fact.author!=member_id:continue
        current=latest.get(fact.subject_key)==i and not needs_source_review(states[fact.id])
        history.append({**record(fact),'direction_state':state,'status':'accepted' if current else 'historical',
            'reviewed_by':review.get('reviewer'),'reviewed_at':review.get('observed_at'),
            'review_action':review.get('direction_action'),'current':current})
    return {'candidates':candidates,'current':[r for r in history if r['current']],'history':history}


def direction_requested(question):
    return bool(re.search(r'舍弃|放弃|暂缓|延期|恢复.*(?:方案|方向)|\b(?:abandon|abandoned|defer|deferred|discard|restore)\b',question[:512],re.I))


def extend_direction_answer(answer,facts,reviews,events):
    if not direction_requested(answer.question):return answer
    if answer.scope and answer.scope.get('kind')=='member_choice':return answer
    view=direction_projection(facts,reviews,events,member_id=answer.scope.get('member_id') if answer.scope else None)
    topic=answer.question[:512].casefold()
    if answer.scope and answer.scope.get('member_id'):topic=topic.replace(answer.scope['member_id'].casefold(),' ')
    topic=re.sub(r'舍弃|放弃|暂缓|延期|恢复|方案|方向|历史|哪些|有什么|以及|或者|已经|后来|现在|这个|那个|是否|了吗|是吗|记录|\b(?:abandon(?:ed)?|defer(?:red)?|discard|restore|directions?|plans?|history)\b',' ',topic)
    def matches(rows):return rank_literal(topic,rows,[] if literal_terms(topic) else rows,text=lambda row:row['quote'])
    confirmed=matches(sorted(view['history'],key=lambda r:(r['current'],r.get('reviewed_at') or ''),reverse=True))
    rows=confirmed[:3] or matches(view['candidates'])[:3]
    gap=() if confirmed else ('尚无匹配且经负责人确认的舍弃或暂缓记录；个人候选和未采用建议不会自动成为团队方向状态。',)
    return replace(answer,conclusion='以下按负责人确认记录区分当前方案状态与历史。' if confirmed else '尚无匹配且已确认的舍弃或暂缓方向；已有明确表述仍保持候选。',
        findings=tuple(rows),evidence_ids=tuple(dict.fromkeys([*answer.evidence_ids,*(eid for r in rows for eid in r['evidence_ids'])])),
        evidence_gaps=tuple([*answer.evidence_gaps,*gap]))
