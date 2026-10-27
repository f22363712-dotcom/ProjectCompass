import json
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from types import SimpleNamespace

from project_compass.fact_engine import derive_facts
from project_compass.meterless_memory import MeterlessUnavailable
from project_compass.sample_data import sample_events
from project_compass.web_server import create_dashboard_server


class MeterlessWebTests(unittest.TestCase):
    def test_enabled_investigation_uses_memory_facts_not_local_rederivation(self):
        facts = [replace(f, value='Memory supplied task') for f in derive_facts(sample_events())]

        class Memory:
            def query(self, events, reviews, relations):
                return SimpleNamespace(facts=facts, support_by_fact={f.id: list(f.evidence_ids) for f in facts},
                                       status={'enabled': True, 'engine': 'test-boundary', 'fact_count': len(facts)})

        self.assertIn('memory', __import__('inspect').signature(create_dashboard_server).parameters,
                      'Web server has no memory integration')
        server = create_dashboard_server(sample_events(), port=0, memory=Memory())
        self.addCleanup(server.server_close)
        result = server.investigate('What next?')
        self.assertIn('Memory supplied task', json.dumps(result))
        self.assertEqual(result['memory']['engine'], 'test-boundary')

    def test_memory_failure_returns_503_without_fallback_conclusion(self):
        class Missing:
            def query(self, *args):
                raise MeterlessUnavailable('local reference unavailable')

        self.assertIn('memory', __import__('inspect').signature(create_dashboard_server).parameters)
        server = create_dashboard_server(sample_events(), port=0, memory=Missing())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/api/investigate',
                                             data=b'{"question":"why"}', method='POST')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 503)
            payload = json.load(caught.exception)
            self.assertIn('unavailable', payload['error'])
            self.assertNotIn('conclusion', payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
