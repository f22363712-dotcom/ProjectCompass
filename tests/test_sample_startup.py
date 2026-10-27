from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

from project_compass.cli import main
from project_compass.web_server import create_dashboard_server
from project_compass.jev_provider import JevDecisionProvider


class SampleStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.envfile = self.root / 'no-credentials.env'

    def invoke(self, args, environment=None):
        output, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'USERPROFILE': str(self.root), **(environment or {})}, clear=True), redirect_stdout(output), redirect_stderr(error):
            code = main([*args, '--env-file', str(self.envfile)])
        return code, output.getvalue(), error.getvalue()

    def test_sample_environment_selects_deterministic_fixture_without_credentials(self):
        try:
            code, output, _ = self.invoke([], {'SAMPLE_MODE': 'true'})
        except SystemExit as exc:
            self.fail(f'SAMPLE_MODE did not select fixture input, parser exited {exc.code}')
        self.assertEqual(code, 0)
        answer = json.loads(output)
        self.assertEqual(answer['evidence_ids'], ['e1', 'e2'])
        self.assertEqual(len(answer['conflicts']), 1)

    def test_sample_environment_file_is_loaded_before_source_selection(self):
        self.envfile.write_text('SAMPLE_MODE=true\n', encoding='utf-8')
        try:
            code, output, _ = self.invoke([])
        except SystemExit as exc:
            self.fail(f'Local SAMPLE_MODE was ignored, parser exited {exc.code}')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['evidence_ids'], ['e1', 'e2'])

    def test_explicit_source_is_not_replaced_by_sample_environment(self):
        path = self.root / 'empty.json'
        path.write_text('[]', encoding='utf-8')
        code, output, _ = self.invoke(['--input', str(path)], {'SAMPLE_MODE': 'true'})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['evidence_ids'], [])

    def test_sample_rejects_external_calls_even_when_credentials_are_injected(self):
        def offline_provider(api_key, **kwargs):
            return JevDecisionProvider(api_key, transport=lambda *args: {'answers': {'supports': {'noul': 0.5}}})
        for flag in ('--jev', '--enable-extraction', '--auto-extract', '--codex-tools'):
            with self.subTest(flag=flag), patch('project_compass.cli.JevDecisionProvider', side_effect=offline_provider):
                code, output, error = self.invoke(['--sample', '--evidence-index', flag], {'TYPESAFE_API_KEY': 'synthetic-test-only'})
            self.assertEqual(code, 2)
            self.assertEqual(output, '')
            self.assertIn('sample', error.casefold())

    def test_missing_source_still_fails_when_sample_mode_is_disabled(self):
        with self.assertRaises(SystemExit) as caught:
            self.invoke([], {'SAMPLE_MODE': 'false'})
        self.assertEqual(caught.exception.code, 2)

    def test_sample_web_keeps_owner_review_across_restart_and_labels_synthetic_data(self):
        results = []

        def serve(events, **kwargs):
            kwargs['port'] = 0
            server = create_dashboard_server(events, **kwargs)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_address[1]}'
            try:
                with urllib.request.urlopen(base + '/api/state', timeout=5) as response:
                    state = json.load(response)
                self.assertTrue(state['sample_mode'])
                self.assertEqual(set(state['personal']), {'dev-a', 'dev-b'})
                self.assertEqual(len(state['team']['conflicts']), 1)
                if not results:
                    fact = next(f for f in state['team']['facts'] if f['value'].endswith('[active]'))
                    request = urllib.request.Request(base + '/api/review', method='POST',
                        data=json.dumps({'action': 'accept_fact', 'fact_id': fact['id'], 'reviewer': 'sample-owner'}).encode())
                    with urllib.request.urlopen(request, timeout=5) as response:
                        state = json.load(response)
                results.append(state)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        with patch('project_compass.cli.run_dashboard', side_effect=serve):
            for _ in range(2):
                code, _, error = self.invoke(['--sample', '--web', '--workspace-root', str(self.root / 'state')])
                self.assertEqual(code, 0, error)
        self.assertEqual(len(results[1]['review_history']), 1)
        self.assertEqual(sum(f['status'] == 'accepted' for f in results[1]['team']['facts']), 1)
