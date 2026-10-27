import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_compass.project_workspace import ProjectWorkspace
from project_compass.fact_engine import derive_facts


class AgentInboxTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('project_compass.agent_inbox'))
        self.module=importlib.import_module('project_compass.agent_inbox')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name)/'project';self.project.mkdir()
        self.workspace=ProjectWorkspace(Path(self.temp.name)/'state',self.project)
        self.events=[];self.workspace.save([])
        self.workspace.save_review_state([],[],[{'action':'accept_fact','fact_id':'existing','reviewer':'owner'}])
        self.config={'schema_version':1,'project_path':str(self.project),'owner_id':'owner','clients':[
            {'client_id':'client-a','member_id':'agent-a','session_id':'session-a','agent':'codex','token':'a'*40},
            {'client_id':'client-b','member_id':'agent-b','session_id':'session-b','agent':'claude-code','token':'b'*40}]}
        self.inbox=self.module.AgentInbox(self.config,self.workspace,self.events)
        self.a=self.inbox.authenticate('Bearer '+'a'*40);self.b=self.inbox.authenticate('Bearer '+'b'*40)

    def event(self,**changes):
        event={'id':'external-one','type':'assistant_message','timestamp':'2026-10-25T08:00:00Z','content':'Next verify the final package on the same phone.'}
        event.update(changes)
        return {'schema_version':1,'events':[event]}

    def test_project_bound_identity_append_and_complete_replay_are_immutable(self):
        result=self.inbox.append_events(self.a,self.event(source_ref='reported.md:3'))
        self.assertEqual(result['appended'],1);event=self.events[0]
        self.assertEqual(event.metadata['member_id'],'agent-a');self.assertEqual(event.session_id,'session-a')
        self.assertEqual(event.author,'codex');self.assertEqual(event.metadata['reported_source_ref'],'reported.md:3')
        self.assertTrue(event.metadata['source_ref'].startswith('agent://client-a/'))
        before=self.workspace.state_path.read_bytes()
        self.assertEqual(self.inbox.append_events(self.a,self.event(source_ref='reported.md:3'))['appended'],0)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
        for change in ({'content':'changed'},{'source_ref':'different.md:9'},{'timestamp':'2026-10-25T09:00:00Z'}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.inbox.append_events(self.a,self.event(**change))
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
        other=self.inbox.append_events(self.b,self.event())
        self.assertNotEqual(result['ids'],other['ids'])
        self.assertEqual(self.workspace.load_review_state()[2][0]['fact_id'],'existing')

    def test_untrusted_fields_roles_sensitive_and_partial_batch_rejected(self):
        before=self.workspace.state_path.read_bytes()
        for changes in ({'metadata':{'facts':[]}},{'author':'owner'},{'session_id':'other'},
                        {'type':'user_message'},{'type':'file_observation'}, {'content':'API_KEY=synthetic-only'},
                        {'content':'a'*40},
                        {'type':'tool_call','command':['mysql','--password','synthetic-only']},
                        {'type':'tool_call','command':'mysql --password synthetic-only'},
                        {'type':'tool_call','command':'curl --token synthetic-only'},
                        {'type':'tool_call','command':['pwsh','-Command','mysql --password synthetic-only']},
                        {'timestamp':'2026-10-25T08:00:00'},{'content':'x'*48001}):
            payload=self.event(**changes)
            payload['events'].insert(0,self.event(id='valid-prefix')['events'][0])
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.inbox.append_events(self.a,payload)
            self.assertEqual(self.workspace.state_path.read_bytes(),before)
            self.assertEqual(self.events,[])

    def test_exact_source_proposal_has_submitter_and_preserves_source_owner(self):
        source=self.inbox.append_events(self.a,self.event())['ids'][0]
        proposal={'schema_version':1,'proposals':[{'id':'proposal-one','source_id':source,'kind':'task',
            'subject_key':'final-phone-check','quote':self.event()['events'][0]['content'],'task_state':'pending'}]}
        result=self.inbox.propose(self.b,proposal)
        self.assertEqual(result['appended'],1)
        facts=derive_facts(self.events)
        self.assertEqual(len(facts),1);self.assertEqual(facts[0].author,'agent-b')
        self.assertEqual(facts[0].status.value,'proposed');self.assertEqual(facts[0].evidence_ids,(source,))
        self.assertEqual(self.events[0].metadata['member_id'],'agent-a')
        before=self.workspace.state_path.read_bytes()
        self.assertEqual(self.inbox.propose(self.b,proposal)['appended'],0)
        for change in ({'quote':'not in source'},{'source_id':'missing'},{'status':'accepted'}):
            bad=json.loads(json.dumps(proposal));bad['proposals'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.inbox.propose(self.b,bad)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
        self.assertEqual(self.workspace.load_review_state()[2][0]['fact_id'],'existing')

    def test_config_auth_and_public_status_do_not_leak_credentials(self):
        for value in (None,'','Bearer wrong'):
            with self.assertRaises(PermissionError):self.inbox.authenticate(value)
        public=json.dumps(self.inbox.public_status())
        self.assertNotIn('a'*40,public);self.assertNotIn('b'*40,public)
        for change in ({'project_path':str(self.project.parent)},{'owner_id':'agent-a'},{'owner_id':'a'*40}):
            config={**self.config,**change}
            with self.assertRaises(ValueError):self.module.AgentInbox(config,self.workspace,self.events)

    def test_failed_persistence_leaves_memory_and_reviews_unchanged(self):
        before=self.workspace.state_path.read_bytes()
        with patch.object(self.workspace,'save',side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):self.inbox.append_events(self.a,self.event())
        self.assertEqual(self.events,[]);self.assertEqual(self.workspace.state_path.read_bytes(),before)

    def test_agent_tool_test_and_file_reports_keep_report_attribution(self):
        for kind in ('test_result','tool_result','tool_call','file_change'):
            source=self.inbox.append_events(self.a,self.event(id=kind,type=kind))['ids'][0]
            self.inbox.propose(self.b,{'schema_version':1,'proposals':[{'id':kind,'source_id':source,
                'kind':'task','subject_key':kind,'quote':self.event()['events'][0]['content']}]})
        self.assertTrue(all(f.claim_type=='assistant_report' for f in derive_facts(self.events)))

    def test_native_execution_quote_keeps_execution_source_category(self):
        from dataclasses import replace
        from project_compass.candidate_extraction import source_claim_type
        self.inbox.append_events(self.a,self.event(type='tool_result'))
        native=replace(self.events[0],metadata={'capture':'native-public-exec-pairs-v1'})
        self.assertEqual(source_claim_type(native),'execution_record')

    def test_persistent_config_references_environment_and_rejects_inline_secrets(self):
        import os
        path=Path(self.temp.name)/'access.json'
        path.write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):self.module.AgentInbox.from_file(path,self.workspace,self.events)
        config=json.loads(json.dumps(self.config))
        for index,client in enumerate(config['clients']):
            del client['token'];client['token_env']='PROJECT_COMPASS_TEST_AGENT_'+str(index)
        path.write_text(json.dumps(config))
        with patch.dict(os.environ,{'PROJECT_COMPASS_TEST_AGENT_0':'a'*40,'PROJECT_COMPASS_TEST_AGENT_1':'b'*40}):
            inbox=self.module.AgentInbox.from_file(path,self.workspace,self.events)
        self.assertEqual(inbox.authenticate('Bearer '+'a'*40).member_id,'agent-a')
