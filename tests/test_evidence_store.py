from datetime import datetime, timezone
import unittest

from project_compass.evidence_store import EvidenceStore
from project_compass.models import EvidenceEvent


def event(event_id: str = "e1") -> EvidenceEvent:
    return EvidenceEvent(
        id=event_id,
        session_id="s1",
        author="dev-a",
        kind="agent_message",
        content="Project goal: ship the evidence board.",
        observed_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
        metadata={"source": "chat"},
    )


class EvidenceStoreTest(unittest.TestCase):
    def test_append_is_ordered_and_duplicate_ids_are_rejected(self):
        store = EvidenceStore()
        store.append(event("e1"))
        store.append(event("e2"))
        self.assertEqual([item.id for item in store.list()], ["e1", "e2"])
        with self.assertRaisesRegex(ValueError, "duplicate evidence id"):
            store.append(event("e1"))

    def test_store_round_trip_preserves_event_fields(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            store = EvidenceStore()
            store.append(event())
            store.save(path)
            restored = EvidenceStore.load(path)
            self.assertEqual(restored.list(), store.list())


if __name__ == "__main__":
    unittest.main()
