from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest

from project_compass.candidate_extraction import validate_candidates
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events


class ExtractionJobTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('project_compass.extraction_jobs'))
        from project_compass.extraction_jobs import ExtractionJobs
        self.Jobs = ExtractionJobs
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = ProjectWorkspace(self.root / 'state', self.root)
        self.source = replace(sample_events()[0], id='public-job', kind='assistant_message',
            content='Verify the new candidate on the phone.', metadata={'role':'assistant','member_id':'dev-a'})
        self.workspace.save([self.source])
        self.events = self.workspace.load()

    def factory(self, entered, release, *, fail=False, unknown=False, cached=False):
        test = self
        class Provider:
            last_result = {'cache_reused': cached}
            def __init__(self, **kwargs):
                self.logger = kwargs['usage_logger']
            def extract(self, events, cache_dir):
                entered.set()
                if not release.wait(4):
                    raise AssertionError('test release missing')
                if not cached:
                    self.logger({'model':'gpt-6.1-sol','input_tokens':None if unknown else 20,
                        'output_tokens':None if unknown else 5,'total_tokens':None if unknown else 25,
                        'cached_input_tokens':None if unknown else 0,'latency_ms':1,'success':not fail})
                if fail:
                    raise ValueError('synthetic failed model response')
                return validate_candidates(events, {'candidates':[{'source_id':test.source.id,'kind':'task',
                    'subject_key':'phone','quote':test.source.content,'task_state':'pending'}]}, 'dev-a')
            def acknowledge(self, persisted):
                test.acknowledged = [e.id for e in persisted]
        return Provider

    def jobs(self, **kwargs):
        jobs = self.Jobs(self.workspace, self.events, 'dev-a', **kwargs)
        self.addCleanup(jobs.close)
        return jobs

    def test_single_flight_commits_against_latest_sources_and_owner_ledger(self):
        entered, release = threading.Event(), threading.Event()
        jobs = self.jobs(extractor_factory=self.factory(entered, release))
        self.assertTrue(jobs.start())
        self.assertTrue(entered.wait(2))
        self.assertFalse(jobs.start())
        newer = replace(self.source, id='arrived-during-model', content='New public statement.')
        with jobs.lock:
            self.workspace.save([newer])
            review = {'action':'accept_fact','fact_id':'previous-fact','reviewer':'owner'}
            self.workspace.save_review_state([], [review], [review])
            self.events[:] = self.workspace.load()
        release.set()
        jobs.wait(4)
        self.assertEqual(jobs.status()['state'], 'complete')
        self.assertIn(newer.id, self.acknowledged)
        self.assertEqual(self.workspace.load_review_state()[2], [review])
        self.assertEqual(jobs.status()['known_tokens'], 25)
        self.assertEqual(jobs.status()['batches_used'], 1)
        self.assertEqual(next(e for e in self.events if e.id==self.source.id), self.source)

    def test_budget_and_unknown_usage_stop_admission_without_retry(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                entered, release = threading.Event(), threading.Event()
                release.set()
                jobs = self.jobs(extractor_factory=self.factory(entered,release,fail=unknown,unknown=unknown),
                                 max_batches=1 if not unknown else 3, max_tokens=20)
                self.assertTrue(jobs.start())
                jobs.wait(4)
                self.assertFalse(jobs.start())
                self.assertEqual(jobs.status()['batches_used'],1)
                self.assertEqual(jobs.status()['unknown_usage_requests'], int(unknown))

    def test_cache_hit_uses_no_model_tokens_and_close_disables_new_jobs(self):
        entered, release = threading.Event(), threading.Event()
        release.set()
        jobs = self.jobs(extractor_factory=self.factory(entered,release,cached=True))
        jobs.start(); jobs.wait(4)
        self.assertEqual(jobs.status()['known_tokens'],0)
        self.assertTrue(jobs.status()['last_result']['cache_reused'])
        jobs.close()
        self.assertFalse(jobs.start())

    def test_no_pending_text_never_starts_provider_or_spends_admission(self):
        self.workspace.save([])
        self.events.clear()
        jobs = self.jobs(extractor_factory=lambda **kwargs:self.fail('empty input started model'))
        self.assertFalse(jobs.start())
        self.assertEqual(jobs.status()['batches_used'],0)

    def test_failure_stops_automatic_retry(self):
        entered, release = threading.Event(), threading.Event()
        release.set()
        jobs = self.jobs(extractor_factory=self.factory(entered,release,fail=True),max_batches=3)
        jobs.start(automatic=True); jobs.wait(4)
        self.assertFalse(jobs.start(automatic=True))
        self.assertEqual(jobs.status()['batches_used'],1)
