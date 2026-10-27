"""Local, rebuildable projection using the pinned official Meterless reference."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock

from .fact_engine import derive_facts
from .models import CandidateFact, FactKind, FactStatus

PIN = '1742dec56a66e58eef9dda2d2b865823758ca8cd'
ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / '.tools/meterless/engines/world-model/reference'


class MeterlessUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MemoryResult:
    facts: list[CandidateFact]
    status: dict
    support_by_fact: dict[str, list[str]]


class MeterlessMemory:
    def __init__(self, directory, project_path, *, reference_root=REFERENCE):
        self.directory = Path(directory).resolve()
        self.reference_root = Path(reference_root).resolve()
        self.namespace = 'project-' + hashlib.sha256(str(Path(project_path).resolve()).encode()).hexdigest()[:20]
        self.storage_path = self.directory / '.world' / f'{self.namespace}.json'
        self._lock = RLock()

    def query(self, events, fact_reviews, confirmed_relations) -> MemoryResult:
        with self._lock:
            node = shutil.which('node')
            cli = self.reference_root / 'node_modules/tsx/dist/cli.mjs'
            if not node or not cli.is_file():
                raise MeterlessUnavailable('Meterless is unavailable; run scripts/setup_meterless.ps1')
            try:
                head = subprocess.run(['git', '-C', str(self.reference_root), 'rev-parse', 'HEAD'],
                                      capture_output=True, text=True, timeout=10, check=True).stdout.strip()
                clean = subprocess.run(['git', '-C', str(self.reference_root), 'diff', '--quiet', PIN, '--',
                                        'src', 'package.json', 'package-lock.json'], timeout=10).returncode == 0
                if head != PIN or not clean:
                    raise MeterlessUnavailable('Meterless source differs from the verified pin; run scripts/setup_meterless.ps1')
                facts = derive_facts(events)
                raw_facts = []
                for fact in facts:
                    raw_facts.append({**asdict(fact), 'kind': fact.kind.value, 'status': fact.status.value,
                                      'observed_at': fact.observed_at.isoformat()})
                source_ids = {eid for fact in facts for eid in fact.evidence_ids}
                source_ids.update(r.evidence_id for r in confirmed_relations if r.fact_id in {f.id for f in facts})
                payload = {'namespace': self.namespace, 'directory': str(self.directory), 'facts': raw_facts,
                           'sources': [event.to_dict() for event in events if event.id in source_ids],
                           'reviews': list(fact_reviews), 'relations': [r.to_dict() for r in confirmed_relations]}
                completed = subprocess.run([node, str(cli), str(ROOT / 'scripts/meterless_bridge.mts'),
                                            str(self.reference_root)], input=json.dumps(payload, ensure_ascii=False),
                                           capture_output=True, encoding='utf-8', timeout=30, check=True)
                output = json.loads(completed.stdout)
                retrieved = [CandidateFact(**{**item, 'kind': FactKind(item['kind']), 'status': FactStatus(item['status']),
                                             'observed_at': datetime.fromisoformat(item['observed_at']),
                                             'evidence_ids': tuple(item['evidence_ids'])}) for item in output['facts']]
                if {fact.id for fact in retrieved} != {fact.id for fact in facts}:
                    raise ValueError('memory projection has incomplete facts')
                support_by_fact = output['support_by_fact']
                for fact in retrieved:
                    if not set(fact.evidence_ids) <= set(support_by_fact[fact.id]) <= source_ids:
                        raise ValueError('memory projection has invalid supporting sources')
                return MemoryResult(retrieved, {**output['status'], 'enabled': True, 'engine': 'Meterless World Model',
                                               'commit': PIN, 'namespace': self.namespace}, support_by_fact)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                raise MeterlessUnavailable('Meterless query failed; no fallback answer was generated. Check the local reference and projection.') from exc
