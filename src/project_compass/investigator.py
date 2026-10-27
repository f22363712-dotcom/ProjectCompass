from __future__ import annotations

from dataclasses import dataclass
import re

from .candidate_extraction import source_claim_type, source_member
from .fact_engine import derive_facts
from .models import CandidateFact, EvidenceEvent, FactKind
from .native_codex import is_platform_control
from .source_currentness import source_states, needs_source_review
from .literal_relevance import rank_literal
from .raw_source_investigation import extend_raw_sources, raw_requests, raw_scope_question
from .directions import extend_direction_answer, direction_requested


@dataclass(frozen=True)
class ConflictSide:
    value: str
    evidence_ids: tuple[str, ...]
    author: str
    status: str = "conflicted"
    source_status: str = 'recorded'
    observed_at: str | None = None
    source_kind: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class Conflict:
    subject_key: str
    sides: tuple[ConflictSide, ...]


@dataclass(frozen=True)
class InvestigationAnswer:
    question: str
    conclusion: str
    goal: str
    active_tasks: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    assumptions: tuple[str, ...]
    conflicts: tuple[Conflict, ...]
    next_action: str
    acceptance_condition: str
    evidence_gaps: tuple[str, ...]
    findings: tuple[dict, ...] = ()
    next_action_status: str | None = None
    source_warnings: tuple[dict, ...] = ()
    scope: dict | None = None
    next_action_basis: dict | None = None

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "conclusion": self.conclusion,
            "goal": self.goal,
            "active_tasks": list(self.active_tasks),
            "evidence_ids": list(self.evidence_ids),
            "assumptions": list(self.assumptions),
            "conflicts": [{"subject_key": item.subject_key, "sides": [{"value": side.value, "evidence_ids": list(side.evidence_ids), "author": side.author, "status": side.status, 'source_status': side.source_status,
                'observed_at': side.observed_at, 'source_kind': side.source_kind, 'session_id': side.session_id} for side in item.sides]} for item in self.conflicts],
            "next_action": self.next_action,
            "acceptance_condition": self.acceptance_condition,
            "evidence_gaps": list(self.evidence_gaps),
            "findings": list(self.findings),
            "next_action_status": self.next_action_status,
            'source_warnings': list(self.source_warnings),
            'scope': self.scope,
            'next_action_basis': self.next_action_basis,
        }


def _member_query(events, facts, question, session_members, *, raw_query=False, direction_query=False):
    public_kinds = {'agent_message', 'assistant_message', 'user_message', 'tool_call',
                    'tool_result', 'command_execution', 'test_result', 'file_change'}
    raw = [e for e in events if e.kind in public_kinds and not is_platform_control(e.content, kind=e.kind)]
    members = sorted(({source_member(e, session_members=session_members) for e in raw}
                      | {f.author for f in facts}) - {'project-documents'})
    if raw_query:question=raw_scope_question(question,known_members=members,sources=raw)
    # ASCII identity boundaries allow Chinese questions without spaces while
    # avoiding Ann/Anna and dev-a/dev-a-extra collisions.
    def complete_mention(match):
        before, after = question[:match.start()], question[match.end():]
        if before and before[-1].isalnum() and not before[-1].isascii():
            if not re.search(r'(?:请问|问一下|关于|查看|查询|看看|成员|同事|和|与|及|为什么|为何)$', before):
                return False
        if after and after[0].isalnum() and not after[0].isascii():
            suffix=r'(?:做了|做过|做什么|负责|的|下一步|接下来|当前|目前|最近|今天|分别|和|与|及|进展|状态|为什么|为何)'
            if raw_query:suffix=r'(?:命令|终端|测试|工具|提交|原始)|'+suffix
            if direction_query:suffix=r'(?:舍弃|放弃|暂缓|延期|恢复|为什么|为何)|'+suffix
            if not re.match(suffix, after):
                return False
        return True

    mentions = [(match.start(), match.end(), member) for member in members
                for match in re.finditer(r'(?<![A-Za-z0-9_-])' + re.escape(member)
                                        + r'(?![A-Za-z0-9_-])', question, re.I) if complete_mention(match)]
    # A full identity owns its span; a separate short-name mention still counts.
    matched = sorted({member for start, end, member in mentions if not any(
        low <= start and high >= end and (low, high) != (start, end)
        for low, high, _ in mentions)})
    if len(matched) == 1:
        member = matched[0]
        return {'kind': 'member', 'member_id': member}, [e for e in raw
            if source_member(e, session_members=session_members) == member]
    query = question.casefold()
    actions=r'下一步|接下来|当前状态|状态|进展|做了|做过|负责|\b(?:next|status|progress|tasks?|decisions?)\b'
    if raw_query:actions+=r'|命令|终端|测试|工具|(?<![a-z0-9_])(?:git|commits?|commands?|terminal|tests?|tools?)(?![a-z0-9_])'
    if direction_query:actions+=r'|舍弃|放弃|暂缓|延期|恢复|\b(?:abandon|abandoned|defer|deferred|discard|restore)\b'
    action_subject = re.split(actions,query,maxsplit=1)[0].strip(' ，,？?。.!')
    direction_prefix=action_subject
    if raw_query or direction_query:
        action_subject=re.sub(r'请问|请|查看|查询|看看|最近|最新|目前|当前|原始|失败|成功|的|\b(?:show|list|find|recent|latest|raw|the|what|are|is)\b',' ',action_subject).strip()
    generic_subjects = {'', '我们', '我', '现在', '目前', '当前', '然后', '接着', 'what', 'what should we do',
                        'what should i do', 'what should happen', 'what is', 'what are', 'how is', 'how are', 'do'}
    unknown_subject = action_subject != query.strip(' ，,？?。.!') and action_subject not in generic_subjects and not any(
        term in action_subject for term in ('项目', '团队', '整体', '全局', 'project', 'team', '为什么', 'why'))
    if direction_query and not raw_query:
        explicit_member=bool(re.search(r'(?:的|[\u2019\x27]s)\s*$',direction_prefix) or re.fullmatch(r'[a-z0-9_-]+',direction_prefix))
        unknown_subject=unknown_subject and explicit_member and not any(term in direction_prefix for term in ('方案','方向','功能','模块','任务','哪些','什么'))
    why_subject=re.match(r'^\s*(?:请问\s*)?([a-z][a-z0-9_-]*)\s*(?:为什么|为何)',query)
    unknown_why_member=bool(why_subject and why_subject[1] not in {'project','team','we','i'})
    if matched or unknown_subject or unknown_why_member or any(term in query for term in
                      ('同事', '成员', '另一个人', '其他人', '做了什么', 'teammate', 'colleague', 'who did', 'what did')):
        return {'kind': 'member_choice', 'available_members': members}, []
    return None, []


def _member_source_answer(question, scope, raw):
    choice = scope['kind'] == 'member_choice'
    member = scope.get('member_id')
    selected = sorted([e for e in raw if e.content.strip()], key=lambda e: (e.observed_at, e.id), reverse=True)[:3]
    findings = tuple({'quote': e.content[:1600], 'status': 'raw', 'claim_type': source_claim_type(e),
        'member_id': member, 'observed_at': e.observed_at.isoformat(), 'evidence_ids': [e.id],
        'source_kind': e.kind, 'session_id': e.session_id,
        'source_ref': e.metadata.get('source_ref'), 'excerpt_truncated': len(e.content) > 1600} for e in selected)
    names = '、'.join(scope.get('available_members', []))
    conclusion = ('请在问题中写明一个成员名。已知成员有' + names + '。' if names else '尚未找到可识别的成员，请先接入公开记录。') if choice else f'找到 {member} 的公开来源记录，尚未生成该成员的候选声明。'
    return InvestigationAnswer(question, conclusion, '成员记录待明确' if choice else f'{member} 的公开来源记录',
        (), tuple(e.id for e in selected), (), (),
        '在问题中写明一个成员名，再查询其公开记录。' if choice else '打开该成员的来源，核对对应版本和实际检查条件。',
        '回答的成员归属与原文来源一致；报告中的完成状态仍需核对。',
        ('成员指向尚不明确，未选择其他成员的记录。',) if choice else
        ('下方仅为有界原文摘录，尚未提取或确认其任务状态。',), findings, scope=scope)


def investigate(events: list[EvidenceEvent], question: str, fact_reviews=(), confirmed_relations=(), *, memory_facts=None, memory_support=None, session_members=None) -> InvestigationAnswer:
    answer=_investigate_claims(events,question,fact_reviews,confirmed_relations,memory_facts=memory_facts,
        memory_support=memory_support,session_members=session_members,raw_query=bool(raw_requests(question)[0]))
    if raw_requests(question)[0]:return extend_raw_sources(answer,events,question,session_members=session_members)
    return extend_direction_answer(answer,derive_facts(events) if memory_facts is None else memory_facts,fact_reviews,events)


def _investigate_claims(events, question, fact_reviews, confirmed_relations, *, memory_facts, memory_support, session_members, raw_query):
    facts = derive_facts(events) if memory_facts is None else memory_facts
    scope_question=question
    scope, member_sources = _member_query(events, facts, scope_question, session_members,raw_query=raw_query,direction_query=direction_requested(question))
    if scope:
        member_facts = [f for f in facts if f.author == scope.get('member_id')]
        if not member_facts:
            return _member_source_answer(question, scope, member_sources)
        subjects = {f.subject_key for f in member_facts}
        facts = [f for f in facts if f.subject_key in subjects]
    if not facts:
        gap = "已采集材料尚未生成带来源的候选事实，无法判断项目当前状态和项目下一步。" if events else "尚未采集项目证据，无法判断项目状态。"
        return InvestigationAnswer(
            question=question,
            conclusion="证据不足，当前无法形成项目状态结论。",
            goal="未找到已记录的项目目标",
            active_tasks=(),
            evidence_ids=(),
            assumptions=(),
            conflicts=(),
            next_action="先从项目材料提取候选目标和任务，核对来源后再提交团队确认。",
            acceptance_condition="候选目标和任务均能打开对应原文，且未确认条目保持候选状态。",
            evidence_gaps=(gap,),
        )
    accepted_by_subject = {
        str(item.get("subject_key", item.get("fact_id"))): item["fact_id"]
        for item in fact_reviews
        if item.get("action") == "accept_fact" and item.get("fact_id")
    }

    def status(fact: CandidateFact) -> str:
        return "accepted" if accepted_by_subject.get(fact.subject_key) == fact.id else fact.status.value

    sources = {event.id: event for event in events}
    reviews_by_fact = {item.get('fact_id'): item for item in fact_reviews if item.get('action') == 'accept_fact'}

    def finding(fact):
        review = reviews_by_fact.get(fact.id, {}) if status(fact) == 'accepted' else {}
        source = sources.get(fact.evidence_ids[0]) if fact.evidence_ids else None
        return {'fact_id': fact.id, 'quote': fact.quote or fact.value, 'kind': fact.kind.value,
                'claim_type': fact.claim_type, 'status': status(fact), 'evidence_ids': list(fact.evidence_ids),
                'source_ref': fact.source_ref, 'reviewed_by': review.get('reviewer'),
                'reviewed_at': review.get('observed_at'), 'member_id': fact.author,
                'observed_at': fact.observed_at.isoformat(), 'session_id': fact.session_id,
                'source_kind': source.kind if source is not None else None,
                **({'direction_state':review['direction_state'],'current':not needs_source_review(states[fact.id])}
                   if review.get('direction_state') in {'abandoned','deferred','active'} else {})}

    states = source_states(events, facts)
    current_facts = [fact for fact in facts if not needs_source_review(states[fact.id])]
    member_current = [f for f in current_facts if not scope or f.author == scope['member_id']]
    current_accepted = {fact.subject_key: fact.id for fact in current_facts if status(fact) == 'accepted'}
    warnings = tuple({'fact_id': fact.id, 'status': status(fact), 'evidence_ids': list(fact.evidence_ids),
                      'source_state': states[fact.id]} for fact in facts if needs_source_review(states[fact.id]))
    goals = [fact for fact in member_current if fact.kind is FactKind.GOAL]
    tasks = [fact for fact in member_current if fact.kind is FactKind.TASK]
    assumptions = [fact.value for fact in member_current if fact.kind is FactKind.ASSUMPTION]
    relevant_fact_ids = {fact.id for fact in facts}
    relevant_source_ids = {evidence_id for fact in facts if fact.id in relevant_fact_ids for evidence_id in fact.evidence_ids}
    relevant_event_ids = relevant_source_ids | {
        relation.evidence_id for relation in confirmed_relations if relation.fact_id in relevant_fact_ids
    }
    if memory_support is not None:
        relevant_event_ids = {eid for fid, ids in memory_support.items() if fid in relevant_fact_ids for eid in ids}
    relevant_event_ids |= {eid for warning in warnings for key in ('replacement_ids', 'observation_ids') for eid in warning['source_state'][key]}
    evidence_ids = tuple(event.id for event in events if event.id in relevant_event_ids
                         and not is_platform_control(event.content, kind=event.kind))
    conflicts: list[Conflict] = []
    for subject_key in sorted({fact.subject_key for fact in facts}):
        candidates = [fact for fact in facts if fact.subject_key == subject_key and len({item.value for item in facts if item.subject_key == subject_key}) > 1]
        if candidates:
            conflicts.append(Conflict(subject_key, tuple(ConflictSide(fact.value, fact.evidence_ids,
                fact.author, status(fact), states[fact.id]['status'], fact.observed_at.isoformat(),
                sources[fact.evidence_ids[0]].kind if fact.evidence_ids and fact.evidence_ids[0] in sources else None,
                fact.session_id) for fact in candidates)))
    accepted_goals = [fact for fact in goals if status(fact) == "accepted"]
    goal = (accepted_goals or goals)[0].value if (accepted_goals or goals) else "未找到已记录的项目目标"
    active_tasks = tuple(
        fact.value
        for fact in tasks
        if current_accepted.get(fact.subject_key) in (None, fact.id) and fact.task_state != "reported_complete"
    )
    unresolved = [conflict for conflict in conflicts if not any(side.status == "accepted" and side.source_status not in {'superseded', 'unavailable'} for side in conflict.sides)]
    gaps = []
    if not goals:
        gaps.append("尚未提取到该成员的目标候选。" if scope else "尚未提取到带来源的项目目标。")
    if not tasks:
        gaps.append("尚未提取到该成员的任务候选。" if scope else "尚未提取到带来源的任务状态。")
    if warnings:
        gaps.append(f'{len(warnings)} 条声明的文件来源已换版或当前不可用，原确认保留，当前状态需要复核。')
    if unresolved:
        next_action = "核对冲突任务的真实状态，并由项目负责人确认当前状态。"
        acceptance = "项目看板保留双方证据，负责人确认后生成一条带证据链接的当前团队事实。"
    elif warnings and not current_facts:
        next_action = '先检查变更后的来源并重新提取候选，再由负责人核对当前状态。'
        acceptance = '旧来源和确认历史保留，新版本的候选有独立证据且不会自动继承确认。'
    elif not current_accepted:
        next_action = "先核对候选事实的来源，再由项目负责人确认团队状态。"
        acceptance = "确认记录包含负责人和来源，候选事实不会自动成为团队事实。"
    else:
        next_action = "按已确认的团队事实推进，并核对剩余证据缺口。"
        acceptance = "当前团队事实保持 accepted，来源证据和历史冲突均可在看板中打开。"
    conclusion = f"项目当前围绕“{goal}”推进，共记录 {len(active_tasks)} 条当前任务状态；保留 {len(conflicts)} 个可追溯冲突。"
    if warnings and not current_facts:
        conclusion = '现有声明的文件来源已变化或不可用，尚无可用于当前状态结论的候选；请先复核来源。'
    findings = []
    next_action_status = None
    next_action_basis = None
    extracted = sorted([fact for fact in member_current if fact.quote or scope], key=lambda fact: fact.observed_at, reverse=True)
    if extracted:
        query = question.casefold()
        pending = [fact for fact in extracted if fact.kind is FactKind.TASK and fact.task_state in {"pending", "active", "blocked"}
                   and current_accepted.get(fact.subject_key) in (None, fact.id)]
        pending.sort(key=lambda fact: status(fact) != "accepted")
        finding_pool = None
        if scope:
            selected = extracted
        elif direction_requested(question) or any(term in query for term in ("方向", "决策", "direction", "decision", "changed")):
            selected = [fact for fact in extracted if fact.kind is FactKind.DECISION]
        elif any(term in query for term in ("阻塞", "未完成", "未验证", "blocked", "untrusted")):
            selected = pending + [fact for fact in extracted if fact.kind is FactKind.ASSUMPTION]
        elif any(term in query for term in ("下一步", "接下来", "next")) and not any(term in query for term in ("为什么", "why", "状态", "state")):
            selected = pending
        elif any(term in query for term in ("为什么", "why", "状态", "state")):
            selected = [fact for fact in extracted if status(fact) == "accepted"] + [fact for fact in extracted if fact.kind is FactKind.ASSUMPTION] + pending
            finding_pool = extracted
        else:
            selected = []
            gaps.append("当前调查仅支持方向变化、未完成或未验证事项、当前状态及下一步问题；此问题尚未得到回答。")
        selected = list({fact.id: fact for fact in selected}.values())
        ranking_question = question
        if scope:
            ranking_question = re.sub(r'(?<![A-Za-z0-9_-])' + re.escape(scope['member_id']) +
                                      r'(?![A-Za-z0-9_-])', ' ', question, flags=re.I)
        selected = rank_literal(ranking_question, selected if finding_pool is None else finding_pool, selected,
            text=lambda fact:fact.quote or fact.value)
        for fact in selected[:3]:
            findings.append(finding(fact))
        if not unresolved and pending:
            next_action = pending[0].value
            next_action_status = status(pending[0])
            next_action_basis = finding(pending[0])
            acceptance = "按来源原文核对该待办的结果与验收条件；尚未确认的完成报告须由项目负责人审核。"
        if any(status(fact) != "accepted" for fact in extracted):
            gaps.append("部分来源声明仍待负责人确认；已确认条目单独标记为 accepted。")
        gaps.append("本次提取覆盖选定材料片段，尚未核验全部历史及版本替代关系。")
        gaps.append('来源可读取不代表历史报告仍适用于当前版本；候选任务须核对来源时间与版本。')
        conclusion = f"来源记录的项目目标为“{goal}”。下方列出与问题相关的原文声明，确认状态分别保留。"
        if not selected:
            gaps.append("没有找到与该问题类型对应的候选原文。")
    if scope:
        member = scope['member_id']
        goal = f'{member} 的记录'
        conclusion = f'找到 {member} 的 {len(member_facts)} 条声明和 {len(member_sources)} 条公开来源，下方显示至多三条带时间的来源声明。'
        if not member_current:
            conclusion = f'{member} 的声明来源已变化或不可用，需要复核后再判断当前工作。'
        if next_action_status is None:
            next_action = '打开该成员的来源，核对对应版本和实际检查条件。'
            acceptance = '成员归属、来源时间和版本相符；历史完成报告不自动成为当前验收结论。'
        gaps.append('这里查询成员记录；历史报告和原文任务状态不等于当前版本已验收。')
        if any(f.author != member for f in facts):
            gaps.append('同一主题涉及其他成员的声明，相关确认与冲突证据同时保留。')
    return InvestigationAnswer(question, conclusion, goal, active_tasks, evidence_ids, tuple(assumptions), tuple(conflicts), next_action, acceptance, tuple(gaps), tuple(findings), next_action_status, warnings, scope, next_action_basis)
