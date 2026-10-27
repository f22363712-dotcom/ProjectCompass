from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from project_compass.candidate_extraction import source_claim_type, validate_candidates
from project_compass.codex_extractor import CodexExtractor, INSTRUCTIONS
from project_compass.extraction_jobs import ExtractionJobs
from project_compass.fact_engine import derive_facts
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events


class PersonalExtractionTests(unittest.TestCase):
    def source(self, identity, member, text, kind='user_message'):
        return replace(sample_events()[0], id=identity, author='codex', kind=kind, content=text,
            metadata={'role': 'user' if kind=='user_message' else 'assistant', 'member_id': member})

    def test_question_reply_is_an_exchange_not_wholly_user_speech(self):
        text='<send_user_message_question_reply>[{"question":"Use A?","answer":"Yes."}]</send_user_message_question_reply>'
        source=self.source('reply','Alice',text)
        self.assertEqual(source_claim_type(source),'conversation_exchange')
        embedded='<send_user_message_question_reply>'+json.dumps([{'question':'Which marker?',
            'answer':'Keep </send_user_message_question_reply> as quoted text.'}])+'</send_user_message_question_reply>'
        self.assertEqual(source_claim_type(replace(source,content=embedded)),'conversation_exchange')
        self.assertEqual(source_claim_type(replace(source,content=text+' trailing prose')),'user_statement')
        self.assertEqual(source_claim_type(replace(source,metadata={**source.metadata,'actor_type':'agent'})),'assistant_report')
        self.assertEqual(source_claim_type(replace(source,kind='file_snapshot',metadata={})),'document_statement')
        self.assertEqual(source_claim_type(replace(source,kind='assistant_message',metadata={'role':'assistant'})),'assistant_report')
        proposal=validate_candidates([source],{'candidates':[{'source_id':source.id,'kind':'decision',
            'subject_key':'choice','quote':text}]})[0]
        self.assertEqual(derive_facts([source,proposal])[0].claim_type,'conversation_exchange')

    def runner(self, calls):
        def run(args, **kwargs):
            records=json.loads(kwargs['input'][len(INSTRUCTIONS):]);calls.append(records)
            result={'candidates':[{'source_id':r['id'],'kind':'task','subject_key':r['id'],
                'quote':r['content'],'task_state':'pending'} for r in records]}
            Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(result),encoding='utf8')
            return subprocess.CompletedProcess(args,0,json.dumps({'type':'turn.completed',
                'usage':{'input_tokens':20,'output_tokens':5,'cached_input_tokens':0}}),'')
        return run

    def test_member_scope_keeps_ownership_prioritizes_user_and_shares_project_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cache=root/'cache';calls=[]
            user=self.source('alice-user','Alice','Repair the timer first.')
            user=replace(user,metadata={**user.metadata,'source_ref':'C:/private/sessions/long-rollout.jsonl:1'})
            assistant=self.source('alice-assistant','Alice','A newer assistant report.', 'assistant_message')
            bob=self.source('bob','Bob','Other member work.')
            doc=replace(user,id='doc',kind='file_snapshot',metadata={'source_ref':'CONTEXT.md'})
            events=[assistant,user,bob,doc]
            provider=CodexExtractor(command='offline-fake',runner=self.runner(calls),member_id='Alice',focus_member='Alice')
            proposals=provider.extract(events,cache)
            self.assertEqual([r['id'] for r in calls[0]],['alice-user','alice-assistant'])
            self.assertNotIn('C:/private/sessions/',json.dumps(calls[0]))
            self.assertEqual(calls[0][0]['claim_type'],'user_statement')
            self.assertEqual(proposals[0].metadata['source_ref'],user.metadata['source_ref'])
            self.assertEqual({e.metadata['member_id'] for e in proposals},{'Alice'})
            provider.acknowledge(events+proposals)
            self.assertEqual(provider.last_result['scope'],{'kind':'member','member_id':'Alice'})
            self.assertEqual(provider.last_result['pending_sources'],0)
            project=CodexExtractor(command='offline-fake',runner=self.runner(calls),member_id='Alice')
            project.extract(events+proposals,cache)
            self.assertEqual({r['id'] for r in calls[1]},{'bob','doc'})

    def test_legacy_binding_is_used_but_explicit_owner_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy=replace(self.source('legacy','Alice','Repair A.'),metadata={'role':'user'})
            explicit=self.source('explicit','Bob','Repair B.')
            calls=[]
            provider=CodexExtractor(command='offline-fake',runner=self.runner(calls),member_id='Alice',
                focus_member='Alice',session_members={legacy.session_id:'Alice'})
            proposals=provider.extract([legacy,explicit],Path(directory))
            self.assertEqual([r['id'] for r in calls[0]],['legacy'])
            self.assertEqual(proposals[0].metadata['member_id'],'Alice')

    def test_jobs_share_budget_and_reject_unknown_or_exhausted_scope_before_spending(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);w=ProjectWorkspace(root/'state',root)
            a=self.source('a','Alice','Check A.');b=self.source('b','Bob','Check B.')
            w.save([a,b]);events=w.load();calls=[];run=self.runner(calls)
            def factory(**kwargs):return CodexExtractor(command='offline-fake',runner=run,**kwargs)
            jobs=ExtractionJobs(w,events,'Alice',extractor_factory=factory,max_batches=2)
            self.addCleanup(jobs.close)
            with self.assertRaises(ValueError):jobs.start(focus_member='missing')
            self.assertEqual(jobs.status()['batches_used'],0)
            self.assertTrue(jobs.start(focus_member='Alice'));jobs.wait(3)
            self.assertEqual(jobs.status()['state'],'complete')
            self.assertFalse(jobs.start(focus_member='Alice'))
            self.assertEqual(jobs.status()['batches_used'],1)
            self.assertTrue(jobs.start(focus_member='Bob'));jobs.wait(3)
            self.assertFalse(jobs.start())
            self.assertEqual(jobs.status()['batches_used'],2)
            self.assertEqual(jobs.status()['known_tokens'],50)
            self.assertEqual(len(calls),2)
            self.assertEqual(set(jobs.status()['eligible_members']),{'Alice','Bob'})


if __name__=='__main__':unittest.main()
