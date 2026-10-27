from datetime import datetime, timezone
from dataclasses import replace
import unittest

from project_compass.evidence_linking import DeterministicLinkProvider, build_evidence_index, confirm_relation
from project_compass.fact_engine import derive_facts
from project_compass.models import (
    EvidenceEvent,
    EvidenceRelation,
    EvidenceRelationMethod,
    EvidenceRelationStatus,
)
from project_compass.sample_data import sample_events


def event(event_id, kind, content, metadata=None):
    return EvidenceEvent(
        event_id,
        "session-1",
        "alice",
        kind,
        content,
        datetime(2026, 10, 24, tzinfo=timezone.utc),
        metadata or {},
    )


class EvidenceLinkingTest(unittest.TestCase):
    def test_catalog_and_relation_serialize_with_explicit_support(self):
        events = [event(
            "e1",
            "assistant_message",
            "Set project goal",
            {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship board"}]},
        )]
        facts = derive_facts(events)
        index = build_evidence_index(events, facts)
        payload = index.to_dict()

        self.assertEqual(payload["evidence"][0]["id"], "e1")
        self.assertEqual(payload["evidence"][0]["classification"], "fact_source")
        self.assertEqual(payload["evidence"][0]["candidate_fact_ids"], [facts[0].id])
        self.assertEqual(payload["evidence"][0]["confirmed_fact_ids"], [])
        self.assertEqual(len(payload["candidate_relations"]), 1)
        self.assertEqual(payload["candidate_relations"][0]["evidence_id"], "e1")
        self.assertEqual(payload["candidate_relations"][0]["fact_id"], facts[0].id)
        self.assertEqual(payload["confirmed_relations"], [])

    def test_test_result_is_candidate_but_unclassified_material_stays_visible(self):
        events = [
            event(
                "e1",
                "assistant_message",
                "Task proposal",
                {"facts": [{"kind": "task", "subject_key": "task.demo", "value": "Build demo"}]},
            ),
            event("e2", "test_result", "7 tests passed"),
            event("e3", "file_snapshot", "# Notes\nBackground context"),
        ]
        facts = derive_facts(events)
        index = build_evidence_index(events, facts)

        self.assertEqual(index.catalog_by_id["e2"].classification, "test_result")
        self.assertEqual(index.catalog_by_id["e3"].classification, "unclassified")
        same_session = [r for r in index.candidate_relations if r.evidence_id == "e2"]
        self.assertEqual(len(same_session), 1)
        self.assertEqual(same_session[0].basis, "same-session-result")
        self.assertIs(same_session[0].status, EvidenceRelationStatus.CANDIDATE)
        self.assertEqual(index.catalog_by_id["e3"].candidate_fact_ids, ())

    def test_same_author_different_session_result_is_not_called_same_session(self):
        events = [
            event(
                "e1",
                "assistant_message",
                "Task proposal",
                {"facts": [{"kind": "task", "subject_key": "task.demo", "value": "Build demo"}]},
            ),
            EvidenceEvent(
                "e2",
                "session-2",
                "alice",
                "test_result",
                "7 tests passed",
                datetime(2026, 10, 24, tzinfo=timezone.utc),
                {},
            ),
        ]
        facts = derive_facts(events)

        index = build_evidence_index(events, facts)

        self.assertEqual([relation for relation in index.candidate_relations if relation.evidence_id == "e2"], [])

    def test_ambiguous_session_result_is_left_unlinked_when_two_tasks_exist(self):
        events = [
            event(
                "e1",
                "assistant_message",
                "Task proposals",
                {"facts": [
                    {"kind": "task", "subject_key": "task.one", "value": "Build demo"},
                    {"kind": "task", "subject_key": "task.two", "value": "Write docs"},
                ]},
            ),
            event("e2", "test_result", "7 tests passed"),
        ]
        facts = derive_facts(events)

        index = build_evidence_index(events, facts)

        self.assertEqual([relation for relation in index.candidate_relations if relation.evidence_id == "e2"], [])

    def test_exact_text_match_can_link_an_ambiguous_result_to_one_task(self):
        events = [
            event(
                "e1",
                "assistant_message",
                "Task proposals",
                {"facts": [
                    {"kind": "task", "subject_key": "task.one", "value": "Build demo"},
                    {"kind": "task", "subject_key": "task.two", "value": "Write docs"},
                ]},
            ),
            event("e2", "test_result", "Build demo passed"),
        ]
        facts = derive_facts(events)

        index = build_evidence_index(events, facts)

        self.assertEqual([(relation.fact_id, relation.basis) for relation in index.candidate_relations if relation.evidence_id == "e2"], [(facts[0].id, "exact-text-match")])

    def test_confirm_relation_creates_confirmed_copy_without_mutating_candidate(self):
        event_one = event(
            "e1",
            "assistant_message",
            "Set project goal",
            {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship board"}]},
        )
        fact = derive_facts([event_one])[0]
        relation = build_evidence_index([event_one], [fact]).candidate_relations[0]

        confirmed = confirm_relation(relation, "owner")

        self.assertIs(relation.status, EvidenceRelationStatus.CANDIDATE)
        self.assertIs(confirmed.status, EvidenceRelationStatus.CONFIRMED)
        self.assertEqual(confirmed.reviewed_by, "owner")
        self.assertEqual(confirmed.id, relation.id + ":confirmed")

    def test_index_can_append_confirmed_relation_while_preserving_candidate(self):
        event_one = event(
            "e1",
            "assistant_message",
            "Set project goal",
            {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship board"}]},
        )
        fact = derive_facts([event_one])[0]
        index = build_evidence_index([event_one], [fact])
        confirmed = confirm_relation(index.candidate_relations[0], "owner")

        updated = index.with_confirmed(confirmed)

        self.assertEqual(len(updated.candidate_relations), 1)
        self.assertEqual(len(updated.confirmed_relations), 1)
        self.assertEqual(updated.confirmed_relations[0].reviewed_by, "owner")
        self.assertEqual(updated.catalog_by_id["e1"].confirmed_fact_ids, (fact.id,))

    def test_index_can_apply_persisted_confirmations_without_removing_candidates(self):
        event_one = event(
            "e1",
            "assistant_message",
            "Set project goal",
            {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship board"}]},
        )
        index = build_evidence_index([event_one])
        confirmed = confirm_relation(index.candidate_relations[0], "owner")

        updated = index.with_confirmed_relations([confirmed])

        self.assertEqual(len(updated.candidate_relations), len(index.candidate_relations))
        self.assertEqual([item.id for item in updated.confirmed_relations], [confirmed.id])
        self.assertIn(confirmed.fact_id, updated.catalog_by_id[confirmed.evidence_id].confirmed_fact_ids)

    def test_index_ignores_stale_persisted_confirmation(self):
        index = build_evidence_index(sample_events())
        stale = EvidenceRelation(
            "relation-missing:confirmed", "fact-missing", "evidence-missing",
            "supports", EvidenceRelationStatus.CONFIRMED, EvidenceRelationMethod.HUMAN,
            0.5, "old", "owner",
        )

        updated = index.with_confirmed_relations([stale])

        self.assertEqual(updated.confirmed_relations, ())

    def test_confirmation_rejects_non_candidate_or_unknown_relations(self):
        event_one = event(
            "e1",
            "assistant_message",
            "Set project goal",
            {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship board"}]},
        )
        fact = derive_facts([event_one])[0]
        index = build_evidence_index([event_one], [fact])
        candidate = index.candidate_relations[0]
        confirmed = confirm_relation(candidate, "owner")

        with self.assertRaises(ValueError):
            confirm_relation(confirmed, "owner")
        with self.assertRaises(ValueError):
            index.with_confirmed(EvidenceRelation(
                "relation-unknown:confirmed", candidate.fact_id, candidate.evidence_id,
                "supports", EvidenceRelationStatus.CONFIRMED, candidate.method, candidate.score, candidate.basis, "owner",
            ))
        with self.assertRaises(ValueError):
            index.with_confirmed(EvidenceRelation(
                candidate.id + ":confirmed", "fake-fact", candidate.evidence_id,
                "supports", EvidenceRelationStatus.CONFIRMED, candidate.method, candidate.score, candidate.basis, "owner",
            ))
        with self.assertRaises(ValueError):
            index.with_confirmed(EvidenceRelation(
                candidate.id, candidate.fact_id, candidate.evidence_id,
                "supports", EvidenceRelationStatus.CONFIRMED, EvidenceRelationMethod.HUMAN, candidate.score, candidate.basis, "owner",
            ))
        with self.assertRaises(ValueError):
            confirm_relation(candidate, "")

    def test_legacy_fact_without_session_id_uses_its_source_event(self):
        event_one = event(
            "e1",
            "assistant_message",
            "Task proposal",
            {"facts": [{"kind": "task", "subject_key": "task.demo", "value": "Build demo"}]},
        )
        event_two = event("e2", "test_result", "7 tests passed")
        fact = replace(derive_facts([event_one])[0], session_id=None)

        index = build_evidence_index([event_one, event_two], [fact])

        self.assertEqual([relation.evidence_id for relation in index.candidate_relations], ["e1", "e2"])
        self.assertIsNone(DeterministicLinkProvider().suggest(fact, event_two))

    def test_null_relation_metadata_is_treated_as_no_explicit_reference(self):
        event_one = event(
            "e1",
            "assistant_message",
            "Task proposal",
            {"supports_fact_ids": None, "facts": [{"kind": "task", "subject_key": "task.demo", "value": "Build demo"}]},
        )

        index = build_evidence_index([event_one], derive_facts([event_one]))

        self.assertEqual(len(index.candidate_relations), 1)


if __name__ == "__main__":
    unittest.main()
