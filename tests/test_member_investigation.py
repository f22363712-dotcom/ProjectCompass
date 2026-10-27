from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest

from project_compass.candidate_extraction import validate_candidates
from project_compass.fact_engine import derive_facts
from project_compass.investigator import investigate
from project_compass.sample_data import sample_events
from project_compass.web_server import create_dashboard_server
from project_compass.zetaris_provider import EvidenceRetrieval


class MemberInvestigationTests(unittest.TestCase):
    def source(self, member, text, **updates):
        return replace(replace(sample_events()[0], id=member + '-source', author='codex',
            kind='assistant_message', content=text,
            metadata={'member_id': member, 'role': 'assistant'}), **updates)

    def records(self, source):
        return [source, *validate_candidates([source], {'candidates': [{
            'source_id': source.id, 'kind': 'task', 'subject_key': source.id,
            'quote': source.content, 'task_state': 'pending'}]})]

    def test_named_member_returns_only_their_claims_and_no_other_owner_next_action(self):
        alice = self.source('Alice', 'Check candidate A; installation remains unverified.')
        bob = self.source('Bob', 'Publish candidate B after approval.')
        events = self.records(alice) + self.records(bob)
        fact = derive_facts(self.records(bob))[0]
        reviews = [{'action': 'accept_fact', 'subject_key': fact.subject_key, 'fact_id': fact.id,
                    'reviewer': 'owner', 'observed_at': bob.observed_at.isoformat()}]
        answer = investigate(events, 'Alice 做了什么，对应的证据是什么？', reviews).to_dict()
        self.assertEqual(answer['scope']['member_id'], 'Alice')
        self.assertEqual([f['quote'] for f in answer['findings']], [alice.content])
        self.assertEqual(answer['evidence_ids'], [alice.id])
        self.assertNotEqual(answer['next_action'], bob.content)
        self.assertNotEqual(answer['next_action_status'], 'accepted')
        self.assertEqual(answer['findings'][0]['member_id'], 'Alice')
        self.assertEqual(answer['findings'][0]['observed_at'], alice.observed_at.isoformat())
        self.assertEqual(answer['findings'][0]['claim_type'], 'assistant_report')

    def test_exact_names_reject_substrings_and_ambiguous_colleague_questions(self):
        events = self.records(self.source('Ann', 'Check A.')) + self.records(self.source('Anna', 'Check B.'))
        answer = investigate(events, 'Anna做了什么？').to_dict()
        self.assertEqual(answer['scope']['member_id'], 'Anna')
        for question in ('另一个同事做了什么？', 'Ann 和 Anna 做了什么？', 'Annabelle 做了什么？'):
            with self.subTest(question=question):
                result = investigate(events, question).to_dict()
                self.assertEqual(result['scope']['kind'], 'member_choice')
                self.assertEqual(result['findings'], [])
                self.assertEqual(result['evidence_ids'], [])
                self.assertEqual(set(result['scope']['available_members']), {'Ann', 'Anna'})

    def test_raw_only_member_has_bounded_literal_excerpt_without_invented_fact(self):
        source = self.source('Alice', 'Historical assistant report. ' * 100)
        platform = self.source('Alice', '<external_codex_apps_notice>private marker</external_codex_apps_notice>', id='control')
        answer = investigate([source, platform], 'Alice 做了什么？').to_dict()
        self.assertEqual(answer['scope']['member_id'], 'Alice')
        self.assertEqual(answer['evidence_ids'], [source.id])
        finding = answer['findings'][0]
        self.assertEqual(finding['status'], 'raw')
        self.assertEqual(finding['quote'], source.content[:1600])
        self.assertTrue(finding['excerpt_truncated'])
        self.assertIsNone(answer['next_action_status'])
        self.assertEqual(derive_facts([source, platform]), [])

    def test_overlapping_chinese_names_use_full_identity_but_two_mentions_are_ambiguous(self):
        events = self.records(self.source('张', 'Verify A.')) + self.records(self.source('张三', 'Verify B.'))
        self.assertEqual(investigate(events, '张三做了什么？').to_dict()['scope']['member_id'], '张三')
        self.assertEqual(investigate(events, '张三和张分别做了什么？').to_dict()['scope']['kind'], 'member_choice')

    def test_unknown_longer_chinese_identity_never_selects_known_substring(self):
        events = self.records(self.source('张三', 'Verify A.'))
        for question in ('张三丰做了什么？', '欧阳张三做了什么？'):
            with self.subTest(question=question):
                result = investigate(events, question).to_dict()
                self.assertEqual(result['scope']['kind'], 'member_choice')
                self.assertEqual(result['evidence_ids'], [])

    def test_unknown_subject_next_action_cannot_fall_back_to_another_members_work(self):
        events = self.records(self.source('Alice', 'Deploy A.'))
        for question in ('Charlie 下一步做什么？', '另一个人接下来做什么？',
                         'Charlie 当前状态如何？', 'Charlie做了哪些决策？', 'Charlie progress?'):
            with self.subTest(question=question):
                result = investigate(events, question).to_dict()
                self.assertEqual(result['scope']['kind'], 'member_choice')
                self.assertNotEqual(result['next_action'], 'Deploy A.')
                self.assertEqual(result['evidence_ids'], [])
        self.assertIsNone(investigate(events, '项目下一步做什么？').to_dict()['scope'])

    def test_other_accepted_conflict_remains_context_and_blocks_member_recommendation(self):
        alice = self.source('Alice', 'Deploy candidate A.')
        bob = self.source('Bob', 'Cancel candidate A deployment.')
        events = [alice, bob]
        for source in (alice, bob):
            events.extend(validate_candidates([source], {'candidates': [{
                'source_id': source.id, 'kind': 'task', 'subject_key': 'shared',
                'quote': source.content, 'task_state': 'pending'}]}))
        fact = next(f for f in derive_facts(events) if f.author == 'Bob')
        review = {'action': 'accept_fact', 'subject_key': fact.subject_key,
                  'fact_id': fact.id, 'reviewer': 'owner'}
        answer = investigate(events, 'Alice 下一步做什么？', [review]).to_dict()
        self.assertNotIn(alice.content, answer['active_tasks'])
        self.assertNotEqual(answer['next_action'], alice.content)
        self.assertNotEqual(answer['next_action'], bob.content)
        self.assertEqual({s['author'] for s in answer['conflicts'][0]['sides']}, {'Alice', 'Bob'})
        self.assertEqual(set(answer['evidence_ids']), {alice.id, bob.id})
        self.assertEqual([f['quote'] for f in answer['findings']], [alice.content])

    def test_legacy_native_binding_matches_board_without_rewriting_source(self):
        source = replace(self.source('Alice', 'Public legacy message.'), metadata={'role': 'assistant'})
        before = source.to_dict()
        server = create_dashboard_server([source], port=0,
            intake_status={'enabled': True, 'thread_id': source.session_id, 'member_id': 'Alice'})
        self.addCleanup(server.server_close)
        answer = server.investigate('Alice 做了什么？')
        self.assertEqual(answer['scope']['member_id'], 'Alice')
        self.assertEqual(answer['evidence_ids'], [source.id])
        self.assertEqual(source.to_dict(), before)

    def test_changed_document_claim_cannot_supply_members_current_next_action(self):
        source = replace(self.source('Alice', 'Check candidate v1.'), kind='file_snapshot',
                         metadata={'source_ref': 'docs/review.md'})
        events = self.records(source)
        fact = replace(derive_facts(events)[0], author='Alice')
        newer = replace(source, id='file-v2', content='Review candidate v2 instead.',
                        observed_at=source.observed_at + timedelta(days=1))
        review = {'action': 'accept_fact', 'fact_id': fact.id, 'subject_key': fact.subject_key}
        result = investigate([*events, newer], 'Alice 下一步做什么？', [review], memory_facts=[fact]).to_dict()
        self.assertNotIn(source.content, result['active_tasks'])
        self.assertNotEqual(result['next_action'], source.content)
        self.assertEqual(result['source_warnings'][0]['source_state']['status'], 'superseded')

    def test_memory_and_jdbc_receive_member_scope_and_only_related_evidence(self):
        alice = self.source('Alice', 'Check candidate A.')
        bob = self.source('Bob', 'Check candidate B.')
        events = self.records(alice) + self.records(bob)
        facts = derive_facts(events)
        wanted = []

        class Memory:
            def query(self, events, reviews, relations):
                return SimpleNamespace(facts=facts, support_by_fact={f.id: list(f.evidence_ids) for f in facts},
                                       status={'enabled': True, 'engine': 'synthetic-memory'})

        class Provider:
            def retrieve(self, snapshot, ids, base_url):
                wanted.extend(ids)
                return EvidenceRetrieval([e for e in events if e.id in ids],
                                         {'enabled': True, 'queried': True, 'source_count': len(ids)})

        server = create_dashboard_server(events, port=0, memory=Memory(), zetaris=Provider())
        self.addCleanup(server.server_close)
        answer = server.investigate('Alice做了什么？')
        self.assertEqual(wanted, [alice.id])
        self.assertEqual(answer['scope']['member_id'], 'Alice')
        self.assertEqual(answer['memory']['engine'], 'synthetic-memory')
        self.assertEqual(answer['zetaris']['source_count'], 1)
        self.assertEqual([f['quote'] for f in answer['findings']], [alice.content])


if __name__ == '__main__':
    unittest.main()
