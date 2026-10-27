from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from project_compass.candidate_extraction import current_file_snapshots, validate_candidates
from project_compass.dashboard import build_dashboard_state
from project_compass.fact_engine import derive_facts
from project_compass.investigator import investigate
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events


class SourceCurrentnessTests(unittest.TestCase):
    def setUp(self):
        self.source = replace(sample_events()[0], id='file-v1', author='scanner', kind='file_snapshot',
            content='Verify final candidate v1 on the phone.', metadata={'source_ref': 'docs/review.md'})
        self.proposals = validate_candidates([self.source], {'candidates': [{'source_id': self.source.id,
            'kind': 'task', 'subject_key': 'phone-verification', 'quote': self.source.content, 'task_state': 'pending'}]})
        self.events = [self.source, *self.proposals]
        self.fact = derive_facts(self.events)[0]
        self.reviews = [{'action': 'accept_fact', 'subject_key': self.fact.subject_key, 'fact_id': self.fact.id, 'reviewer': 'owner'}]
        self.new = replace(self.source, id='file-v2', content='Candidate v2 is ready for a new review.', observed_at=self.source.observed_at + timedelta(days=1))

    def test_replaced_file_preserves_owner_history_but_is_not_current_work(self):
        events = [*self.events, self.new]
        board = build_dashboard_state(events, fact_reviews=self.reviews)
        fact = board['team']['facts'][0]
        self.assertEqual(fact['status'], 'accepted')
        self.assertEqual(fact['source_state']['status'], 'superseded')
        self.assertEqual(fact['source_state']['replacement_ids'], [self.new.id])
        answer = investigate(events, '为什么是当前状态，下一步做什么？', self.reviews).to_dict()
        self.assertNotIn(self.source.content, answer['active_tasks'])
        self.assertNotEqual(answer['next_action'], self.source.content)
        self.assertTrue(answer['source_warnings'])
        self.assertIn(self.new.id, answer['evidence_ids'])
        self.assertNotIn('推进', answer['conclusion'])

    def test_complete_scan_absence_is_append_only_and_return_restores_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = ProjectWorkspace(root / 'state', root)
            workspace.save(self.events)
            workspace.save_review_state([], self.reviews, self.reviews)
            workspace.save([], complete_scan=True)
            absent = workspace.load()
            self.assertIn(self.source.id, {e.id for e in absent})
            self.assertEqual(current_file_snapshots(absent), [])
            fact = build_dashboard_state(absent, fact_reviews=self.reviews)['team']['facts'][0]
            self.assertEqual(fact['source_state']['status'], 'unavailable')
            workspace.save([], complete_scan=True)
            self.assertEqual(len(workspace.load()), len(absent))
            workspace.save([self.source], complete_scan=True)
            self.assertEqual([e.id for e in current_file_snapshots(workspace.load())], [self.source.id])
            self.assertEqual(workspace.load_review_state()[2], self.reviews)

    def test_proposal_or_partial_save_never_infers_file_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = ProjectWorkspace(root / 'state', root)
            workspace.save(self.events)
            workspace.save([])
            self.assertEqual([e.id for e in current_file_snapshots(workspace.load())], [self.source.id])

    def test_stale_acceptance_cannot_resolve_current_conflict(self):
        payload = {'candidates': [{'source_id': self.new.id, 'kind': 'task', 'subject_key': 'phone-verification',
            'quote': self.new.content, 'task_state': 'pending'}]}
        events = [*self.events, self.new, *validate_candidates([self.new], payload)]
        answer = investigate(events, '下一步做什么？', self.reviews).to_dict()
        self.assertNotIn(self.source.content, answer['active_tasks'])
        self.assertIsNone(answer['next_action_status'])
        self.assertIn('冲突', answer['next_action'])
