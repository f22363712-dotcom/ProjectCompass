from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import shutil
import subprocess
import unittest

from project_compass.candidate_extraction import validate_candidates
from project_compass.fact_engine import derive_facts
from project_compass.investigator import investigate
from project_compass.projections import build_views
from project_compass.sample_data import sample_events


class SourceTimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for the offline UI harness')
    def test_browser_source_time_and_next_action_rendering(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node',str(root/'tests/web_source_time_test.cjs'),
            str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def records(self, identity, text, *, member='Alice', kind='task', subject=None,
                source_kind='assistant_message', days=0):
        source=replace(sample_events()[0], id=identity, session_id='session-'+member,
            author='codex', kind=source_kind, content=text,
            observed_at=sample_events()[0].observed_at+timedelta(days=days),
            metadata={'member_id':member,'role':'assistant','source_ref':identity+'.jsonl:1'})
        proposal=validate_candidates([source],{'candidates':[{'source_id':identity,
            'kind':kind,'subject_key':subject or identity,'quote':text,
            'task_state':'pending' if kind=='task' else 'unknown'}]})
        return [source,*proposal]

    def test_views_and_conflict_sides_preserve_original_source_time_and_session(self):
        events=self.records('old','Install candidate A.',subject='release')+self.records(
            'new','Do not install candidate B yet.',member='Bob',days=3,subject='release')
        before=[e.to_dict() for e in events];views=build_views(events)
        sources={e.id:e for e in events}
        for fact in views['team']['facts']:
            source=sources[fact['evidence_ids'][0]]
            for item in (fact,views['personal'][fact['author']]['facts'][0]):
                self.assertEqual(item.get('observed_at'),source.observed_at.isoformat())
                self.assertEqual(item.get('source_kind'),source.kind)
                self.assertEqual(item.get('session_id'),source.session_id)
        for side in views['team']['conflicts'][0]['sides']:
            source=sources[side['evidence_ids'][0]]
            self.assertEqual(side.get('observed_at'),source.observed_at.isoformat())
            self.assertEqual(side.get('source_kind'),source.kind)
        self.assertEqual([e.to_dict() for e in events],before)

    def test_document_time_keeps_snapshot_kind_instead_of_implying_publication(self):
        events=self.records('review','Candidate B not installed.',source_kind='file_snapshot')
        fact=build_views(events)['team']['facts'][0]
        self.assertEqual(fact.get('source_kind'),'file_snapshot')
        self.assertEqual(fact.get('observed_at'),events[0].observed_at.isoformat())

    def test_unconfirmed_next_action_has_its_own_source_and_currentness_gap(self):
        events=self.records('old','Install candidate A.')
        answer=investigate(events,'Alice 下一步做什么？').to_dict()
        basis=answer.get('next_action_basis')
        self.assertIsNotNone(basis)
        self.assertEqual(basis['quote'],answer['next_action'])
        self.assertEqual(basis['evidence_ids'],['old'])
        self.assertEqual(basis['observed_at'],events[0].observed_at.isoformat())
        self.assertEqual(basis['source_kind'],'assistant_message')
        self.assertEqual(basis['member_id'],'Alice')
        self.assertEqual(basis['status'],'proposed')
        self.assertIsNone(basis['reviewed_at'])
        self.assertTrue(any('当前版本' in gap for gap in answer['evidence_gaps']))

    def test_action_basis_is_not_assumed_to_be_first_or_visible_finding(self):
        events=self.records('goal','Validate the project.',kind='goal')
        for i in range(3):events+=self.records('risk'+str(i),'Risk '+str(i),kind='assumption',days=i+1)
        events+=self.records('todo','Check candidate B.',days=5)
        goal=derive_facts(events)[0]
        review={'action':'accept_fact','fact_id':goal.id,'subject_key':goal.subject_key,
                'reviewer':'owner','observed_at':'2026-10-25T07:00:00+00:00'}
        answer=investigate(events,'为什么是当前状态，下一步做什么？',[review]).to_dict()
        self.assertNotIn('todo',[eid for f in answer['findings'] for eid in f['evidence_ids']])
        basis=answer.get('next_action_basis')
        self.assertIsNotNone(basis)
        self.assertEqual(basis['evidence_ids'],['todo'])
        self.assertEqual(basis['quote'],'Check candidate B.')

    def test_accepted_basis_keeps_source_time_separate_from_review_time(self):
        events=self.records('review','Check final candidate B.',source_kind='file_snapshot')
        fact=derive_facts(events)[0]
        review={'action':'accept_fact','fact_id':fact.id,'subject_key':fact.subject_key,
                'reviewer':'owner','observed_at':'2026-10-25T07:00:00+00:00'}
        answer=investigate(events,'项目下一步做什么？',[review]).to_dict();basis=answer.get('next_action_basis')
        self.assertIsNotNone(basis)
        self.assertEqual(basis['status'],'accepted')
        self.assertEqual(basis['reviewed_at'],review['observed_at'])
        self.assertEqual(basis['observed_at'],events[0].observed_at.isoformat())
        self.assertEqual(basis['source_kind'],'file_snapshot')
        self.assertEqual(basis['reviewed_by'],'owner')

    def test_member_basis_does_not_inherit_another_members_owner_confirmation(self):
        alice=self.records('a','Check candidate A.')
        bob=self.records('b','Check candidate B.',member='Bob',days=3)
        fact=derive_facts(bob)[0]
        review={'action':'accept_fact','fact_id':fact.id,'subject_key':fact.subject_key,
                'reviewer':'owner','observed_at':'2026-10-25T07:00:00+00:00'}
        answer=investigate(alice+bob,'Alice 下一步做什么？',[review]).to_dict()
        basis=answer['next_action_basis']
        self.assertEqual(basis['evidence_ids'],['a'])
        self.assertEqual(basis['member_id'],'Alice')
        self.assertIsNone(basis['reviewed_by'])
        self.assertEqual(basis['status'],'proposed')

    def test_conflict_raw_and_changed_source_generic_actions_have_no_basis(self):
        left=self.records('a','Install A.',subject='release');right=self.records(
            'b','Pause A.',member='Bob',subject='release')
        conflict=investigate(left+right,'下一步做什么？').to_dict()
        self.assertIsNone(conflict.get('next_action_basis'))
        sources={e.id:e for e in left+right}
        for side in conflict['conflicts'][0]['sides']:
            source=sources[side['evidence_ids'][0]]
            self.assertEqual(side.get('observed_at'),source.observed_at.isoformat())
            self.assertEqual(side.get('source_kind'),source.kind)
        self.assertIsNone(investigate(left[:1],'Alice 做了什么？').to_dict().get('next_action_basis'))
        document=self.records('doc','Install A.',source_kind='file_snapshot')
        newer=replace(document[0],id='doc-v2',content='Install B.',observed_at=document[0].observed_at+timedelta(days=1))
        result=investigate([*document,newer],'项目下一步做什么？').to_dict()
        self.assertIsNone(result.get('next_action_basis'))
        self.assertNotEqual(result['next_action'],'Install A.')


if __name__=='__main__':unittest.main()
