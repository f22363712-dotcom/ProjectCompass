import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from project_compass.candidate_extraction import validate_candidates
from project_compass.cli import _review_callback
from project_compass.fact_engine import derive_facts
from project_compass.project_scanner import scan_project
from project_compass.project_workspace import ProjectWorkspace
from project_compass.web_server import create_dashboard_server


class ProjectRescanTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('project_compass.project_refresh'))
        self.module = importlib.import_module('project_compass.project_refresh')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.file = self.project / 'README.md'
        self.file.write_text('Verify candidate v1 on the phone.', encoding='utf8')
        self.workspace = ProjectWorkspace(self.root / 'state', self.project)
        sources = scan_project(self.project)
        self.source = next(e for e in sources if e.kind == 'file_snapshot')
        proposals = validate_candidates(sources, {'candidates': [{'source_id': self.source.id,
            'kind': 'task', 'subject_key': 'phone-check', 'quote': self.source.content, 'task_state': 'pending'}]})
        self.workspace.save([*sources, *proposals])
        self.events = self.workspace.load()
        fact = derive_facts(self.events)[0]
        self.review = {'action': 'accept_fact', 'fact_id': fact.id, 'subject_key': fact.subject_key, 'reviewer': 'test-owner'}
        self.workspace.save_review_state([], [self.review], [self.review])

    def serve(self, rescan=True):
        callback, relations, history, reviews = _review_callback(self.events, self.workspace, str(self.project), [], [self.review], [self.review])
        scanner = self.module.ProjectRescan(self.workspace, self.events)
        server = create_dashboard_server(self.events, port=0, project_path=str(self.project),
            review_callback=callback, confirmed_relations=relations, review_history=history, fact_reviews=reviews,
            rescan_callback=scanner.refresh if rescan else None)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}'

    def post(self, base, path, payload=None):
        request = urllib.request.Request(base + path, data=json.dumps(payload or {}).encode(), method='POST')
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, json.load(response)

    def test_rescan_updates_current_board_keeps_sources_and_review_then_reopens(self):
        base = self.serve()
        self.file.write_text('Candidate v2 needs a different review.', encoding='utf8')
        status, payload = self.post(base, '/api/rescan')
        self.assertEqual(status, 200)
        self.assertEqual(payload['project_scan']['changed_files'], 1)
        old = next(f for f in payload['team']['facts'] if f['id'] == self.review['fact_id'])
        self.assertEqual(old['status'], 'accepted')
        self.assertEqual(old['source_state']['status'], 'superseded')
        self.assertEqual(payload['review_history'], [self.review])
        self.assertEqual(next(e for e in self.workspace.load() if e.id == self.source.id), self.source)
        self.assertEqual(self.workspace.load_review_state()[2], [self.review])
        answer = self.post(base, '/api/investigate', {'question': '下一步做什么？'})[1]
        self.assertNotEqual(answer['next_action'], self.source.content)
        self.assertTrue(answer['source_warnings'])
        self.assertFalse(self.post(base, '/api/rescan')[1]['project_scan']['updated'])

    def test_missing_file_and_failed_scan_do_not_erase_history(self):
        base = self.serve()
        self.file.unlink()
        status, payload = self.post(base, '/api/rescan')
        self.assertEqual(status, 200)
        self.assertEqual(payload['project_scan']['unavailable_files'], 1)
        self.assertEqual(payload['team']['facts'][0]['source_state']['status'], 'unavailable')
        before = self.workspace.state_path.read_bytes()
        self.project.rmdir()
        self.assertEqual(self.post(base, '/api/rescan')[0], 400)
        self.assertEqual(self.workspace.state_path.read_bytes(), before)

    def test_source_missing_promotion_is_rejected_and_unconfigured_route_is_explicit(self):
        base = self.serve()
        self.file.unlink()
        self.post(base, '/api/rescan')
        status, _ = self.post(base, '/api/review', {'action': 'accept_fact', 'fact_id': self.review['fact_id'], 'reviewer': 'test-owner'})
        self.assertEqual(status, 400)
        self.assertEqual(self.post(self.serve(rescan=False), '/api/rescan')[0], 409)

    def test_bad_registry_cannot_commit_disk_state_ahead_of_live_events(self):
        before = self.workspace.state_path.read_bytes()
        events_before = [e.to_dict() for e in self.events]
        self.file.write_text('Changed source that must not be partially committed.', encoding='utf8')
        self.workspace.registry_path.write_text('{invalid', encoding='utf8')
        with self.assertRaises(ValueError):
            self.module.ProjectRescan(self.workspace, self.events).refresh()
        self.assertEqual(self.workspace.state_path.read_bytes(), before)
        self.assertEqual([e.to_dict() for e in self.events], events_before)

    def test_registry_write_failure_preserves_canonical_file_and_live_events(self):
        before = self.workspace.state_path.read_bytes()
        events_before = [e.to_dict() for e in self.events]
        self.file.write_text('Changed source with a simulated registry permission error.', encoding='utf8')
        write = Path.write_text
        def unavailable_registry(path, *args, **kwargs):
            if path.name.startswith('registry.json'):
                raise PermissionError('synthetic registry permission failure')
            return write(path, *args, **kwargs)
        with patch.object(Path, 'write_text', unavailable_registry), self.assertRaises(PermissionError):
            self.module.ProjectRescan(self.workspace, self.events).refresh()
        self.assertEqual(self.workspace.state_path.read_bytes(), before)
        self.assertEqual([e.to_dict() for e in self.events], events_before)
