from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import unittest

from project_compass.evidence_linking import build_evidence_index
from project_compass.fact_engine import derive_facts
from project_compass.native_command_trace import CAPTURE
from project_compass.sample_data import sample_events


class CommandSearchTests(unittest.TestCase):
    def event(self, command, **metadata):
        return replace(sample_events()[0],id='report',kind='tool_result',content='Captured output only.',
            metadata={'command':command,'member_id':'Alice',**metadata})

    def catalog(self,event):return build_evidence_index([event]).to_dict()['evidence'][0]

    def test_agent_command_is_searchable_without_promoting_origin_or_fact(self):
        event=self.event(['pwsh','-Command',"& run-central.ps1 -Phase unit"],
            actor_type='agent',origin='agent_submission',exit_code=0)
        row=self.catalog(event)
        self.assertIn('run-central.ps1',row.get('command_preview',''))
        self.assertEqual(row.get('command_origin'),'agent_report')
        self.assertEqual(row.get('exit_code'),0)
        self.assertEqual(derive_facts([event]),[])

    def test_native_marker_cannot_override_agent_origin(self):
        event=replace(self.event('run-checks',capture=CAPTURE,exit_code=1),kind='command_execution')
        self.assertEqual(self.catalog(event).get('command_origin'),'native_trace')
        self.assertEqual(self.catalog(event).get('exit_code'),1)
        fake=replace(event,metadata={**event.metadata,'actor_type':'agent'})
        self.assertEqual(self.catalog(fake).get('command_origin'),'agent_report')

    def test_preview_is_bounded_and_original_command_remains_unchanged(self):
        command='START '+('middle '*1000)+' END run-central.ps1'
        event=self.event(command);before=event.to_dict();row=self.catalog(event)
        preview=row.get('command_preview','')
        self.assertTrue(preview.startswith('START ') and preview.endswith('END run-central.ps1'))
        self.assertLessEqual(len(preview),1600)
        self.assertIn('…',preview)
        self.assertEqual(event.to_dict(),before)

    def test_legacy_shapes_and_unknown_exit_do_not_invent_a_run_result(self):
        for command in (None,[],{},[None],['ok',3],' '):
            with self.subTest(command=command):
                self.assertNotIn('command_preview',self.catalog(self.event(command)))
        for exit_code in (None,False,'0',0.0,1.0):
            with self.subTest(exit_code=exit_code):
                row=self.catalog(self.event('run-checks',exit_code=exit_code))
                self.assertIsNone(row.get('exit_code'))
                self.assertEqual(row.get('command_origin'),'reported')

    @unittest.skipUnless(shutil.which('node'),'Node is required for offline UI verification')
    def test_browser_command_only_query_origin_exit_and_source_drawer(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node',str(root/'tests/web_command_search_test.cjs'),
            str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
