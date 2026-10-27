from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
from urllib.parse import urlencode

from .models import EvidenceEvent
from .native_codex import is_platform_control
from .zetaris_worker import PROTOCOL, build_select


def digest(value):
    return sha256(value.encode('utf8')).hexdigest()


@dataclass(frozen=True)
class EvidenceSnapshot:
    revision: str
    bodies: dict
    payloads: dict
    families: dict


def build_snapshot(events):
    payloads, families = {}, {}
    rows = {'messages': [], 'project': []}
    for event in sorted(events, key=lambda event: event.id):
        if event.kind in {'fact_proposal', 'file_observation', 'reasoning', 'analysis'} or is_platform_control(event.content, kind=event.kind):
            continue
        payload = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if event.id in payloads:
            if payloads[event.id] != payload:
                raise ValueError('source identity has conflicting payloads')
            continue
        family = 'messages' if event.kind.endswith('_message') or event.metadata.get('role') in {'user', 'assistant'} else 'project'
        payloads[event.id], families[event.id] = payload, family
        rows[family].append({'source_key': digest(event.id), 'payload_json': payload, 'digest': digest(payload), 'family': family})
    bodies = {family: json.dumps({'records': items}, ensure_ascii=False, separators=(',', ':')) for family, items in rows.items()}
    revision = digest(json.dumps(bodies, sort_keys=True, ensure_ascii=False))
    return EvidenceSnapshot(revision, MappingProxyType(bodies), MappingProxyType(payloads), MappingProxyType(families))


class ZetarisUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceRetrieval:
    events: list[EvidenceEvent]
    receipt: dict


class ZetarisEvidenceProvider:
    def __init__(self, bundle, project_path, *, runner=None, timeout=90):
        self.bundle = Path(bundle).resolve()
        self.database = 'pc_' + digest(str(Path(project_path).resolve()))[:16]
        self.runner = runner or subprocess.run
        self.timeout = timeout

    def retrieve(self, snapshot, evidence_ids, base_url):
        wanted = set(evidence_ids)
        receipt = {'enabled': True, 'engine': 'Zetaris JDBC', 'queried': bool(wanted), 'snapshot': snapshot.revision}
        if not wanted:
            return EvidenceRetrieval([], {**receipt, 'source_count': 0, 'family_count': 0})
        try:
            if not wanted <= snapshot.payloads.keys():
                raise ValueError('required source is absent')
            families = sorted({snapshot.families[eid] for eid in wanted})
            sources = []
            for family in families:
                endpoint = base_url + '/api/zetaris-sources?' + urlencode({'revision': snapshot.revision, 'family': family})
                sources.append({'table': 'evidence_' + digest(endpoint)[:16] + '_' + family, 'endpoint': endpoint})
            plan = {'bundle': str(self.bundle), 'database': self.database, 'sources': sources,
                    'keys': sorted(digest(eid) for eid in wanted)}
            sql = build_select(plan)
            allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
                       'COMSPEC', 'PATHEXT', 'JAVA_HOME', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA'}
            environment = {k: v for k, v in os.environ.items() if k.upper() in allowed}
            environment['PYTHONIOENCODING'] = 'utf-8'
            result = self.runner([sys.executable, str(Path(__file__).with_name('zetaris_worker.py'))],
                input=json.dumps(plan), capture_output=True, encoding='utf8', timeout=self.timeout, env=environment)
            responses = []
            for line in result.stdout.splitlines():
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get('protocol') == PROTOCOL:
                    responses.append(value)
            response = responses[-1] if responses else {}
            if result.returncode or response.get('success') is not True:
                raise ValueError('JDBC worker failed')
            key_to_id = {digest(eid): eid for eid in wanted}
            found = {}
            for values in response['rows']:
                row = dict(zip(response['columns'], values, strict=True))
                eid = key_to_id.get(row['source_key'])
                if (eid is None or eid in found or row['payload_json'] != snapshot.payloads[eid]
                        or row['digest'] != digest(row['payload_json']) or row['family'] != snapshot.families[eid]):
                    raise ValueError('SQL source integrity check failed')
                found[eid] = EvidenceEvent.from_dict(json.loads(row['payload_json']))
            if found.keys() != wanted:
                raise ValueError('SQL is missing required sources')
            return EvidenceRetrieval(list(found.values()), {**receipt, 'source_count': len(found), 'family_count': len(families),
                'source_hashes_verified': True, 'query_sha256': digest(sql), 'sql': sql,
                'created_sources': response.get('created_sources'), 'elapsed_ms': response.get('elapsed_ms')})
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            raise ZetarisUnavailable('Zetaris retrieval failed; no fallback answer generated. Check local setup and source integrity.') from exc
