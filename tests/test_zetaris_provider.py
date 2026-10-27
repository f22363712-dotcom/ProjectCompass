import importlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest

from project_compass.sample_data import sample_events


class ZetarisProviderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('project_compass.zetaris_provider'), 'Zetaris provider missing')
        self.module = importlib.import_module('project_compass.zetaris_provider')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        a, b = sample_events()
        self.events = [replace(a, kind='file_snapshot'), replace(b, kind='assistant_message')]
        self.snapshot = self.module.build_snapshot(self.events)

    def runner(self, *, missing=False, corrupt=False):
        def run(args, **kwargs):
            plan = json.loads(kwargs['input'])
            rows = [row for body in self.snapshot.bodies.values() for row in json.loads(body)['records']]
            if missing:
                rows.pop()
            if corrupt:
                rows[0] = {**rows[0], 'payload_json': '{}'}
            return subprocess.CompletedProcess(args, 0, json.dumps({'protocol': 'projectcompass-zetaris-v1', 'success': True,
                'columns': ['source_key', 'payload_json', 'digest', 'family'],
                'rows': [[r[k] for k in ['source_key', 'payload_json', 'digest', 'family']] for r in rows],
                'created_sources': len(plan['sources']), 'elapsed_ms': 3}), '')
        return run

    def test_snapshot_excludes_internal_records_and_changes_with_source_content(self):
        extra = [replace(self.events[0], id='proposal', kind='fact_proposal'),
                 replace(self.events[0], id='observation', kind='file_observation'),
                 replace(self.events[1], id='control', content='<external_codex_apps_open_page>{}</external_codex_apps_open_page>')]
        self.assertEqual(self.module.build_snapshot(self.events + extra).revision, self.snapshot.revision)
        changed = replace(self.events[0], content='Changed source')
        self.assertNotEqual(self.module.build_snapshot([changed, self.events[1]]).revision, self.snapshot.revision)
        self.assertEqual(set(self.snapshot.bodies), {'messages', 'project'})

    def test_actual_returned_payloads_are_verified_and_materials_are_reconstructed(self):
        provider = self.module.ZetarisEvidenceProvider(self.root, self.root, runner=self.runner())
        result = provider.retrieve(self.snapshot, {'e1', 'e2'}, 'http://host.docker.internal:18791')
        self.assertEqual({e.id for e in result.events}, {'e1', 'e2'})
        self.assertEqual(result.receipt['source_count'], 2)
        self.assertEqual(result.receipt['family_count'], 2)
        self.assertTrue(result.receipt['source_hashes_verified'])

    def test_missing_or_corrupt_rows_fail_without_a_fallback(self):
        for options in [{'missing': True}, {'corrupt': True}]:
            with self.subTest(options=options):
                provider = self.module.ZetarisEvidenceProvider(self.root, self.root, runner=self.runner(**options))
                with self.assertRaises(self.module.ZetarisUnavailable):
                    provider.retrieve(self.snapshot, {'e1', 'e2'}, 'http://host.docker.internal:18791')

    def test_empty_selection_does_not_claim_a_query_or_start_worker(self):
        provider = self.module.ZetarisEvidenceProvider(self.root, self.root,
            runner=lambda *a, **k: self.fail('empty selection started worker'))
        result = provider.retrieve(self.snapshot, set(), 'http://host.docker.internal:18791')
        self.assertFalse(result.receipt['queried'])

    def test_timeout_is_bounded_and_not_retried(self):
        calls = []
        def timeout(*args, **kwargs):
            calls.append(1)
            raise subprocess.TimeoutExpired('worker', 90)
        provider = self.module.ZetarisEvidenceProvider(self.root, self.root, runner=timeout)
        with self.assertRaises(self.module.ZetarisUnavailable):
            provider.retrieve(self.snapshot, {'e1'}, 'http://host.docker.internal:18791')
        self.assertEqual(len(calls), 1)
