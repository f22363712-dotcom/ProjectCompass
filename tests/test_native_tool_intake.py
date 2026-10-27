from pathlib import Path
import tempfile
import json
import unittest

from project_compass.native_codex import NativeCodexIntake
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events

THREAD='11111111-1111-4111-8111-111111111111'


class NativeToolIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'project';self.project.mkdir()
        self.sessions=self.root/'sessions';self.sessions.mkdir()
        self.path=self.sessions/f'rollout-{THREAD}.jsonl'
        self.header={'type':'session_meta','payload':{'id':THREAD,'cwd':str(self.project)}}
        self.message={'type':'response_item','timestamp':'2026-10-25T08:00:00Z','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'Keep phone verification pending.'}]}}
        self.workspace=ProjectWorkspace(self.root/'state',self.project)
        self.events=[]

    def pair(self,call='one',text='text(await tools.exec_command({cmd:"python -m unittest"}));'):
        return [{'type':'response_item','timestamp':'2026-10-25T08:01:00Z','payload':{'type':'custom_tool_call','call_id':call,'name':'exec','input':text}},
                {'type':'response_item','timestamp':'2026-10-25T08:02:00Z','payload':{'type':'custom_tool_call_output','call_id':call,'output':[{'type':'input_text','text':'{"exit_code":0,"output":"3 tests passed"}'}]}}]

    def write(self,rows):
        self.path.write_text('\n'.join(json.dumps(r) for r in [self.header,*rows])+'\n',encoding='utf8')

    def reader(self,**kwargs):
        return NativeCodexIntake(self.sessions,self.project,THREAD,'owner',**kwargs)

    def test_opt_in_capture_is_append_only_and_restart_idempotent(self):
        self.write([self.message,*self.pair()])
        self.reader().sync(self.workspace,self.events)
        self.assertEqual(len(self.events),1)
        self.workspace.save_review_state([], [{'reviewer':'owner'}], [{'action':'accept_fact','fact_id':'existing','reviewer':'owner'}])
        reader=self.reader(include_tools=True)
        status=reader.sync(self.workspace,self.events)
        self.assertEqual(status['new_messages'],0)
        self.assertEqual(status['new_tool_records'],2)
        self.assertEqual(len(self.events),3)
        self.assertEqual(self.workspace.load_review_state()[2][0]['fact_id'],'existing')
        before=self.workspace.state_path.read_bytes()
        self.assertEqual(reader.sync(self.workspace,self.events)['new_tool_records'],0)
        self.reader(include_tools=True).sync(self.workspace,self.events)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
        self.write([self.message,*self.pair(),*self.pair('two')])
        status=reader.sync(self.workspace,self.events)
        self.assertEqual(status['new_tool_records'],2)
        self.assertTrue(status['updated'])

    def test_partial_and_conflicting_tool_records_preserve_saved_state(self):
        rows=[self.message,*self.pair()]
        self.write(rows)
        reader=self.reader(include_tools=True);reader.sync(self.workspace,self.events)
        before=self.workspace.state_path.read_bytes()
        self.write([*rows,self.pair('one',text='text(await tools.exec_command({cmd:"different"}));')[0]])
        with self.assertRaises(ValueError):reader.sync(self.workspace,self.events)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
        self.write([*rows,self.pair('pending')[0]])
        with self.path.open('a',encoding='utf8') as handle:handle.write('{"type":')
        status=reader.sync(self.workspace,self.events)
        self.assertEqual(status['pending_tail_lines'],1)
        self.assertEqual(status['pending_tool_calls'],1)
        self.assertEqual(status['new_tool_records'],0)

    def test_disabling_new_capture_keeps_previously_stored_trace(self):
        self.write([self.message,*self.pair()])
        self.reader(include_tools=True).sync(self.workspace,self.events)
        before=self.workspace.state_path.read_bytes()
        status=self.reader().sync(self.workspace,self.events)
        self.assertFalse(status['tools_enabled'])
        self.assertEqual(status['stored_tool_records'],2)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)

    def test_persisted_call_identity_cannot_be_rewritten_even_when_filtered(self):
        self.write([self.message,*self.pair()])
        reader=self.reader(include_tools=True);reader.sync(self.workspace,self.events)
        before=self.workspace.state_path.read_bytes();signature=reader.signature
        for text in ['text(await tools.exec_command({cmd:"different"}));',
                     'text(await tools.exec_command({cmd:"Get-Content .env"}));']:
            with self.subTest(text=text):
                self.write([self.message,*self.pair(text=text)])
                with self.assertRaisesRegex(ValueError,'conflicting native tool record identity'):
                    reader.sync(self.workspace,self.events)
                self.assertEqual(self.workspace.state_path.read_bytes(),before)
                self.assertEqual(reader.signature,signature)
                with self.assertRaises(ValueError):self.reader(include_tools=True).sync(self.workspace,self.events)
        rows=self.pair();rows[1]['payload']['output']=[{'type':'input_image','image_url':'data:synthetic'}]
        self.write([self.message,*rows])
        with self.assertRaises(ValueError):reader.sync(self.workspace,self.events)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)

    def test_typed_command_append_pending_restart_and_review_retention(self):
        def command(status='completed',output='observed command output',code=0):
            return {'type':'event_msg','timestamp':'2026-10-25T08:03:00Z','payload':{'type':'item_completed','item':{
                'type':'CommandExecution','id':'exec-typed','command':['python','-m','unittest'],
                'cwd':self.project.as_uri(),'source':'unified_exec_startup','status':status,
                'exit_code':code,'aggregated_output':output,'duration':{'secs':1,'nanos':0}}}}
        rows=[self.message,*self.pair()]
        self.write([*rows,command('in_progress',code=None)])
        reader=self.reader(include_tools=True);status=reader.sync(self.workspace,self.events)
        self.assertEqual(status.get('pending_native_commands'),1)
        self.workspace.save_review_state([],[],[{'action':'accept_fact','fact_id':'existing','reviewer':'owner'}])
        # Native rollouts append a completion; the pending line is not rewritten.
        with self.path.open('a',encoding='utf8') as handle:handle.write(json.dumps(command())+'\n')
        status=reader.sync(self.workspace,self.events)
        self.assertEqual(status['new_native_commands'],1)
        self.assertEqual(len(self.events),4)
        before=self.workspace.state_path.read_bytes()
        status=self.reader(include_tools=True).sync(self.workspace,self.events)
        self.assertEqual(status['new_native_commands'],0)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
        self.assertEqual(self.workspace.load_review_state()[2][0]['fact_id'],'existing')
        self.write([*rows,command(output='API_KEY=synthetic-only')])
        with self.assertRaises(ValueError):reader.sync(self.workspace,self.events)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
