import json
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace

from project_compass.sample_data import sample_events
from project_compass.web_server import create_dashboard_server
from project_compass.zetaris_provider import EvidenceRetrieval, ZetarisUnavailable


class ZetarisWebTests(unittest.TestCase):
    def test_snapshot_callback_exposes_only_requested_sources_while_board_keeps_all(self):
        test = self
        unrelated = replace(sample_events()[0], id='unrelated', kind='assistant_message',
                            content='Unrelated private working text.', metadata={'role': 'assistant'})
        class Provider:
            def retrieve(self, snapshot, wanted, base_url):
                test.assertEqual(set(snapshot.payloads), set(wanted))
                for family in snapshot.bodies:
                    code, body = test.request(base, f'/api/zetaris-sources?revision={snapshot.revision}&family={family}')
                    test.assertEqual(code, 200)
                    test.assertNotIn(unrelated.content, json.dumps(body))
                return EvidenceRetrieval([e for e in sample_events() if e.id in wanted],
                                         {'enabled': True, 'queried': True, 'source_count': len(wanted)})
        server, base = self.start(Provider())
        server.dashboard_events.append(unrelated)
        answer = server.investigate('why')
        self.assertEqual(answer['zetaris']['input_source_count'], 2)
        self.assertGreater(answer['zetaris']['input_bytes'], 0)
        self.assertEqual(server.dashboard_state()['project']['evidence_count'], 3)
        self.assertFalse(server.source_snapshots)

    def test_unrelated_source_changes_do_not_reregister_the_same_requested_snapshot(self):
        revisions = []
        class Provider:
            def retrieve(self, snapshot, wanted, base_url):
                revisions.append(snapshot.revision)
                return EvidenceRetrieval(sample_events(), {'enabled': True, 'queried': True})
        server, base = self.start(Provider())
        first = server.investigate('why')
        server.dashboard_events.append(replace(sample_events()[0], id='unrelated',
            kind='assistant_message', content='New unclaimed source.', metadata={'role': 'assistant'}))
        second = server.investigate('why')
        self.assertEqual(first['evidence_ids'], second['evidence_ids'])
        self.assertEqual(revisions[0], revisions[1])
        self.assertFalse(server.source_snapshots)

    def start(self, provider, **kwargs):
        server = create_dashboard_server(sample_events(), port=0, zetaris=provider, **kwargs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f'http://127.0.0.1:{server.server_address[1]}'

    def request(self, base, path, payload=None):
        request = urllib.request.Request(base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            method='GET' if payload is None else 'POST')
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, json.load(response)

    def test_sql_callback_reads_frozen_sources_without_investigation_lock(self):
        test = self
        class Provider:
            def retrieve(self, snapshot, wanted, base_url):
                for family in snapshot.bodies:
                    code, body = test.request(base, f'/api/zetaris-sources?revision={snapshot.revision}&family={family}')
                    test.assertEqual(code, 200)
                    test.assertEqual(body, json.loads(snapshot.bodies[family]))
                return EvidenceRetrieval([e for e in sample_events() if e.id in wanted],
                    {'enabled': True, 'queried': True, 'source_count': len(wanted)})
        server, base = self.start(Provider())
        code, answer = self.request(base, '/api/investigate', {'question': 'why'})
        self.assertEqual(code, 200)
        self.assertEqual(answer['zetaris']['source_count'], 2)
        self.assertEqual(answer['evidence_ids'], ['e1', 'e2'])
        self.assertEqual(self.request(base, '/api/zetaris-sources?revision=missing&family=project')[0], 404)
        self.assertFalse(server.source_snapshots)

    def test_refresh_can_run_during_sql_and_invalidates_stale_answer(self):
        entered, release = threading.Event(), threading.Event()
        class Provider:
            def retrieve(self, snapshot, wanted, base_url):
                entered.set()
                release.wait(4)
                return EvidenceRetrieval(sample_events(), {'enabled': True, 'queried': True})
        def refresh():
            server.dashboard_fact_reviews.append({'action': 'accept_fact', 'fact_id': 'new', 'subject_key': 'task'})
            return {'enabled': True}
        server, base = self.start(Provider(), refresh_callback=refresh)
        result = []
        query = threading.Thread(target=lambda: result.append(self.request(base, '/api/investigate', {'question': 'why'})))
        query.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request(base, '/api/refresh', {})[0], 200)
        finally:
            release.set()
            query.join(5)
        self.assertEqual(result[0][0], 409)
        self.assertNotIn('conclusion', result[0][1])
        self.assertFalse(server.source_snapshots)

    def test_enabled_failure_has_no_fallback_and_releases_snapshot(self):
        class Provider:
            def retrieve(self, *args):
                raise ZetarisUnavailable('test unavailable')
        server, base = self.start(Provider())
        code, answer = self.request(base, '/api/investigate', {'question': 'why'})
        self.assertEqual(code, 503)
        self.assertNotIn('conclusion', answer)
        self.assertFalse(server.source_snapshots)

    def test_offline_server_does_not_publish_source_snapshot_route(self):
        _, base = self.start(None)
        self.assertEqual(self.request(base, '/api/zetaris-sources?revision=x&family=project')[0], 404)
