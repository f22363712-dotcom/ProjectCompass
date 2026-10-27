import importlib
import importlib.util
import unittest
from pathlib import Path

from project_compass.native_codex import SENSITIVE


class NativeToolTraceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('project_compass.native_tool_trace'))
        self.module=importlib.import_module('project_compass.native_tool_trace')

    def collector(self,**kwargs):
        return self.module.ToolTraceCollector('thread','developer-a',SENSITIVE,**kwargs)

    def pair(self,c,call='one',input=None,output=None,timestamp='2026-10-25T08:00:00Z'):
        a={'type':'custom_tool_call','call_id':call,'name':'exec',
           'input':input or 'text(await tools.exec_command({cmd:"python -m unittest"}));',
           'internal_chat_message_metadata_passthrough':'DO_NOT_IMPORT'}
        b={'type':'custom_tool_call_output','call_id':call,'output':output if output is not None else [
            {'type':'input_text','text':'{"exit_code":0,"output":"3 tests passed"}'}],
            'internal_chat_message_metadata_passthrough':'DO_NOT_IMPORT'}
        c.add({'timestamp':timestamp,'payload':a},Path('rollout.jsonl'),2)
        c.add({'timestamp':timestamp,'payload':b},Path('rollout.jsonl'),3)

    def test_pair_has_exact_text_reciprocal_links_and_no_completion_claim(self):
        c=self.collector();self.pair(c)
        events,status=c.finish()
        self.assertEqual([e.kind for e in events],['tool_call','tool_result'])
        self.assertEqual(events[1].content,'{"exit_code":0,"output":"3 tests passed"}')
        self.assertEqual(events[0].metadata['paired_evidence_id'],events[1].id)
        self.assertEqual(events[1].metadata['paired_evidence_id'],events[0].id)
        self.assertTrue(all(e.metadata['member_id']=='developer-a' and 'facts' not in e.metadata for e in events))
        self.assertNotIn('DO_NOT_IMPORT',str([e.to_dict() for e in events]))
        self.assertEqual(status['captured_tool_pairs'],1)

    def test_sensitive_binary_unknown_and_oversized_pairs_are_excluded(self):
        c=self.collector()
        self.pair(c,'secret-input',input='text(await tools.exec_command({cmd:"Get-Content .env"}));')
        self.pair(c,'secret-output',output=[{'type':'input_text','text':'TYPESAFE_API_KEY=synthetic-only'}])
        self.pair(c,'secret-shell',input='text(await tools.exec_command({cmd:"mysql --password synthetic-only"}));')
        self.pair(c,'binary',output=[{'type':'input_image','image_url':'data:synthetic-only'}])
        self.pair(c,'unknown',input='text(await tools.get_goal({}));')
        self.pair(c,'oversize',output=[{'type':'input_text','text':'x'*48001}])
        events,status=c.finish()
        self.assertEqual(events,[])
        self.assertEqual(status['skipped_tool_sensitive'],3)
        self.assertEqual(status['skipped_tool_binary'],1)
        self.assertEqual(status['skipped_tool_unsupported'],1)
        self.assertEqual(status['skipped_tool_oversize'],1)

    def test_latest_window_and_duplicate_identity_are_deterministic(self):
        c=self.collector(max_pairs=1)
        self.pair(c,'old');self.pair(c,'new',timestamp='2026-10-25T09:00:00Z');self.pair(c,'old')
        events,status=c.finish()
        self.assertEqual(len(events),2)
        self.assertTrue(all(e.metadata['native_call_id']=='new' for e in events))
        self.assertEqual(status['older_tool_pairs_not_in_window'],1)
        other=self.collector(max_pairs=1);self.pair(other,'new',timestamp='2026-10-25T09:00:00Z')
        self.assertEqual([e.id for e in other.finish()[0]],[e.id for e in events])
        with self.assertRaises(ValueError):
            self.pair(c,'old',input='text(await tools.exec_command({cmd:"changed command"}));')

    def test_backfill_selects_oldest_unstored_pairs(self):
        c=self.collector(max_pairs=1)
        self.pair(c,'old');self.pair(c,'middle',timestamp='2026-10-25T08:30:00Z');self.pair(c,'new',timestamp='2026-10-25T09:00:00Z')
        events,status=c.finish(existing_call_ids={'new'}, backfill_limit=1)
        self.assertEqual({e.metadata['native_call_id'] for e in events},{'old'})
        self.assertEqual(status['older_tool_pairs_not_in_window'],1)

    def test_partial_pairs_wait_and_do_not_create_false_execution(self):
        c=self.collector()
        c.add({'timestamp':'2026-10-25T08:00:00Z','payload':{'type':'custom_tool_call','call_id':'pending','name':'exec',
            'input':'text(await tools.exec_command({cmd:"run"}));'}},Path('rollout.jsonl'),2)
        events,status=c.finish()
        self.assertEqual(events,[])
        self.assertEqual(status['pending_tool_calls'],1)

    def test_all_tool_references_must_be_direct_local_calls(self):
        scripts = [
            'const remote=tools.remote_service; text(await remote({}));',
            'const alias=tools; text(await alias.remote_service({}));',
            'text(await tools /* comment */ .remote_service({}));',
            'text(await tools["remote_service"]({}));',
            'text(`${await tools.remote_service({})}`);',
        ]
        for script in scripts:
            with self.subTest(script=script):
                c=self.collector()
                self.pair(c,input='text(await tools.exec_command({cmd:"echo public"}));'+script)
                events,status=c.finish()
                self.assertEqual(events,[])
                self.assertEqual(status['skipped_tool_unsupported'],1)
        c=self.collector()
        self.pair(c,input='// tools.remote_service()\ntext(await tools /* comment */ . exec_command({cmd:"echo tools.remote_service"}));')
        self.assertEqual(len(c.finish()[0]),2)
