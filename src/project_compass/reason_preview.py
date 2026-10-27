"""Project-bound immutable model suggestions; source checks are not causal proof."""
from copy import deepcopy
from dataclasses import asdict,replace
from hashlib import sha256
import json,re
from pathlib import Path

from .candidate_extraction import source_claim_type,source_member
from .fact_engine import derive_facts
from .native_codex import is_platform_control
from .source_currentness import source_states,needs_source_review


def _hash(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def _fact_hash(fact):
    value=asdict(fact);value.pop('status')
    return _hash(value)


def _public(source):
    return source.kind in {'user_message','assistant_message','agent_message','file_snapshot','git_commit',
        'tool_call','tool_result','command_execution','test_result','file_change'} and source.metadata.get('phase') not in {'analysis','reasoning'} and source.metadata.get('role') not in {'analysis','reasoning'} and not is_platform_control(source.content,kind=source.kind)


def _read(directory,name):
    with (directory/name).open('rb') as handle:data=handle.read(262145)
    if len(data)>262144:raise ValueError('oversized reason preview')
    return data,json.loads(data)


def _text(value,limit,*,empty=False):
    if not isinstance(value,str) or len(value)>limit or not empty and not value.strip():raise ValueError('invalid preview text')
    return value


def _parse(directory):
    raw={};data={}
    for name in ['manifest.json','request.json','schema.json','model-final.json']:
        raw[name],data[name]=_read(directory,name)
    manifest=data['manifest.json'];packet=data['request.json'];payload=data['model-final.json']
    if not isinstance(manifest,dict) or manifest.get('approved') is not True or type(manifest.get('attempts')) is not int or manifest['attempts']!=1 or manifest.get('max_invocations')!=1 or manifest.get('status')!='completed_for_review':raise ValueError('preview is not a completed authorized snapshot')
    for name,key in [('request.json','request_sha256'),('schema.json','schema_sha256')]:
        if sha256(raw[name]).hexdigest()!=manifest.get(key):raise ValueError('preview input digest mismatch')
    if not isinstance(packet,dict) or set(packet)!={'instructions','cases','records'}:raise ValueError('invalid preview packet')
    _text(packet['instructions'],12000)
    if not isinstance(packet['cases'],list) or not 1<=len(packet['cases'])<=12 or not isinstance(packet['records'],list) or not 1<=len(packet['records'])<=24:raise ValueError('invalid preview bounds')
    records={};cases={}
    for row in packet['records']:
        if not isinstance(row,dict) or set(row)!={'id','kind','member_id','claim_type','observed_at','content'}:raise ValueError('invalid source record')
        for key in ['id','kind','member_id','claim_type','observed_at']:_text(row[key],200)
        _text(row['content'],24000)
        if row['id'] in records:raise ValueError('duplicate source')
        records[row['id']]=row
    limit=manifest.get('max_source_chars');cap=manifest.get('max_candidates')
    if type(limit) is not int or not 1<=limit<=24000 or sum(len(r['content']) for r in records.values())>limit or type(cap) is not int or not 1<=cap<=12:raise ValueError('invalid preview limits')
    for case in packet['cases']:
        if not isinstance(case,dict) or set(case)!={'case_id','question','target_fact_id','target_quote','target_status','eligible_source_ids'}:raise ValueError('invalid target')
        _text(case['case_id'],160);_text(case['question'],512);_text(case['target_fact_id'],160);_text(case['target_quote'],1600)
        ids=case['eligible_source_ids']
        if case['case_id'] in cases or not isinstance(ids,list) or not 1<=len(ids)<=24 or any(not isinstance(i,str) or i not in records for i in ids) or len(set(ids))!=len(ids):raise ValueError('invalid target scope')
        if case['target_status']!='proposed':raise ValueError('model target status cannot assert acceptance')
        cases[case['case_id']]=case
    if not isinstance(payload,dict) or set(payload)!={'answers'} or not isinstance(payload['answers'],list):raise ValueError('invalid response')
    answers={};total=0
    for answer in payload['answers']:
        if not isinstance(answer,dict) or set(answer)!={'case_id','candidates','gap'}:raise ValueError('invalid answer fields')
        key=answer['case_id']
        if not isinstance(key,str) or key not in cases or key in answers:raise ValueError('invalid answer target')
        _text(answer['gap'],240,empty=True)
        rows=answer['candidates']
        if not isinstance(rows,list):raise ValueError('invalid candidates')
        total+=len(rows)
        if total>cap or not rows and not answer['gap'].strip():raise ValueError('missing outcome or too many candidates')
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'relation_type','summary','source_quotes','assumptions','counter_evidence'}:raise ValueError('invalid reason fields')
            if not isinstance(row['relation_type'],str) or row['relation_type'] not in {'reported_reason','hypothesized_reason'}:raise ValueError('invalid relation type')
            _text(row['summary'],240)
            if not isinstance(row['assumptions'],list) or len(row['assumptions'])>3 or row['relation_type']=='hypothesized_reason' and not row['assumptions']:raise ValueError('inference needs assumptions')
            for text in row['assumptions']:_text(text,160)
            for field in ['source_quotes','counter_evidence']:
                quotes=row[field]
                if not isinstance(quotes,list) or not (1 if field=='source_quotes' else 0)<=len(quotes)<=3:raise ValueError('invalid quote count')
                for quote in quotes:
                    if not isinstance(quote,dict) or set(quote)!={'source_id','quote'}:raise ValueError('invalid citation fields')
                    sid=quote['source_id'];text=_text(quote['quote'],1600)
                    if not isinstance(sid,str) or sid not in cases[key]['eligible_source_ids'] or text not in records[sid]['content']:raise ValueError('invalid scoped citation')
        answers[key]=answer
    if set(answers)!=set(cases):raise ValueError('each target needs an outcome')
    return raw,manifest,records,cases,answers


def _make_binding(parsed,project_path,events,*,session_members=None):
    try:
        raw,manifest,records,cases,answers=parsed
        project=Path(project_path).resolve(strict=True)
        if not project.is_dir():raise ValueError('project directory required')
        sources={e.id:e for e in events};facts={f.id:f for f in derive_facts(events)}
        for sid,record in records.items():
            source=sources.get(sid)
            if not source or not _public(source) or any(record[key]!=value for key,value in {
                'kind':source.kind,'member_id':source_member(source,session_members=session_members),
                'claim_type':source_claim_type(source),'observed_at':source.observed_at.isoformat(),'content':source.content}.items()):raise ValueError('source binding mismatch')
        for case in cases.values():
            fact=facts.get(case['target_fact_id'])
            if not fact or case['target_quote']!=(fact.quote or fact.value) or not set(fact.evidence_ids)<=set(case['eligible_source_ids']):raise ValueError('target binding mismatch')
        return {'version':1,'project_path':str(project),'session_members':dict(session_members or {}),
            'model':_text(manifest.get('model'),120),'reasoning_effort':_text(manifest.get('reasoning_effort'),32),
            'request_sha256':sha256(raw['request.json']).hexdigest(),'schema_sha256':sha256(raw['schema.json']).hexdigest(),
            'response_sha256':sha256(raw['model-final.json']).hexdigest(),
            'source_hashes':{sid:_hash(sources[sid].to_dict()) for sid in records},
            'target_hashes':{c['target_fact_id']:_fact_hash(facts[c['target_fact_id']]) for c in cases.values()},
            'target_members':{c['target_fact_id']:facts[c['target_fact_id']].author for c in cases.values()}}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):raise ValueError('reason preview does not match valid project sources') from None


def make_reason_binding(directory,project_path,events,*,session_members=None):
    try:return _make_binding(_parse(Path(directory)),project_path,events,session_members=session_members)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):raise ValueError('reason preview does not match valid project sources') from None


class ReasonPreview:
    @classmethod
    def load(cls,directory,project_path,events):
        try:
            directory=Path(directory).resolve(strict=True)
            _,binding=_read(directory,'binding.json')
            if not isinstance(binding,dict):raise ValueError('binding required')
            parsed=_parse(directory)
            expected=_make_binding(parsed,project_path,events,session_members=binding.get('session_members'))
            if binding!=expected:raise ValueError('project snapshot mismatch')
            raw,manifest,records,cases,answers=parsed
            preview=cls();preview._binding=deepcopy(binding);preview._records=deepcopy(records)
            preview._cases=deepcopy(cases);preview._answers=deepcopy(answers)
            preview._creator=_text(manifest.get('model'),120)
            preview.revision=_hash({'binding':binding,'response':json.loads(raw['model-final.json'])})
            return preview
        except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):raise ValueError('reason preview is invalid or belongs to a different project') from None

    def matches_project(self,project_path):
        return project_path is not None and str(Path(project_path).resolve())==self._binding['project_path']

    def project(self,events,*,member_id=None,target_ids=None):
        sources={e.id:e for e in events};facts={f.id:f for f in derive_facts(events)}
        rows=[];gaps=[];unavailable=0
        for key,case in self._cases.items():
            fid=case['target_fact_id']
            if target_ids is not None and fid not in target_ids or member_id is not None and self._binding['target_members'][fid]!=member_id:continue
            fact=facts.get(fid);ids=case['eligible_source_ids']
            valid=fact is not None and _fact_hash(fact)==self._binding['target_hashes'][fid]
            valid=valid and all(sid in sources and _public(sources[sid]) and _hash(sources[sid].to_dict())==self._binding['source_hashes'][sid] for sid in ids)
            if valid:
                check=replace(fact,id='reason-check:'+key,evidence_ids=tuple(dict.fromkeys([*fact.evidence_ids,*ids])))
                valid=not needs_source_review(source_states(events,[check])[check.id])
            if not valid:unavailable+=1;continue
            common={'target_fact_id':fid,'target_quote':fact.quote or fact.value,'member_id':fact.author,
                'target_observed_at':fact.observed_at.isoformat(),'scope_source_ids':list(ids),'evidence_ids':list(ids)}
            answer=self._answers[key]
            def citation(q):
                source=sources[q['source_id']];record=self._records[source.id];start=source.content.index(q['quote'])
                return {**q,'source_start':start,'source_end':start+len(q['quote']),
                    'source_kind':source.kind,'claim_type':source_claim_type(source),'reported_by':source.author,
                    'member_id':record['member_id'],'observed_at':source.observed_at.isoformat()}
            for row in answer['candidates']:
                rows.append({**common,'id':'reason-'+_hash([self._binding['project_path'],fid,row])[:24],
                    'status':'proposed','model':self._creator,'relation_type_suggested':row['relation_type'],
                    'summary':row['summary'],'assumptions':list(row['assumptions']),
                    'source_quotes':[citation(q) for q in row['source_quotes']],
                    'counter_evidence':[citation(q) for q in row['counter_evidence']]})
            if answer['gap']:gaps.append({**common,'text':answer['gap'],'scope_bound':True})
        return deepcopy({'enabled':True,'preview_only':True,'read_only':True,'source_validation_only':True,
            'revision':self.revision,'candidates':rows,'gaps':gaps,'unavailable_targets':unavailable})

    def for_answer(self,answer,events):
        if not re.search(r'为什么|为何|原因|\b(?:why|reason)\b',answer['question'][:512],re.I):return answer
        scope=answer.get('scope') or {}
        ids={row.get('fact_id') for row in answer.get('findings',[])}
        if scope.get('kind')=='member_choice':ids=set()
        view=self.project(events,target_ids=ids,member_id=scope.get('member_id'))
        used=[eid for row in [*view['candidates'],*view['gaps']] for eid in row['evidence_ids']]
        return {**answer,'reason_preview':view,'evidence_ids':list(dict.fromkeys([*answer['evidence_ids'],*used]))}
