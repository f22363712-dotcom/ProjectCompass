"""Conservative whole-message human directives; exact, unaccepted task reports."""
from dataclasses import replace
from hashlib import sha256
import re
import unicodedata

from .candidate_extraction import source_claim_type, validate_candidates
from .extraction_progress import ExtractionProgress, source_key
from .fact_engine import derive_facts
from .models import FactKind
from .native_codex import is_platform_control


RULE='explicit-user-task-v1'
_DIRECTIVE=re.compile(r'^(?:那就|现在|请|我们)?(?:先|优先)(?:处理|修复|排查|验证|完成|实现|接入|测试|检查|补齐|梳理|搭建)[^\s。!?？,，;；.]|'
    r'^(?:please\s+)?(?:first\s+(?:fix|repair|check|test|build|implement|verify)|prioriti[sz]e)\s+\S',re.I)
_CONTEXT=re.compile(r'[?？]|[吗么呢]\s*[。.!]?\s*$|```|(?:^|\n)\s*>|如果|假设|假想|举例|只是|建议|考虑|或许|还是|撤回|取消|作废|未决定|没决定|尚未决定|前提|仅在|只有|批准后|获批后|审批后|'
    r'(?:等|待)[^，。；\n]{0,16}(?:确认|批准|同意)|(?:确认|批准|同意)后(?:再|才)|'
    r'摘自|引自|转述|原话|旧安排|旧指令|'
    r'\b(?:if|unless|maybe|would|might|consider|withdraw|cancel)\b|'
    r'\b(?:after|until|once|when|pending)\b[^.;\n]{0,60}\b(?:approval|permission|confirmation|confirmed|approved)\b|'
    r'\bsubject\s+to\b|\b(?:agent|assistant)[\'’]s\s+(?:words|reply|response)\b',re.I)
_NEGATED=re.compile(r'不是|并非|不要|无需|不用|暂不|不再|先不动|先不做|暂时不做|'
    r'不(?:处理|修复|排查|验证|完成|实现|接入|测试|检查|补齐|梳理|搭建)|\b(?:not|never|don[\'’]t)\b',re.I)
_OTHER_WORK=re.compile(r'^(?:其他|其余|别的|剩余)(?:功能|任务|模块|事项|工作|文件|内容|部分|代码|需求|接口)?(?:先|暂时)?'
    r'(?:不要|无需|不用|不再|不)(?:改|修改|改动|改变|动|做|调整|接入|推进|处理|修复)(?:了)?$|'
    r'^(?:please\s+)?(?:do\s+not|don[\'’]t|never)\s+(?:change|modify|touch|alter|fix|implement|work\s+on)\s+'
    r'(?:(?:any|the)\s+)?(?:other|unrelated|remaining)\s+(?:features?|work|tasks?|modules?|code|files?|components?|functions?|behaviou?r)(?:\s+(?:yet|now))?$',re.I)


def _unconditional_directive(text):
    if not _DIRECTIVE.search(text) or _CONTEXT.search(text):return False
    # Only a complete simple other-work restriction can be omitted from the
    # negation check. Keep it intact in the stored quote and all context checks.
    task_clauses=[clause.strip() for clause in re.split(r'[，,。.;；\n]',text)]
    return not any(_NEGATED.search(clause) for clause in task_clauses if not _OTHER_WORK.fullmatch(clause))


def record_user_task_candidates(events,workspace=None,*,session_members=None):
    progress=ExtractionProgress(workspace.state_path.parent/'user-task-rules',namespace=RULE+':scan-v1') if workspace else None
    cursors=progress.load() if progress else {}
    public=[e for e in events if e.kind=='user_message' and source_claim_type(e)=='user_statement'
        and e.metadata.get('phase') not in {'analysis','reasoning'} and not is_platform_control(e.content,kind=e.kind)]
    short=[e for e in public if len(e.content)<=1600]
    keyed=[(e,source_key(e)) for e in sorted(short,key=lambda e:(e.observed_at,e.id))]
    for source,key in keyed:
        if type(cursors.get(key,0)) is not int or cursors.get(key,0) not in (0,len(source.content)):
            raise ValueError('user task rule requires a whole immutable source cursor')
    covered=[(f.evidence_ids,f.quote or f.value) for f in derive_facts(events) if f.kind is FactKind.TASK]
    rows=[];updates={};visited=0
    for source,key in keyed:
        if cursors.get(key,0)==len(source.content):continue
        quote=source.content.strip();text=unicodedata.normalize('NFKC',quote)
        visited+=1;updates[key]=len(source.content)
        if len(quote)>=3 and _unconditional_directive(text) and not any(source.id in ids and quote in existing for ids,existing in covered):
            rows.append({'source_id':source.id,'kind':'task','subject_key':'explicit-user-task:'+sha256(quote.encode()).hexdigest()[:16],
                'quote':quote,'task_state':'pending'})
        if len(rows)==12 or visited==200:break
    proposals=[replace(e,metadata={**e.metadata,'derivation':RULE})
        for e in validate_candidates(events,{'candidates':rows},session_members=session_members)]
    if proposals:
        if workspace:
            workspace.save(proposals)
            events[:]=workspace.load()
        else:events.extend(proposals)
    if progress:progress.acknowledge(updates)
    merged={**cursors,**updates}
    complete=sum(merged.get(key,0)==len(source.content) for source,key in keyed)
    return {'updated':bool(proposals),'new_user_task_proposals':len(proposals),'user_task_scan':{
        'enabled':True,'rule':RULE,'eligible_sources':len(keyed),'completed_sources':complete,
        'pending_sources':len(keyed)-complete,'skipped_long_sources':len(public)-len(short),
        'batch_sources':visited,'semantic_coverage':False,'max_message_chars':1600}}
