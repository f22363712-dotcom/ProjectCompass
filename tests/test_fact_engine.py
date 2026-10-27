from datetime import datetime, timezone
import unittest

from project_compass.fact_engine import derive_facts
from project_compass.models import EvidenceEvent, FactStatus


def ev(event_id, session, author, content, metadata):
    return EvidenceEvent(event_id, session, author, "agent_message", content, datetime(2026, 10, 23, tzinfo=timezone.utc), metadata)


class FactEngineTest(unittest.TestCase):
    def test_derives_structured_facts_and_conflict(self):
        events = [
            ev("e1", "s1", "a", "", {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship board"}, {"kind": "task", "subject_key": "task.demo", "value": "Build demo", "status": "active"}]}),
            ev("e2", "s2", "b", "", {"facts": [{"kind": "task", "subject_key": "task.demo", "value": "Build demo", "status": "blocked"}]}),
        ]
        facts = derive_facts(events)
        task_facts = [fact for fact in facts if fact.subject_key == "task.demo"]
        self.assertEqual(len(task_facts), 2)
        self.assertTrue(all(fact.status is FactStatus.CONFLICTED for fact in task_facts))
        self.assertEqual(task_facts[0].evidence_ids, ("e1",))


if __name__ == "__main__":
    unittest.main()
