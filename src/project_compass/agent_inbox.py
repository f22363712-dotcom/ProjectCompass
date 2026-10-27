"""Explicit local Agent reports/proposals, never owner review or scan authority."""
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re

from .candidate_extraction import validate_candidates
from .fact_engine import derive_facts
from .models import EvidenceEvent
from .native_codex import SENSITIVE
from .native_tool_trace import PRIVATE_INPUT, PRIVATE_TEXT, has_sensitive_switch

EVENT_TYPES = {'assistant_message', 'tool_call', 'tool_result', 'test_result', 'file_change'}


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or not value.keys() <= set(required) | set(optional):
        raise ValueError('invalid Agent protocol fields')


def bounded(value, limit, *, slug=False):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise ValueError('invalid bounded Agent text')
    if slug and not re.fullmatch(r'[A-Za-z0-9_.-]+', value):
        raise ValueError('invalid Agent identity')
    return value


def public_input(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if SENSITIVE.search(text) or PRIVATE_TEXT.search(text):
        raise ValueError('Agent input contains a sensitive marker')


@dataclass(frozen=True)
class AgentClient:
    client_id: str
    member_id: str
    session_id: str
    agent: str


class AgentInbox:
    """Call under the dashboard state lock. One process owns the workspace."""
    def __init__(self, config, workspace, events):
        fields(config, ('schema_version', 'project_path', 'owner_id', 'clients'))
        if type(config['schema_version']) is not int or config['schema_version'] != 1:
            raise ValueError('unsupported Agent config version')
        if Path(bounded(config['project_path'], 2048)).resolve() != workspace.project_path:
            raise ValueError('Agent config project mismatch')
        self.owner_id = bounded(config['owner_id'], 120)
        if not isinstance(config['clients'], list) or not 1 <= len(config['clients']) <= 8:
            raise ValueError('Agent config requires1 to8 clients')
        self.workspace, self.events, self.clients, self._tokens = workspace, events, [], []
        for item in config['clients']:
            fields(item, ('client_id', 'member_id', 'session_id', 'agent', 'token'))
            client = AgentClient(bounded(item['client_id'], 64, slug=True), bounded(item['member_id'], 120),
                                 bounded(item['session_id'], 256), bounded(item['agent'], 64))
            token = bounded(item['token'], 256)
            if (len(token) < 32 or not token.isascii() or any(char.isspace() for char in token) or
                client.member_id in {self.owner_id, 'project-documents'} or
                any(old.client_id == client.client_id or old.member_id == client.member_id or old.session_id == client.session_id for old in self.clients) or
                any(hmac.compare_digest(token, old) for old, _ in self._tokens)):
                raise ValueError('invalid or duplicate Agent access binding')
            self.clients.append(client);self._tokens.append((token, client))
        if any(token in json.dumps(self.public_status(), ensure_ascii=False) for token, _ in self._tokens):
            raise ValueError('Agent credential must not be reused as a public identity')

    @classmethod
    def from_file(cls, path, workspace, events):
        with Path(path).open(encoding='utf8') as handle:
            text = handle.read(65537)
        if len(text) > 65536:
            raise ValueError('Agent config exceeds size bound')
        try:
            config = json.loads(text)
        except ValueError:
            raise ValueError('invalid private Agent config JSON') from None
        fields(config, ('schema_version', 'project_path', 'owner_id', 'clients'))
        if not isinstance(config['clients'], list):
            raise ValueError('invalid Agent config clients')
        clients = []
        for item in config['clients']:
            fields(item, ('client_id', 'member_id', 'session_id', 'agent', 'token_env'))
            name = bounded(item['token_env'], 128)
            if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
                raise ValueError('invalid Agent credential environment name')
            token = os.environ.get(name)
            if not token:
                raise ValueError('Agent credential environment variable is missing')
            clients.append({key:value for key,value in item.items() if key != 'token_env'} | {'token':token})
        config = {**config, 'clients':clients}
        return cls(config, workspace, events)

    def authenticate(self, authorization):
        if isinstance(authorization, str) and authorization.startswith('Bearer '):
            token = authorization[7:]
            if token.isascii():
                for expected, client in self._tokens:
                    if hmac.compare_digest(expected, token):
                        return client
        raise PermissionError('Agent credential required')

    def public_status(self):
        return {'enabled': True, 'owner_id': self.owner_id,
                'clients': [dict(client_id=c.client_id, member_id=c.member_id, session_id=c.session_id, agent=c.agent) for c in self.clients]}

    def _batch(self, payload, name):
        fields(payload, ('schema_version', name))
        if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
            raise ValueError('unsupported Agent protocol version')
        batch = payload[name]
        if not isinstance(batch, list) or not 1 <= len(batch) <= 20:
            raise ValueError('Agent batch must contain1 to20 entries')
        if any(token in json.dumps(payload, ensure_ascii=False) for token, _ in self._tokens):
            raise ValueError('Agent credential must not appear in submitted evidence')
        public_input(payload)
        return batch

    def _identity(self, client, external, prefix):
        external = bounded(external, 128)
        value = json.dumps([self.workspace.project_id, client.client_id, external], ensure_ascii=False)
        return prefix + sha256(value.encode()).hexdigest()[:32]

    def _commit(self, incoming):
        saved = self.workspace.load()
        known = {event.id: event for event in saved}
        added, ids = {}, []
        for event in incoming:
            previous = added.get(event.id) or known.get(event.id)
            if previous is not None and previous.to_dict() != event.to_dict():
                raise ValueError('Agent evidence identity already has different public content')
            if previous is None:
                added[event.id] = event
            ids.append(event.id)
        derive_facts([*saved, *added.values()])
        if added:
            self.workspace.save(list(added.values()))
            self.events[:] = self.workspace.load()
        return {'appended': len(added), 'ids': ids}

    def append_events(self, client, payload):
        incoming = []
        for item in self._batch(payload, 'events'):
            fields(item, ('id', 'type', 'timestamp', 'content'), ('source_ref', 'tool', 'command', 'exit_code', 'files'))
            if not isinstance(item['type'], str) or item['type'] not in EVENT_TYPES:
                raise ValueError('unsupported Agent report type')
            content = bounded(item['content'], 48000)
            try:
                stamp = datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError('timezone required')
            except (AttributeError, ValueError, TypeError):
                raise ValueError('Agent report requires an explicit timezone-aware timestamp') from None
            identity = self._identity(client, item['id'], 'agent-event-')
            metadata = {'member_id': client.member_id, 'client_id': client.client_id, 'capture': 'agent-http-v1',
                        'origin': 'agent_submission', 'source_ref': f'agent://{client.client_id}/{identity}',
                        'external_id': item['id'], 'actor_type': 'agent'}
            if 'source_ref' in item:
                metadata['reported_source_ref'] = bounded(item['source_ref'], 2048)
            if 'tool' in item:
                metadata['tool'] = bounded(item['tool'], 256)
            if 'exit_code' in item:
                if type(item['exit_code']) is not int:
                    raise ValueError('invalid Agent exit code')
                metadata['exit_code'] = item['exit_code']
            if 'files' in item:
                if not isinstance(item['files'], list) or len(item['files']) > 32:
                    raise ValueError('invalid Agent file references')
                metadata['files'] = [bounded(value, 2048) for value in item['files']]
            if 'command' in item:
                command = item['command']
                parts = command if isinstance(command, list) else [command]
                if not parts or len(parts) > 128:
                    raise ValueError('invalid Agent command')
                for part in parts:
                    bounded(part, 16000)
                command_text = json.dumps(command, ensure_ascii=False)
                if len(command_text) > 16000 or PRIVATE_INPUT.search(command_text) or any(has_sensitive_switch(part) for part in parts):
                    raise ValueError('Agent command exceeds bounds or contains sensitive access')
                metadata['command'] = command
            incoming.append(EvidenceEvent(identity, client.session_id, client.agent, item['type'], content, stamp, metadata))
        return self._commit(incoming)

    def propose(self, client, payload):
        incoming = []
        for item in self._batch(payload, 'proposals'):
            fields(item, ('id', 'source_id', 'kind', 'subject_key', 'quote'), ('task_state',))
            identity = self._identity(client, item['id'], 'agent-proposal-')
            try:
                proposals = validate_candidates(self.events, {'candidates': [{k:v for k,v in item.items() if k != 'id'}]})
            except (ValueError, TypeError, KeyError):
                raise ValueError('Agent proposal requires a valid exact source quote and kind') from None
            proposal = proposals[0]
            incoming.append(replace(proposal, id=identity, session_id=client.session_id,
                metadata={**proposal.metadata, 'member_id': client.member_id, 'client_id': client.client_id,
                          'derivation': 'agent-submission-extractive-v1', 'actor_type': 'agent'}))
        return self._commit(incoming)
