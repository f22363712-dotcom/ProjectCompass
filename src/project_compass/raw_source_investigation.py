"""Read-only original-material retrieval, separate from accepted project truth."""
from dataclasses import replace
import re
import unicodedata

from .candidate_extraction import source_claim_type, source_member
from .evidence_linking import command_context
from .literal_relevance import literal_terms, rank_literal
from .native_codex import is_platform_control


QUOTED = re.compile(r'`([^`]{1,512})`|“([^”]{1,512})”|"([^"]{1,512})"')
EXIT_QUERY = re.compile(r'(?:退出码|exit(?:\s+code)?)\s*(?:为|是|=|:)?\s*(-?\d+)')
TEST_MATERIAL = re.compile(r'测试|(?<![a-z0-9_])(?:tests?|pytest|unittest|ctest|test_result)(?![a-z0-9_])')
CODE_LEXEME = re.compile(r'(?<![A-Za-z0-9_.+-])(?:[A-Za-z]:)?/?[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*')
CODE_EXTENSIONS = {'java','kt','py','js','jsx','ts','tsx','cs','cpp','c','h','go','rs','md','json','jsonl','xml','toml','yaml','yml','gradle','html','css','txt','log','csv','ps1','sh','bat'}


def raw_requests(question):
    query=unicodedata.normalize('NFKC',question[:512]).casefold()
    groups=[]
    if re.search(r'(?<![a-z0-9_])(?:git|commits?)(?![a-z0-9_])',query):groups.append('git')
    command=bool(re.search(r'命令|终端|(?<![a-z0-9_])(?:command|terminal|exec_command)(?![a-z0-9_])',query))
    tests=bool(re.search(r'测试|(?<![a-z0-9_])(?:tests?|test_result)(?![a-z0-9_])',query))
    output=bool(re.search(r'原文|原始|输出|记录|结果|日志|\b(?:raw|output|records?|results?|logs?)\b',query))
    if command or (tests and output):groups.append('execution')
    if re.search(r'工具(?:输出|结果)|\btool\s+(?:output|result)',query):groups.append('tool')
    return groups,tests


def _search_text(event):
    context=command_context(event)
    command=event.metadata['command'] if context else ''
    if isinstance(command,list):command=' '.join(command)
    return ' '.join([event.content,event.id,str(event.metadata.get('source_ref','')),command])


def _path_text(value):
    return re.sub('/+','/',unicodedata.normalize('NFKC',value).replace('\\','/'))


def _member_reference(member):
    return re.compile(r'(?<![a-z0-9_./\\+-])'+re.escape(unicodedata.normalize('NFKC',member))+r'(?![a-z0-9_/\\+-]|\.[a-z0-9_])',re.I)


def _code_word(word):
    return bool(word and ('.' in word or '/' in word or '_' in word or re.search(r'[a-z][A-Z]',word) or len(word)>=16)) and word.casefold() not in {'test_result','exec_command','git_commit','command_execution','tool_result','tool_call'}


def raw_scope_question(question,*,known_members=(),sources=()):
    """Explicit selectors are topics, not potential member identities."""
    query=QUOTED.sub(' ',unicodedata.normalize('NFKC',question[:512]))
    mentions=sorted((match.start(),match.end(),member) for member in known_members for match in _member_reference(member).finditer(query))
    # Keep full member spans opaque while parsing the surrounding code topics.
    mentions=[row for row in mentions if not any(low<=row[0] and high>=row[1] and (low,high)!=(row[0],row[1]) for low,high,_ in mentions)]
    hints=None
    member_intent=bool(re.search(r'成员|同事|开发者|做了|做过|负责',query))
    def mask(match):
        nonlocal hints
        word=match[0].rstrip('.+-')
        if not _code_word(word):return match[0]
        if '/' in word or word.rsplit('.',1)[-1].casefold() in CODE_EXTENSIONS:return ' '
        if member_intent:return match[0]
        if hints is None:
            hints='\n'.join(_path_text(_search_text(e)).casefold() for e in sources
                if e.metadata.get('phase') not in {'analysis','reasoning'} and e.metadata.get('role') not in {'analysis','reasoning'} and not is_platform_control(e.content,kind=e.kind))
        name=re.escape(word.casefold())
        if re.search(r'(?<![a-z0-9_])'+name+r'\.(?:java|kt|py|js|jsx|ts|tsx|cs|cpp|h|go|rs)(?![a-z0-9_])',hints) or re.search(r'\b(?:class|interface|object|enum|record|def|function)\s+'+name+r'(?![a-z0-9_])',hints):return ' '
        return match[0]
    pieces=[];end=0
    for start,stop,member in mentions:
        if start<end:continue
        pieces.extend([CODE_LEXEME.sub(mask,_path_text(query[end:start])),member]);end=stop
    pieces.append(CODE_LEXEME.sub(mask,_path_text(query[end:])))
    return EXIT_QUERY.sub(' ',''.join(pieces).casefold())


def _topic(question,scope):
    query=unicodedata.normalize('NFKC',question[:512]).casefold()
    if scope and scope.get('member_id'):
        query=_member_reference(scope['member_id']).sub(' ',query)
    quoted=[next(piece for piece in match if piece) for match in QUOTED.findall(query)]
    qualifiers=QUOTED.sub(' ',query)
    query=EXIT_QUERY.sub(' ',query)
    query=re.sub(r'原生(?:的)?\s*(?=命令|终端|工具|记录|输出)',' ',query)
    query=re.sub(r'命令|终端|测试|工具|输出|原文|原始|记录|日志|结果|提交|退出码|失败|成功|最近|最新', ' ',query)
    query=re.sub(r'(?<![a-z0-9_])(?:git|commits?|command|terminal|test_result|tests?|tool|outputs?|raw|results?|logs?|exit|code|failed|failure)(?![a-z0-9_])',' ',query)
    return query,quoted,qualifiers


def _code_subjects(question,scope):
    """Literal ASCII subject anchors, not a semantic code-name parser."""
    text=unicodedata.normalize('NFKC',question[:512])
    if scope and scope.get('member_id'):
        text=_member_reference(scope['member_id']).sub(' ',text)
    text=_path_text(QUOTED.sub(' ',text))
    patterns=[]
    for word in dict.fromkeys(match[0] for match in CODE_LEXEME.finditer(text)):
        word=word.rstrip('.+-');term=word.casefold()
        if not _code_word(word):continue
        # A class may occur in Class.java; a requested filename must not borrow
        # File.java.bak. Longer identifiers are never a match for a short one.
        boundary=r'a-z0-9_.+-' if '.' in word or '/' in word else r'a-z0-9_'
        patterns.append(re.compile(r'(?<!['+boundary+'])'+re.escape(term)+r'(?!['+boundary+'])'))
    return patterns


def extend_raw_sources(answer,events,question,*,session_members=None):
    groups,test_outputs=raw_requests(question)
    if not groups or answer.scope and answer.scope.get('kind')=='member_choice':return answer
    member=answer.scope.get('member_id') if answer.scope else None
    def owner(event):
        return 'project-documents' if event.kind=='git_commit' else source_member(event,session_members=session_members)
    eligible=[e for e in events if e.kind in {'git_commit','command_execution','test_result','tool_result'}
        and e.metadata.get('phase') not in {'analysis','reasoning'}
        and not is_platform_control(e.content,kind=e.kind) and (not member or owner(e)==member)]
    topic,quoted,qualifiers=_topic(question,answer.scope)
    code_subjects=_code_subjects(question,answer.scope)
    normalized=lambda value:unicodedata.normalize('NFKC',value).casefold()
    code=EXIT_QUERY.search(qualifiers)
    exact_exit=int(code.group(1)) if code else None
    failed=bool(re.search(r'失败|\b(?:failed|failure)\b',qualifiers))
    succeeded=bool(re.search(r'成功|\b(?:succeeded|successful|success)\b',qualifiers))
    invalid_quote=bool(re.search(r'[`“”"]',qualifiers))
    if invalid_quote:eligible=[]
    findings=[];used=set();gaps=['引用片段未闭合或超出512字符查询范围，未按近似文本选取原文。'] if invalid_quote else []
    quota=min(3,6//len(groups))
    for group in groups:
        def belongs(event):
            if group=='git':return event.kind=='git_commit'
            if group=='tool':return event.kind=='tool_result'
            execution=event.kind in {'command_execution','test_result'} or event.kind=='tool_result' and (test_outputs or bool(command_context(event)))
            return execution and (not test_outputs or event.kind=='test_result' or bool(TEST_MATERIAL.search(normalized(_search_text(event)))))
        pool=[e for e in eligible if belongs(e) and e.id not in used]
        if group!='git' and (exact_exit is not None or failed or succeeded):
            pool=[e for e in pool if type(e.metadata.get('exit_code')) is int and
                (e.metadata['exit_code']==exact_exit if exact_exit is not None else
                 (not failed or e.metadata['exit_code']!=0) and (not succeeded or e.metadata['exit_code']==0))]
        if quoted:pool=[e for e in pool if all(term in normalized(_search_text(e)) for term in quoted)]
        if code_subjects:pool=[e for e in pool if any(pattern.search(_path_text(_search_text(e)).casefold()) for pattern in code_subjects)]
        pool.sort(key=lambda e:(e.observed_at,e.id),reverse=True)
        selected=rank_literal(topic,pool,[] if literal_terms(topic) else pool,text=_search_text)[:quota]
        if not selected:
            title={'git':'Git提交','execution':'命令或测试输出','tool':'工具输出'}[group]
            gaps.append(f'在当前采集与成员范围内未找到匹配的{title}原始记录；这不证明相关工作没有发生。')
        for event in selected:
            used.add(event.id);context=command_context(event)
            agent=event.metadata.get('actor_type')=='agent' or event.metadata.get('origin')=='agent_submission' or event.metadata.get('capture')=='agent-http-v1'
            finding={'quote':event.content[:1600],'status':'raw','claim_type':'assistant_report' if agent else source_claim_type(event),
                'member_id':owner(event),'observed_at':event.observed_at.isoformat(),'source_kind':event.kind,
                'session_id':event.session_id,'source_ref':event.metadata.get('source_ref'),'evidence_ids':[event.id],
                'excerpt_truncated':len(event.content)>1600,**context}
            if event.kind!='git_commit' and 'exit_code' not in finding:
                finding['exit_code']=event.metadata.get('exit_code') if type(event.metadata.get('exit_code')) is int else None
            findings.append(finding)
    old_gaps=[gap for gap in answer.evidence_gaps if '当前调查仅支持' not in gap and '没有找到与该问题类型对应' not in gap]
    state_query=bool(re.search(r'当前状态|项目状态|\bcurrent\s+(?:state|status)\b',qualifiers))
    context=[f for f in answer.findings if f.get('status')!='raw'][:3] if state_query else []
    # A source-only member fallback must not keep citations for unrelated raw
    # records that this selector rejected. Fact-based context keeps its basis.
    raw_fallback=bool(answer.findings) and all(f.get('status')=='raw' for f in answer.findings)
    basis_ids=[] if raw_fallback else answer.evidence_ids
    return replace(answer,conclusion='以下展示当前采集范围内匹配的原始记录；记录时间、来源和项目验收状态分别核对。',
        findings=tuple([*context,*findings]),evidence_ids=tuple(dict.fromkeys([*basis_ids,*(eid for f in findings for eid in f['evidence_ids'])])),
        evidence_gaps=tuple([*old_gaps,*gaps,'原始提交、输出或退出码不自动成为已确认团队事实；历史结果不证明当前版本已验收。']))
