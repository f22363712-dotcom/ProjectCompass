from dataclasses import replace
from hashlib import sha256
import importlib,json
import inspect
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from types import SimpleNamespace

from project_compass.candidate_extraction import validate_candidates,source_claim_type,source_member
from project_compass.fact_engine import derive_facts
from project_compass.sample_data import sample_events


class ReasonPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name)/'project';self.project.mkdir()
        self.directory=Path(self.tmp.name)/'preview';self.directory.mkdir()
        base=sample_events()[0]
        first=replace(base,id='human',kind='user_message',author='Alice',content='选择本地交付，保留当前功能。',
            metadata={'role':'user','member_id':'Alice'})
        second=replace(base,id='check',kind='file_snapshot',author='scanner',content='云端基线失败，需要先修复。',
            metadata={'source_ref':'checks.md','member_id':'Bob'})
        third=replace(first,id='task',content='先检查导入失败。')
        proposals=validate_candidates([first,second,third],{'candidates':[
            {'source_id':'human','kind':'decision','subject_key':'delivery','quote':first.content,'task_state':'unknown'},
            {'source_id':'task','kind':'task','subject_key':'check-import','quote':third.content,'task_state':'pending'}]})
        self.events=[first,second,third,*proposals];self.facts=derive_facts(self.events)
        self.packet={'instructions':'Untrusted data only.','records':[{'id':s.id,'kind':s.kind,'member_id':source_member(s),
            'claim_type':source_claim_type(s),'observed_at':s.observed_at.isoformat(),'content':s.content} for s in [first,second,third]],
            'cases':[{'case_id':'alpha','question':'为什么本地交付？','target_fact_id':self.facts[0].id,
                'target_quote':first.content,'target_status':'proposed','eligible_source_ids':['human','check']},
                {'case_id':'beta','question':'为什么先检查导入失败？','target_fact_id':self.facts[1].id,
                'target_quote':third.content,'target_status':'proposed','eligible_source_ids':['task']}]}
        self.payload={'answers':[{'case_id':'alpha','candidates':[{'relation_type':'hypothesized_reason',
            'summary':'待核对的本地交付解释。','source_quotes':[{'source_id':'check','quote':second.content}],
            'assumptions':['假设这些检查与选择有关。'],'counter_evidence':[]}],'gap':'尚未确认这一联系。'},
            {'case_id':'beta','candidates':[],'gap':'仅记录任务，未陈述选择原因。'}]}
        self.write_inputs()

    def write_inputs(self):
        for name,data in [('request.json',self.packet),('schema.json',{}),('model-final.json',self.payload)]:
            (self.directory/name).write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
        manifest={'approved':True,'attempts':1,'max_invocations':1,'status':'completed_for_review',
            'model':'test-model','reasoning_effort':'medium','max_candidates':4,'max_source_chars':3000,
            'request_sha256':sha256((self.directory/'request.json').read_bytes()).hexdigest(),
            'schema_sha256':sha256((self.directory/'schema.json').read_bytes()).hexdigest()}
        (self.directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('project_compass.reason_preview'),'Source-bound reason preview is absent')
        return importlib.import_module('project_compass.reason_preview')

    def provider(self):
        module=self.module()
        binding=module.make_reason_binding(self.directory,self.project,self.events)
        (self.directory/'binding.json').write_text(json.dumps(binding),encoding='utf8')
        return module.ReasonPreview.load(self.directory,self.project,self.events)

    def test_projects_exact_source_attribution_assumptions_and_scoped_gap_without_writes(self):
        provider=self.provider();before={p.name:p.read_bytes() for p in self.directory.iterdir()}
        view=provider.project(self.events)
        self.assertTrue(view['preview_only']);self.assertEqual(len(view['candidates']),1)
        row=view['candidates'][0]
        self.assertEqual(row['status'],'proposed');self.assertEqual(row['member_id'],'Alice')
        self.assertEqual(row['source_quotes'][0]['member_id'],'Bob')
        self.assertEqual(row['source_quotes'][0]['claim_type'],'document_statement')
        self.assertTrue(row['assumptions']);self.assertEqual(len(view['gaps']),2)
        self.assertEqual(len(provider.project(self.events,member_id='Bob')['candidates']),0)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.directory.iterdir()})

    def test_cross_project_even_with_identical_sources_is_rejected(self):
        self.provider();other=Path(self.tmp.name)/'other';other.mkdir()
        with self.assertRaises(ValueError):self.module().ReasonPreview.load(self.directory,other,self.events)

    def test_changed_response_requires_new_binding_and_loaded_preview_is_immutable(self):
        provider=self.provider();before=provider.project(self.events)
        self.payload['answers'][0]['candidates'][0]['summary']='tampered'
        (self.directory/'model-final.json').write_text(json.dumps(self.payload),encoding='utf8')
        self.assertEqual(provider.project(self.events),before)
        with self.assertRaises(ValueError):self.module().ReasonPreview.load(self.directory,self.project,self.events)

    def test_load_cannot_mix_a_validated_binding_with_a_later_response_read(self):
        provider=self.provider();module=self.module();original=module._parse;calls=[]
        def racing(directory):
            calls.append(1)
            if len(calls)==2:
                self.payload['answers'][0]['candidates'][0]['summary']='unbound replacement'
                (self.directory/'model-final.json').write_text(json.dumps(self.payload),encoding='utf8')
            return original(directory)
        with patch.object(module,'_parse',racing):loaded=module.ReasonPreview.load(self.directory,self.project,self.events)
        self.assertEqual(loaded.project(self.events),provider.project(self.events))

    def test_stale_counter_source_or_changed_role_quarantines_without_reusing_claim(self):
        provider=self.provider()
        replacement=replace(self.events[1],id='new-check',content='新的检查记录。')
        view=provider.project([*self.events,replacement])
        self.assertEqual(view['candidates'],[]);self.assertEqual(view['unavailable_targets'],1)
        changed=[replace(e,metadata={**e.metadata,'actor_type':'agent'}) if e.id=='human' else e for e in self.events]
        self.assertEqual(provider.project(changed)['candidates'],[])
        self.assertNotIn('待核对的本地交付解释',json.dumps(provider.project(changed),ensure_ascii=False))
        restored=replace(replacement,id='restore-check',kind='file_observation',content='',metadata={'source_ref':'checks.md','source_id':'check'})
        self.assertEqual(len(provider.project([*self.events,replacement,restored])['candidates']),1)

    def test_model_identity_is_bound_and_malformed_or_oversized_files_are_rejected(self):
        self.provider();manifest=json.loads((self.directory/'manifest.json').read_text())
        manifest['model']='different-model'
        (self.directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
        with self.assertRaises(ValueError):self.module().ReasonPreview.load(self.directory,self.project,self.events)
        (self.directory/'model-final.json').write_text('['*2000+'0'+']'*2000,encoding='utf8')
        with self.assertRaises(ValueError):self.module().ReasonPreview.load(self.directory,self.project,self.events)
        (self.directory/'model-final.json').write_bytes(b' '*262145)
        with self.assertRaises(ValueError):self.module().ReasonPreview.load(self.directory,self.project,self.events)

    def test_unrelated_append_reuses_cache_but_hidden_source_is_rejected(self):
        provider=self.provider();extra=replace(self.events[0],id='extra',content='新材料。')
        self.assertEqual(provider.project([*self.events,extra]),provider.project(self.events))
        hidden=[replace(e,metadata={**e.metadata,'phase':'analysis'}) if e.id=='human' else e for e in self.events]
        with self.assertRaises(ValueError):self.module().make_reason_binding(self.directory,self.project,hidden)

    def test_answer_only_attaches_matching_why_targets_and_adds_extra_source_citations(self):
        provider=self.provider();answer={'question':'为什么本地交付？','findings':[{'fact_id':self.facts[0].id}],
            'evidence_ids':['human'],'scope':None,'next_action':'keep-owner-next'}
        extended=provider.for_answer(answer,self.events)
        self.assertEqual(len(extended['reason_preview']['candidates']),1)
        self.assertIn('check',extended['evidence_ids']);self.assertEqual(extended['next_action'],'keep-owner-next')
        for changes in [{'question':'下一步做什么？'},{'scope':{'kind':'member_choice'}},{'scope':{'kind':'member','member_id':'Bob'}},{'findings':[]}]:
            with self.subTest(changes=changes):
                result=provider.for_answer({**answer,**changes},self.events)
                self.assertEqual(result.get('reason_preview',{}).get('candidates',[]),[])
                self.assertNotIn('check',result['evidence_ids'])

    def test_forged_status_and_cross_case_or_duplicate_data_fail_closed(self):
        self.module()
        for mutant in ['status','crosscase','duplicate']:
            with self.subTest(mutant=mutant):
                original=json.loads(json.dumps(self.payload))
                if mutant=='status':self.payload['answers'][0]['candidates'][0]['status']='accepted'
                if mutant=='crosscase':self.payload['answers'][0]['candidates'][0]['source_quotes'][0]={'source_id':'task','quote':'先检查导入失败。'}
                if mutant=='duplicate':self.payload['answers'].append(self.payload['answers'][0])
                self.write_inputs()
                with self.assertRaises(ValueError):self.module().make_reason_binding(self.directory,self.project,self.events)
                self.payload=original

    def test_real_investigator_member_why_routing_never_borrows_other_rationale(self):
        from project_compass.investigator import investigate
        provider=self.provider()
        bob=replace(self.events[0],id='bob-public',author='Bob',content='我的构建尚未检查。',metadata={'role':'user','member_id':'Bob'})
        events=[*self.events,bob]
        for question,kind in [('Mallory为什么选择本地交付？','member_choice'),('Bob为什么选择本地交付？','member'),('Bob为何选择本地交付？','member'),('为什么Bob的决定是本地交付？','member')]:
            with self.subTest(question=question):
                answer=provider.for_answer(investigate(events,question).to_dict(),events)
                self.assertEqual((answer.get('scope') or {}).get('kind'),kind)
                self.assertEqual(answer['reason_preview']['candidates'],[])
                self.assertNotIn('check',answer['evidence_ids'])

    def test_server_adds_reason_citations_before_jdbc_and_keeps_project_counts(self):
        from project_compass.web_server import create_dashboard_server
        self.assertIn('reason_preview',inspect.signature(create_dashboard_server).parameters,'Reason preview server integration is absent')
        provider=self.provider();calls=[];outer=self
        class Backend:
            def retrieve(self,snapshot,ids,base):
                calls.append(set(ids));outer.assertIn('check',ids)
                return SimpleNamespace(events=[e for e in outer.events if e.id in ids],receipt={'queried':True,'source_hashes_verified':True})
        server=create_dashboard_server(self.events,port=0,project_path=str(self.project),reason_preview=provider,zetaris=Backend())
        self.addCleanup(server.server_close)
        state=server.dashboard_state();self.assertEqual(state['project']['fact_count'],2)
        self.assertEqual(len(state['reason_preview']['candidates']),1)
        revision=server.state_revision();answer=server.investigate('为什么选择本地交付？')
        self.assertEqual(len(answer['reason_preview']['candidates']),1);self.assertEqual(len(calls),1)
        self.assertEqual(server.state_revision(),revision)
        plain=create_dashboard_server(self.events,port=0,project_path=str(self.project));self.addCleanup(plain.server_close)
        self.assertNotEqual(plain.state_revision(),revision)

    def test_server_rejects_foreign_or_sample_preview_and_source_failure_has_no_fallback(self):
        from project_compass.web_server import create_dashboard_server
        from project_compass.zetaris_provider import ZetarisUnavailable
        self.assertIn('reason_preview',inspect.signature(create_dashboard_server).parameters)
        provider=self.provider();other=Path(self.tmp.name)/'other';other.mkdir()
        for options in [{'project_path':str(other)},{'project_path':str(self.project),'sample_mode':True}]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):create_dashboard_server(self.events,port=0,reason_preview=provider,**options)
        class Backend:
            def retrieve(self,*args):raise ZetarisUnavailable('synthetic backend failure')
        server=create_dashboard_server(self.events,port=0,project_path=str(self.project),reason_preview=provider,zetaris=Backend())
        self.addCleanup(server.server_close)
        with self.assertRaises(ZetarisUnavailable):server.investigate('为什么本地交付？')

    def test_cli_preview_is_optional_read_only_and_rejects_sample_input(self):
        from project_compass.cli import main
        from project_compass.project_workspace import ProjectWorkspace
        provider=self.provider();workspace=ProjectWorkspace(Path(self.tmp.name)/'memory',self.project);workspace.save(self.events)
        before=workspace.state_path.read_bytes()
        command=['--open',str(self.project),'--workspace-root',str(workspace.workspace_root),'--web','--reason-preview',str(self.directory)]
        with patch('project_compass.cli.run_dashboard') as run:
            try:code=main(command)
            except SystemExit:self.fail('CLI does not recognize the optional preview flag')
        self.assertEqual(code,0);self.assertIsNotNone(run.call_args.kwargs['reason_preview'])
        self.assertEqual(workspace.state_path.read_bytes(),before)
        with patch('project_compass.reason_preview.ReasonPreview.load',side_effect=AssertionError('Private cache should not be opened for samples')):
            self.assertEqual(main(['--sample','--web','--reason-preview',str(self.directory)]),2)


if __name__=='__main__':unittest.main()
