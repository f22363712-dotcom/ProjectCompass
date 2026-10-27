"""Review failures must not become later, silently persisted owner decisions."""
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from project_compass.cli import _review_callback
from project_compass.evidence_linking import build_evidence_index
from project_compass.fact_engine import derive_facts
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events
from project_compass.web_server import create_dashboard_server


class ReviewPersistenceFailureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = Path(self.directory.name)/'synthetic-project'
        self.project.mkdir()
        self.workspace = ProjectWorkspace(Path(self.directory.name)/'state', self.project)
        self.events = sample_events()
        self.workspace.save(self.events)
        self.fact = derive_facts(self.events)[0]
        self.relation = build_evidence_index(self.events).candidate_relations[0]

    def callback(self):
        return _review_callback(self.events, self.workspace, str(self.project), [], [], [])

    def test_same_owner_action_can_retry_after_failed_persistence_exactly_once(self):
        for action, target in [('accept_fact', self.fact.id), ('confirm_relation', self.relation.id)]:
            with self.subTest(action=action):
                # Each action gets its own callback/state, just like an independent board.
                self.workspace.save_review_state([], [], [])
                callback, relations, history, reviews = self.callback()
                before = self.workspace.state_path.read_bytes()
                with patch.object(self.workspace, '_write_state', side_effect=OSError('simulated disk failure')):
                    with self.assertRaises(OSError):
                        callback(action, target, 'sample-owner')
                self.assertEqual(self.workspace.state_path.read_bytes(), before)
                state = callback(action, target, 'sample-owner')
                stored_relations, stored_history, stored_reviews = self.workspace.load_review_state()
                self.assertEqual(len(stored_history), 1)
                self.assertEqual((len(stored_relations), len(stored_reviews)),
                    (0, 1) if action == 'accept_fact' else (1, 0))
                self.assertEqual((len(relations), len(history), len(reviews)),
                    (0, 1, 1) if action == 'accept_fact' else (1, 1, 0))
                self.assertEqual(len(state['review_history']), 1)
                self.assertEqual([e.to_dict() for e in self.workspace.load()], [e.to_dict() for e in self.events])
                with self.assertRaises(ValueError):
                    callback(action, target, 'sample-owner')

    def test_failed_fact_acceptance_cannot_be_committed_by_a_different_later_action(self):
        callback, _, _, _ = self.callback()
        with patch.object(self.workspace, '_write_state', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                callback('accept_fact', self.fact.id, 'sample-owner')
        state = callback('confirm_relation', self.relation.id, 'sample-owner')
        self.assertEqual(state['fact_reviews'], [])
        self.assertEqual([r['action'] for r in state['review_history']], ['confirm_relation'])
        self.assertEqual(self.workspace.load_review_state()[2], [])

    def test_failed_atomic_replace_preserves_prior_reviews_and_caller_lists(self):
        callback, relations, history, reviews = self.callback()
        callback('accept_fact', self.fact.id, 'sample-owner')
        saved = self.workspace.state_path.read_bytes()
        previous = (list(relations), list(history), list(reviews))
        with patch.object(Path, 'replace', side_effect=OSError('simulated replace failure')):
            with self.assertRaises(OSError):
                callback('confirm_relation', self.relation.id, 'sample-owner')
        self.assertEqual(self.workspace.state_path.read_bytes(), saved)
        self.assertEqual((relations, history, reviews), previous)
        self.assertTrue(self.workspace.state_path.with_suffix('.json.tmp').exists())
        callback('confirm_relation', self.relation.id, 'sample-owner')
        self.assertEqual((len(relations), len(history), len(reviews)), (1, 2, 1))
        self.assertEqual(self.workspace.load_review_state()[2], previous[2])

    def test_http_save_failure_returns_sanitized_503_and_allows_explicit_retry(self):
        callback, relations, history, reviews = self.callback()
        server = create_dashboard_server(self.events, port=0, project_path=str(self.project),
            confirmed_relations=relations, review_history=history, fact_reviews=reviews,
            review_callback=callback)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        body = json.dumps({'action':'accept_fact', 'fact_id':self.fact.id, 'reviewer':'sample-owner'}).encode()
        request = lambda: Request(base+'/api/review', data=body, headers={'Content-Type':'application/json'})
        try:
            with patch.object(self.workspace, '_write_state', side_effect=OSError('PRIVATE_DISK_PATH')):
                with self.assertRaises(HTTPError) as failed:
                    urlopen(request(), timeout=5)
                self.assertEqual(failed.exception.code, 503)
                error = json.loads(failed.exception.read())
                self.assertNotIn('PRIVATE_DISK_PATH', error['error'])
            with urlopen(base+'/api/state', timeout=5) as response:
                self.assertEqual(json.load(response)['fact_reviews'], [])
            with urlopen(request(), timeout=5) as response:
                state = json.load(response)
            self.assertEqual(len(state['fact_reviews']), 1)
            self.assertEqual(len(state['review_history']), 1)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == '__main__':
    unittest.main()
