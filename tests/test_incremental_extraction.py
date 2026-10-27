from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import importlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from project_compass.candidate_extraction import select_batch, validate_candidates
from project_compass.codex_extractor import CodexExtractor, INSTRUCTIONS
from project_compass.cli import main
from project_compass.fact_engine import derive_facts
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events


class IncrementalExtractionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('project_compass.extraction_progress'), 'range progress is missing')
        self.progress = importlib.import_module('project_compass.extraction_progress')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = replace(sample_events()[0], id='source', kind='assistant_message', content='A' * 3000 + 'Verify the tail.',
                              metadata={'role': 'assistant'})
        self.calls = []

    def provider(self, *, empty=False):
        def run(args, **kwargs):
            self.calls.append(kwargs['input'])
            records = json.loads(kwargs['input'][len(INSTRUCTIONS):])
            result = {'candidates': [] if empty else [{'source_id': records[0]['id'], 'kind': 'task',
                'subject_key': 'work', 'quote': records[0]['content'][-20:], 'task_state': 'pending'}]}
            Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(result), encoding='utf8')
            return subprocess.CompletedProcess(args, 0, json.dumps({'type': 'turn.completed',
                'usage': {'input_tokens': 100, 'output_tokens': 10, 'cached_input_tokens': 0}}), '')
        return CodexExtractor(command='offline-fake-codex', runner=run, member_id='dev')

    def test_cursor_advances_to_tail_and_new_sources_keep_priority(self):
        key = self.progress.source_key(self.source)
        fresh = replace(self.source, id='new-message', content='New work.')
        batch = select_batch([self.source, fresh], max_chars=100, cursors={key: 3000})
        self.assertEqual(batch[0].id, fresh.id)
        tail = next(e for e in batch if e.id == self.source.id)
        self.assertEqual(tail.content, 'Verify the tail.')
        self.assertEqual(tail.metadata['_extraction_window_start'], 3000)
        changed = replace(self.source, content='Changed source')
        self.assertNotEqual(self.progress.source_key(changed), key)
        self.assertEqual(select_batch([changed], cursors={key: 3000})[0].content, changed.content)

    def test_completed_messages_do_not_reserve_unused_document_budget(self):
        message = replace(self.source, content='Done')
        documents = [replace(self.source, id=f'doc-{i}', kind='file_snapshot', content=str(i) * 3000,
                     metadata={'source_ref': f'docs/{i}.md'}) for i in range(3)]
        batch = select_batch([message, *documents], max_chars=10000,
                             cursors={self.progress.source_key(message): 4})
        self.assertEqual(sum(len(e.content) for e in batch), 9000)

    def test_repeated_quote_uses_absolute_selected_window(self):
        source = replace(self.source, content='same same')
        payload = {'candidates': [{'source_id': source.id, 'kind': 'task', 'subject_key': 'repeat', 'quote': 'same'}]}
        proposal = validate_candidates([source], payload, source_windows={source.id: (5, 9)})[0]
        self.assertEqual(proposal.metadata['facts'][0]['source_start'], 5)
        self.assertEqual(derive_facts([source, proposal])[0].quote, 'same')

    def test_unacknowledged_batch_replays_cache_then_acknowledgement_advances(self):
        provider, cache = self.provider(), self.root / 'cache'
        first = provider.extract([self.source], cache)
        replay = provider.extract([self.source], cache)
        self.assertEqual(first, replay)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            provider.acknowledge([self.source])
        provider.acknowledge([self.source, *first])
        second = provider.extract([self.source, *first], cache)
        self.assertEqual(second[0].content, 'Verify the tail.')
        self.assertEqual(second[0].metadata['facts'][0]['source_start'], 3000)
        provider.acknowledge([self.source, *first, *second])
        self.assertEqual(provider.extract([self.source, *first, *second], cache), [])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(provider.last_result['pending_sources'], 0)

    def test_empty_output_can_be_acknowledged_without_inventing_facts(self):
        source = replace(self.source, content='No project claim here.   ')
        provider, cache = self.provider(empty=True), self.root / 'cache'
        self.assertEqual(provider.extract([source], cache), [])
        provider.acknowledge([source])
        self.assertEqual(provider.extract([source], cache), [])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(provider.last_result['pending_sources'], 0)

    def test_corrupt_progress_and_out_of_window_cache_stop_before_requests(self):
        provider, cache = self.provider(), self.root / 'cache'
        provider.extract([self.source], cache)
        cached = next(cache.glob('*.json'))
        value = json.loads(cached.read_text())
        value['candidates'][0]['quote'] = 'Verify the tail.'
        cached.write_text(json.dumps(value), encoding='utf8')
        with self.assertRaises(ValueError):
            provider.extract([self.source], cache)
        self.assertEqual(len(self.calls), 1)
        (cache / 'coverage-v1.json').write_text('{"version":99}', encoding='utf8')
        with self.assertRaises(ValueError):
            provider.extract([self.source], cache)
        self.assertEqual(len(self.calls), 1)

    def test_cli_save_failure_does_not_acknowledge_or_repeat_model_request(self):
        project = self.root / 'project'
        project.mkdir()
        workspace = ProjectWorkspace(self.root / 'state', project)
        workspace.save([self.source])
        original = ProjectWorkspace.save

        def fail_proposals(instance, events, metadata=None):
            if any(e.kind == 'fact_proposal' for e in events):
                raise OSError('synthetic disk failure')
            return original(instance, events, metadata)

        args = ['--open', str(project), '--workspace-root', str(self.root / 'state'),
                '--extract-codex', '--member-id', 'dev', '--env-file', str(self.root / 'absent.env')]
        with patch('project_compass.cli.CodexExtractor', side_effect=lambda **kw: self.provider()), \
             patch.object(ProjectWorkspace, 'save', fail_proposals), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 2)
        self.assertFalse((workspace.state_path.parent / 'extraction-cache/coverage-v1.json').exists())
        with patch('project_compass.cli.CodexExtractor', side_effect=lambda **kw: self.provider()), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 0)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((workspace.state_path.parent / 'extraction-cache/coverage-v1.json').is_file())

    def test_two_projects_cannot_share_inspection_progress_for_identical_source_records(self):
        source = replace(self.source, content='Project task remains pending.')
        for name in ['project-a', 'project-b']:
            project = self.root / name
            project.mkdir()
            ProjectWorkspace(self.root / 'state', project).save([source])
            args = ['--open', str(project), '--workspace-root', str(self.root / 'state'),
                    '--extract-codex', '--member-id', 'dev', '--env-file', str(self.root / 'absent.env')]
            with patch('project_compass.cli.CodexExtractor', side_effect=lambda **kw: self.provider()), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 0)
        self.assertEqual(len(self.calls), 2)

    def test_exact_legacy_cache_is_reused_without_claiming_old_coverage(self):
        import inspect
        self.assertIn('legacy_cache_dir', inspect.signature(CodexExtractor).parameters)
        legacy, current = self.root / 'legacy', self.root / 'current'
        first = self.provider().extract([self.source], legacy)
        provider = CodexExtractor(command='offline-fake-codex', runner=lambda *a, **k: self.fail('legacy cache unexpectedly called model'),
                                  legacy_cache_dir=legacy, member_id='dev')
        self.assertEqual(provider.extract([self.source], current), first)
        self.assertFalse((current / 'coverage-v1.json').exists())
        provider.acknowledge([self.source, *first])
        self.assertTrue((current / 'coverage-v1.json').exists())
        self.assertFalse((legacy / 'coverage-v1.json').exists())

    def test_retry_by_another_member_keeps_persisted_attribution_and_can_acknowledge(self):
        project = self.root / 'project'
        project.mkdir()
        workspace = ProjectWorkspace(self.root / 'state', project)
        workspace.save([self.source])
        cache = self.root / 'cache'
        first = self.provider()
        first.member_id = 'alice'
        proposals = first.extract(workspace.load(), cache)
        workspace.save(proposals)
        # Simulate save succeeding, then interruption before the range receipt.
        retry = self.provider()
        retry.member_id = 'bob'
        replay = retry.extract(workspace.load(), cache)
        self.assertEqual(replay[0].metadata['member_id'], 'alice')
        workspace.save(replay)
        retry.acknowledge(workspace.load())
        self.assertTrue((cache / 'coverage-v1.json').exists())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(workspace.load()[-1].metadata['member_id'], 'alice')
