"""Bounded single-flight candidate jobs; polling and review remain model-free."""
from threading import Event, RLock, Thread, current_thread

from .candidate_extraction import coverage_summary, focused_extraction_sources, extraction_sources, source_member
from .codex_extractor import CodexExtractor
from .extraction_progress import ExtractionProgress
from .models import EvidenceEvent


class ExtractionJobs:
    def __init__(self, workspace, events, member_id, *, extractor_factory=CodexExtractor,
                 usage_logger=None, max_batches=2, max_tokens=80000, automatic=False, interval=60, session_members=None):
        if not member_id.strip() or type(max_batches) is not int or max_batches < 1 or type(max_tokens) is not int or max_tokens < 1 or interval < 5:
            raise ValueError('explicit member and positive extraction budgets/interval required')
        self.workspace, self.events, self.member_id = workspace, events, member_id.strip()
        self.session_members = dict(session_members or {})
        self.factory, self.usage_logger = extractor_factory, usage_logger
        self.max_batches, self.max_tokens = max_batches, max_tokens
        self.automatic, self.interval = automatic, interval
        self.cache = workspace.state_path.parent / 'extraction-cache'
        self.lock = RLock()
        self.stop = Event()
        self.worker = self.scheduler = None
        self.closed = False
        self.state = 'idle'
        self.batches_used = self.known_tokens = self.unknown_usage_requests = 0
        self.auto_failed = False
        self.last_result = {}
        self.error_type = None
        self.stop_reason = None
        self.scope = {'kind': 'project'}

    def bind(self, lock):
        self.lock = lock

    def _coverage(self, focus_member=None):
        sources = focused_extraction_sources(self.events, focus_member, session_members=self.session_members)
        return coverage_summary(sources, ExtractionProgress(self.cache).load())

    def status(self):
        with self.lock:
            try:
                coverage = self._coverage()
            except (OSError, ValueError) as exc:
                coverage = {'coverage_unavailable': True, 'coverage_error_type': type(exc).__name__}
            return {'enabled': True, 'member_id': self.member_id, 'automatic': self.automatic, 'state': self.state,
                'running': bool(self.worker and self.worker.is_alive()), 'closed': self.closed,
                'batches_used': self.batches_used, 'max_batches': self.max_batches,
                'known_tokens': self.known_tokens, 'max_tokens': self.max_tokens,
                'unknown_usage_requests': self.unknown_usage_requests,
                'budget_scope': 'this server start; token threshold is checked after actual requests',
                'scope': dict(self.scope),
                'eligible_members': sorted({source_member(event, session_members=self.session_members)
                    for event in extraction_sources(self.events) if event.kind in {'user_message', 'assistant_message'}}),
                'last_result': dict(self.last_result), 'error_type': self.error_type,
                'stop_reason': self.stop_reason, **coverage}

    def _usage(self, entry):
        with self.lock:
            total = entry.get('total_tokens')
            if type(total) is int and total >= 0:
                self.known_tokens += total
            else:
                self.unknown_usage_requests += 1
            if self.usage_logger:
                self.usage_logger(entry)

    def start(self, *, automatic=False, focus_member=None):
        with self.lock:
            if self.closed or (self.worker and self.worker.is_alive()):
                return False
            if automatic and self.auto_failed:
                return False
            if self.unknown_usage_requests or self.batches_used >= self.max_batches or self.known_tokens >= self.max_tokens:
                self.state = 'stopped'
                self.stop_reason = 'unknown_usage' if self.unknown_usage_requests else 'budget_reached'
                return False
            if not self._coverage(focus_member)['pending_chars']:
                self.state, self.stop_reason = 'idle', 'no_pending_text'
                return False
            snapshot = [EvidenceEvent.from_dict(event.to_dict()) for event in self.events]
            self.batches_used += 1
            self.state, self.stop_reason, self.error_type = 'running', None, None
            self.scope = {'kind':'member','member_id':focus_member} if focus_member is not None else {'kind':'project'}
            self.worker = Thread(target=self._run, args=(snapshot, focus_member), name='compass-extraction', daemon=True)
            self.worker.start()
            return True

    def _run(self, snapshot, focus_member=None):
        try:
            options = dict(member_id=focus_member or self.member_id, usage_logger=self._usage,
                legacy_cache_dir=self.workspace.workspace_root / 'extraction-cache', timeout=120, session_members=self.session_members)
            if focus_member is not None:
                options['focus_member'] = focus_member
            provider = self.factory(**options)
            proposals = provider.extract(snapshot, self.cache)
            with self.lock:
                previous_ids = {event.id for event in self.events}
                if proposals:
                    self.workspace.save(proposals)
                self.events[:] = self.workspace.load()
                provider.acknowledge(self.events)
                self.last_result = {**provider.last_result,
                    'new_candidates': sum(event.id not in previous_ids for event in proposals)}
                self.state = 'complete'
        except Exception as exc:
            with self.lock:
                self.state, self.error_type, self.auto_failed = 'failed', type(exc).__name__, True

    def start_scheduler(self):
        if not self.automatic or self.scheduler is not None:
            return
        self.scheduler = Thread(target=self._schedule, name='compass-extraction-scheduler', daemon=True)
        self.scheduler.start()

    def _schedule(self):
        while not self.stop.is_set():
            try:
                self.start(automatic=True)
            except (OSError, ValueError) as exc:
                with self.lock:
                    self.state, self.error_type, self.auto_failed = 'failed', type(exc).__name__, True
            with self.lock:
                if self.auto_failed or self.state == 'stopped':
                    return
            if self.stop.wait(self.interval):
                return

    def wait(self, timeout=125):
        worker = self.worker
        if worker and worker is not current_thread():
            worker.join(timeout)

    def close(self):
        with self.lock:
            self.closed = True
            self.stop.set()
        if self.scheduler and self.scheduler is not current_thread():
            self.scheduler.join(2)
        self.wait()
