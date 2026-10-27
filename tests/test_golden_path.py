import unittest

from project_compass.investigator import investigate
from project_compass.fact_engine import derive_facts
from project_compass.sample_data import sample_events


class GoldenPathTest(unittest.TestCase):
    def test_answer_is_evidence_backed_and_keeps_both_conflict_sides(self):
        answer = investigate(sample_events(), "Why is the project in its current state, and what should happen next?")
        self.assertIn("e1", answer.evidence_ids)
        self.assertTrue(answer.goal)
        self.assertGreaterEqual(len(answer.active_tasks), 1)
        self.assertEqual(len(answer.conflicts), 1)
        self.assertEqual(len(answer.conflicts[0].sides), 2)
        self.assertTrue(answer.next_action)
        self.assertTrue(answer.acceptance_condition)
        self.assertEqual(answer.evidence_gaps, ())

    def test_answer_uses_accepted_team_fact_and_keeps_conflict_history(self):
        events = sample_events()
        facts = derive_facts(events)
        accepted = next(fact for fact in facts if fact.value.endswith("[active]"))

        answer = investigate(
            events,
            "Why is the project in its current state, and what should happen next?",
            fact_reviews=[{"action": "accept_fact", "fact_id": accepted.id, "subject_key": accepted.subject_key}],
        )

        payload = answer.to_dict()
        self.assertIn(accepted.value, payload["active_tasks"])
        self.assertEqual(payload["conflicts"][0]["sides"][0]["status"], "accepted")
        self.assertTrue(payload["next_action"])


if __name__ == "__main__":
    unittest.main()
