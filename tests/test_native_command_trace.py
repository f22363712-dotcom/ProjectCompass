import importlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

from project_compass.native_codex import SENSITIVE


class NativeCommandTraceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('project_compass.native_command_trace'))
        self.module=importlib.import_module('project_compass.native_command_trace')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve()

    def collector(self,**kwargs):
        return self.module.CommandTraceCollector('thread','developer-a',self.project,SENSITIVE,**kwargs)

    def row(self,identity='exec-one',**changes):
        item={'type':'CommandExecution','id':identity,'command':['python','-m','unittest'],
              'cwd':self.project.as_uri(),'source':'unified_exec_startup','status':'completed',
              'exit_code':0,'aggregated_output':'3 tests passed\n','duration':{'secs':1,'nanos':20},
              'parsed_cmd':[{'private':'DO_NOT_IMPORT'}],'private':'DO_NOT_IMPORT'}
        item.update(changes)
        return {'type':'event_msg','timestamp':'2026-10-25T08:00:00Z','payload':{'type':'item_completed','item':item}}

    def add(self,collector,row):
        return collector.add(row,Path('rollout.jsonl'),7)

    def test_exact_public_execution_and_failed_empty_output_without_task_fact(self):
        c=self.collector();self.add(c,self.row());self.add(c,self.row('exec-failed',status='failed',exit_code=1,aggregated_output=''))
        events,status=c.finish()
        self.assertEqual(len(events),2)
        event=next(e for e in events if e.metadata['native_item_id']=='exec-one')
        self.assertEqual(event.kind,'command_execution')
        self.assertEqual(event.content,'3 tests passed\n')
        self.assertEqual(event.metadata['command'],['python','-m','unittest'])
        self.assertEqual(event.metadata['exit_code'],0)
        self.assertEqual(event.metadata['member_id'],'developer-a')
        self.assertNotIn('DO_NOT_IMPORT',str([e.to_dict() for e in events]))
        self.assertTrue(all('facts' not in e.metadata for e in events))
        self.assertEqual(status['captured_native_commands'],2)

    def test_sensitive_oversized_and_outside_project_are_not_imported(self):
        c=self.collector()
        self.add(c,self.row('secret-command',command=['cat','.env']))
        self.add(c,self.row('secret-output',aggregated_output='MY_API_KEY=synthetic-only'))
        self.add(c,self.row('outside',cwd=self.project.parent.as_uri()))
        self.add(c,self.row('outside-uri',cwd=self.project.as_uri()+'/%2e%2e'))
        self.add(c,self.row('oversized',aggregated_output='x'*48001))
        self.add(c,self.row('binary',aggregated_output='abc\x00def'))
        events,status=c.finish()
        self.assertEqual(events,[])
        self.assertEqual(status['skipped_native_command_sensitive'],2)
        self.assertEqual(status['skipped_native_command_outside_project'],2)
        self.assertEqual(status['skipped_native_command_oversize'],1)
        self.assertEqual(status['skipped_native_command_nontext'],1)

    def test_pending_unknown_reasoning_and_window_are_explicit(self):
        c=self.collector(max_events=1)
        self.add(c,self.row('old'));self.add(c,self.row('new'))
        self.add(c,self.row('pending',status='in_progress',exit_code=None))
        self.assertFalse(self.add(c,{'type':'event_msg','payload':{'type':'item_completed','item':{'type':'Reasoning','raw_content':'DO_NOT_IMPORT'}}}))
        events,status=c.finish()
        self.assertEqual(len(events),1)
        self.assertEqual(status['older_native_commands_not_in_window'],1)
        self.assertEqual(status['pending_native_commands'],1)
        other=self.collector(max_events=1);self.add(other,self.row(events[0].metadata['native_item_id']))
        self.assertEqual(other.finish()[0][0].id,events[0].id)

    def test_backfill_selects_oldest_unstored_command(self):
        c=self.collector(max_events=1)
        self.add(c,self.row('old'));self.add(c,self.row('middle'));self.add(c,self.row('new'))
        events,status=c.finish(existing_native_ids={'new','middle'}, backfill_limit=1)
        self.assertEqual({e.metadata['native_item_id'] for e in events},{'old'})
        self.assertEqual(status['older_native_commands_not_in_window'],0)

    def test_conflicts_before_filter_and_window_preserve_identity(self):
        c=self.collector();self.add(c,self.row());events=c.finish()[0]
        self.add(c,self.row())
        self.assertEqual(c.finish()[1]['duplicate_native_commands'],1)
        with self.assertRaises(ValueError):self.add(c,self.row(aggregated_output='different'))
        for changes in ({'aggregated_output':'API_KEY=synthetic-only'}, {'exit_code':1}, {'command':['different']}, {'status':'in_progress','exit_code':None}):
            other=self.collector();self.add(other,self.row(**changes))
            with self.assertRaisesRegex(ValueError,'conflicting native command identity'):
                other.validate_existing(events)

    def test_malformed_complete_command_is_rejected(self):
        for changes in ({'exit_code':True},{'command':'python -m unittest'},{'duration':{'secs':-1,'nanos':0}},{'cwd':'https://example.com/project'}):
            with self.subTest(changes=changes):
                c=self.collector();self.add(c,self.row(**changes))
                with self.assertRaises(ValueError):c.finish()

    def test_empty_output_has_a_command_preview_and_no_automatic_facts(self):
        from project_compass.evidence_linking import build_evidence_index
        c=self.collector();self.add(c,self.row(aggregated_output=''))
        events=c.finish()[0];index=build_evidence_index(events)
        self.assertIn('python',index.catalog[0].content_preview)
        self.assertEqual(index.catalog[0].classification,'execution_result')
        self.assertEqual(index.candidate_relations,())

    def test_split_secret_switches_never_enter_public_command_metadata(self):
        c=self.collector()
        for flag in ['--password','--token','--client-secret']:
            self.add(c,self.row(flag,command=['synthetic-command',flag,'synthetic-only']))
        self.add(c,self.row('shell-secret',command=['pwsh','-Command','mysql --password synthetic-only']))
        events,status=c.finish()
        self.assertEqual(events,[])
        self.assertEqual(status['skipped_native_command_sensitive'],4)

    def test_nonstring_status_and_source_fail_with_intake_value_error(self):
        for changes in ({'status':[]},{'source':{}}):
            with self.subTest(changes=changes):
                c=self.collector();self.add(c,self.row(**changes))
                with self.assertRaises(ValueError):c.finish()

    def test_copied_identity_is_screened_for_recognized_secret_markers(self):
        c=self.collector();self.add(c,self.row('API_KEY=synthetic-only'))
        events,status=c.finish()
        self.assertEqual(events,[])
        self.assertEqual(status['skipped_native_command_sensitive'],1)
