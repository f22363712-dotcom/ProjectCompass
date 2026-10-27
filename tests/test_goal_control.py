from dataclasses import replace
import unittest

from project_compass.candidate_extraction import select_batch, validate_candidates
from project_compass.evidence_linking import build_evidence_index
from project_compass.investigator import investigate
from project_compass.dashboard import build_dashboard_state
from project_compass.fact_engine import derive_facts
from project_compass.native_codex import is_platform_control
from project_compass.projections import build_views
from project_compass.sample_data import sample_events
from project_compass.zetaris_provider import build_snapshot


GOAL = '<codex_internal_context source="goal">Repeated continuation instructions.</codex_internal_context>'


class GoalControlTests(unittest.TestCase):
    def source(self, identity, text, kind='user_message'):
        return replace(sample_events()[0], id=identity, kind=kind, author='Alice', content=text,
                       metadata={'member_id': 'Alice', 'role': 'user'})

    def test_saved_control_is_retained_but_not_speech_model_input_or_provider_source(self):
        control = self.source('goal-control', GOAL)
        reply = self.source('actual-reply', '<send_user_message_question_reply>[{"answer":"Use A."}]</send_user_message_question_reply>')
        events = [control, reply]
        before = [e.to_dict() for e in events]
        self.assertEqual([e.id for e in select_batch(events)], [reply.id])
        self.assertEqual([e.id for e in build_evidence_index(events).catalog], [reply.id])
        self.assertEqual(build_views(events)['personal']['Alice']['evidence_ids'], [reply.id])
        self.assertEqual(set(build_snapshot(events).payloads), {reply.id})
        answer = investigate(events, 'Alice做了什么？').to_dict()
        self.assertEqual(answer['evidence_ids'], [reply.id])
        with self.assertRaises(ValueError):
            validate_candidates(events, {'candidates': [{'source_id': control.id, 'quote': GOAL,
                'kind': 'decision', 'subject_key': 'automatic-continuation'}]})
        self.assertEqual([e.to_dict() for e in events], before)

    def test_document_and_command_quoting_a_protocol_envelope_remain_evidence(self):
        document = self.source('protocol-document', GOAL, 'file_snapshot')
        command = self.source('protocol-stdout', GOAL, 'command_execution')
        events = [document, command]
        self.assertEqual({e.id for e in build_evidence_index(events).catalog}, {document.id, command.id})
        self.assertEqual(set(build_snapshot(events).payloads), {document.id, command.id})
        self.assertEqual([e.id for e in select_batch(events)], [document.id])
        self.assertEqual(len(validate_candidates([document], {'candidates': [{'source_id': document.id,
            'kind': 'assumption', 'subject_key': 'document-quotation', 'quote': GOAL}]})), 1)

    def test_matching_whole_envelopes_only_preserves_surrounding_speech_and_mismatched_tags(self):
        self.assertTrue(is_platform_control(GOAL))
        self.assertTrue(is_platform_control('  \n' + GOAL + '\n'))
        for text in ('User discussion ' + GOAL, '```xml\n' + GOAL + '\n```',
                     GOAL + '\nMy decision is to ship option B.\n' + GOAL,
                     '<external_codex_apps_open_page>literal</external_codex_apps_other>',
                     '<codex_internal_context>unclosed'):
            with self.subTest(text=text):
                self.assertFalse(is_platform_control(text))

    def test_legacy_control_claim_keeps_review_history_but_cannot_supply_current_work(self):
        control = self.source('legacy-control', GOAL)
        control = replace(control, metadata={**control.metadata, 'facts': [{'kind': 'task',
            'subject_key': 'legacy-task', 'value': 'Deploy A.', 'task_state': 'pending'}]})
        fact = derive_facts([control])[0]
        reviews = [{'action': 'accept_fact', 'fact_id': fact.id, 'subject_key': fact.subject_key,
                    'reviewer': 'owner'}]
        board = build_dashboard_state([control], fact_reviews=reviews)
        self.assertEqual(board['team']['facts'][0]['status'], 'accepted')
        self.assertEqual(board['team']['facts'][0]['source_state']['status'], 'unavailable')
        answer = investigate([control], '下一步做什么？', reviews).to_dict()
        self.assertNotIn('Deploy A.', answer['active_tasks'])
        self.assertNotEqual(answer['next_action'], 'Deploy A.')
        self.assertEqual(answer['evidence_ids'], [])
        self.assertEqual(answer['source_warnings'][0]['evidence_ids'], [control.id])
        self.assertEqual(board['fact_reviews'], reviews)


if __name__ == '__main__':
    unittest.main()
