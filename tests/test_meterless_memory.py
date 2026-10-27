from __future__ import annotations

import importlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from dataclasses import replace
from pathlib import Path

from project_compass.fact_engine import derive_facts
from project_compass.investigator import investigate
from project_compass.models import EvidenceEvent, EvidenceRelation, EvidenceRelationMethod, EvidenceRelationStatus

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / '.tools/meterless/engines/world-model/reference'


@unittest.skipUnless(REFERENCE.is_dir(), 'run scripts/setup_meterless.ps1 for real-reference tests')
class MeterlessMemoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('project_compass.meterless_memory'), 'Meterless adapter is missing')
        self.adapter = importlib.import_module('project_compass.meterless_memory')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.memory = self.adapter.MeterlessMemory(self.root / 'memory', self.project)
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        self.events = [
            EvidenceEvent('a', 's-a', 'alice', 'message', 'Package pending', now, {'facts': [
                {'kind': 'task', 'subject_key': 'package', 'value': 'Package pending'}]}),
            EvidenceEvent('b', 's-b', 'bob', 'message', 'Package complete', now, {'facts': [
                {'kind': 'task', 'subject_key': 'package', 'value': 'Package complete'}]}),
            EvidenceEvent('c', 's-c', 'alice', 'test_result', 'Pending package verified', now),
        ]
        self.facts = derive_facts(self.events)

    def test_reopen_keeps_two_supports_and_owner_replacement_history(self):
        first, second = self.facts
        relation = EvidenceRelation('support-extra', first.id, 'c', 'supports', EvidenceRelationStatus.CONFIRMED,
                                    EvidenceRelationMethod.HUMAN, None, 'owner checked', 'owner')
        reviews = [{'action': 'accept_fact', 'fact_id': first.id, 'subject_key': 'package',
                    'reviewer': 'owner', 'observed_at': '2026-10-25T01:00:00+00:00'}]
        initial = self.memory.query(self.events, reviews, [relation])
        self.assertEqual({f.id for f in initial.facts}, {first.id, second.id})
        self.assertEqual(set(next(f for f in initial.facts if f.id == first.id).evidence_ids), {'a'})
        self.assertEqual(set(initial.support_by_fact[first.id]), {'a', 'c'})
        self.assertEqual(initial.status['accepted_count'], 1)
        self.assertGreater(initial.status['event_count'], 0)
        persisted = self.memory.storage_path.read_bytes()
        reopened = self.adapter.MeterlessMemory(self.root / 'memory', self.project).query(self.events, reviews, [relation])
        self.assertEqual(reopened.status['event_count'], initial.status['event_count'])
        self.assertEqual(self.memory.storage_path.read_bytes(), persisted)
        reviews.append({'action': 'accept_fact', 'fact_id': second.id, 'subject_key': 'package',
                        'reviewer': 'owner', 'observed_at': '2026-10-25T02:00:00+00:00'})
        updated = self.memory.query(self.events, reviews, [relation])
        self.assertEqual(updated.status['accepted_count'], 1)
        self.assertEqual(updated.status['review_count'], 2)
        self.assertEqual(next(f.status.value for f in updated.facts if f.id == second.id), 'accepted')
        self.assertEqual(next(f.status.value for f in updated.facts if f.id == first.id), 'conflicted')
        raw = json.loads(self.memory.storage_path.read_text(encoding='utf-8'))
        updates = [e for e in raw['events'] if e['kind'] == 'upsertEntity' and e['payload'].get('attrs', {}).get('id') == second.id]
        self.assertEqual(updates[-1]['payload']['source']['at'], reviews[-1]['observed_at'])

    def test_project_namespace_cannot_leak_previous_project_facts(self):
        self.memory.query(self.events, [], [])
        other = self.root / 'other'
        other.mkdir()
        alternate = self.adapter.MeterlessMemory(self.root / 'memory', other)
        self.assertNotEqual(alternate.storage_path, self.memory.storage_path)
        result = alternate.query([], [], [])
        self.assertEqual(result.facts, [])
        self.assertEqual(result.status['fact_count'], 0)

    def test_missing_reference_fails_without_an_attributed_fallback(self):
        missing = self.adapter.MeterlessMemory(self.root / 'memory', self.project, reference_root=self.root / 'absent')
        with self.assertRaisesRegex(self.adapter.MeterlessUnavailable, 'setup_meterless'):
            missing.query(self.events, [], [])

    def test_missing_current_source_is_rejected_even_with_an_existing_projection(self):
        fact = self.facts[0]
        relation = EvidenceRelation('extra', fact.id, 'c', 'supports', EvidenceRelationStatus.CONFIRMED,
                                    EvidenceRelationMethod.HUMAN, None, 'checked', 'owner')
        self.memory.query(self.events, [], [relation])
        with self.assertRaises(self.adapter.MeterlessUnavailable):
            self.memory.query(self.events[:2], [], [relation])

    def test_removed_support_is_not_returned_from_historical_graph_edges(self):
        fact = self.facts[0]
        relation = EvidenceRelation('extra', fact.id, 'c', 'supports', EvidenceRelationStatus.CONFIRMED,
                                    EvidenceRelationMethod.HUMAN, None, 'checked', 'owner')
        self.memory.query(self.events, [], [relation])
        result = self.memory.query(self.events, [], [])
        self.assertEqual(set(next(f for f in result.facts if f.id == fact.id).evidence_ids), {'a'})
        self.assertEqual(set(result.support_by_fact[fact.id]), {'a'})

    def test_historical_supplement_does_not_hide_a_current_message_task(self):
        now = self.events[0].observed_at
        message = replace(self.events[0], metadata={'facts': [{'kind': 'task', 'subject_key': 'package',
            'value': 'Package pending', 'quote': 'Package pending', 'task_state': 'pending'}]})
        old = EvidenceEvent('old', 'files', 'scanner', 'file_snapshot', 'v1', now,
                            {'source_ref': 'docs/task.md'})
        new = EvidenceEvent('new', 'files', 'scanner', 'file_snapshot', 'v2', now + timedelta(hours=1),
                            {'source_ref': 'docs/task.md'})
        events = [message, old, new]
        fact = derive_facts(events)[0]
        relation = EvidenceRelation('old-support', fact.id, 'old', 'supports', EvidenceRelationStatus.CONFIRMED,
                                    EvidenceRelationMethod.HUMAN, None, 'checked', 'owner')
        reviews = [{'action': 'accept_fact', 'fact_id': fact.id, 'subject_key': 'package',
                    'reviewer': 'owner', 'observed_at': '2026-10-25T02:00:00+00:00'}]
        result = self.memory.query(events, reviews, [relation])
        answer = investigate(events, '下一步做什么', reviews, [relation], memory_facts=result.facts)
        self.assertEqual(answer.next_action, 'Package pending')
        self.assertEqual(len(answer.findings), 1)
