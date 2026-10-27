from datetime import datetime, timezone
from dataclasses import replace
from project_compass.project_scanner import scan_project
from project_compass.candidate_extraction import select_batch
from pathlib import Path
import tempfile
import unittest

from project_compass.evidence_linking import confirm_relation
from project_compass.models import EvidenceEvent, EvidenceRelation, EvidenceRelationMethod, EvidenceRelationStatus
from project_compass.project_workspace import ProjectWorkspace


def event(event_id):
    return EvidenceEvent(
        event_id,
        "session-1",
        "alice",
        "assistant_message",
        "public note",
        datetime(2026, 10, 24, tzinfo=timezone.utc),
        {"source_ref": "README.md"},
    )


def candidate_relation():
    return EvidenceRelation(
        id="relation-fact-e1",
        fact_id="fact-1",
        evidence_id="e1",
        relation="supports",
        status=EvidenceRelationStatus.CANDIDATE,
        method=EvidenceRelationMethod.DETERMINISTIC,
        score=0.9,
        basis="exact-text-match",
    )


class ProjectWorkspaceTest(unittest.TestCase):
    def test_file_rollback_keeps_both_snapshots_but_extracts_current_disk_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            workspace = ProjectWorkspace(root / "state", project)
            path = project / "README.md"
            for text in ("Candidate A", "Candidate B", "Candidate A"):
                path.write_text(text, encoding="utf-8")
                workspace.save(scan_project(project))
            loaded = workspace.load()
            self.assertEqual({e.content for e in loaded if e.kind == "file_snapshot"}, {"Candidate A", "Candidate B"})
            batch = select_batch(loaded)
            self.assertEqual([e.content for e in batch], ["Candidate A"])

    def test_rescan_preserves_old_evidence_and_appends_new_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            workspace = ProjectWorkspace(root / "compass-state", project)
            workspace.save([event("old-snapshot")])

            workspace.save([event("new-snapshot")])

            self.assertEqual([item.id for item in workspace.load()], ["old-snapshot", "new-snapshot"])

    def test_reusing_evidence_id_for_changed_content_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            workspace = ProjectWorkspace(root / "compass-state", project)
            workspace.save([event("e1")])

            with self.assertRaises(ValueError):
                workspace.save([replace(event("e1"), content="different claim")])

            self.assertEqual(workspace.load()[0].content, "public note")

    def test_equivalent_paths_share_id_and_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            workspace = ProjectWorkspace(root / "compass-state", project)
            equivalent = ProjectWorkspace(root / "compass-state", project / ".")

            saved = workspace.save([event("e1")], {"source": "scan"})

            self.assertEqual(workspace.project_id, equivalent.project_id)
            self.assertTrue(saved.exists())
            self.assertEqual([item.id for item in equivalent.load()], ["e1"])
            self.assertFalse((project / ".project-memory").exists())

    def test_two_projects_are_isolated_and_missing_state_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "project-a"
            second = root / "project-b"
            first.mkdir()
            second.mkdir()
            first_workspace = ProjectWorkspace(root / "compass-state", first)
            second_workspace = ProjectWorkspace(root / "compass-state", second)

            first_workspace.save([event("first")])

            self.assertTrue(first_workspace.exists())
            self.assertFalse(second_workspace.exists())
            self.assertEqual([item.id for item in first_workspace.load()], ["first"])
            with self.assertRaises(FileNotFoundError):
                second_workspace.load()

    def test_review_state_round_trips_without_changing_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            workspace = ProjectWorkspace(root / "compass-state", project)
            workspace.save([event("e1")])
            confirmed = confirm_relation(candidate_relation(), "owner")
            history = [{"action": "confirm_relation", "relation_id": confirmed.id, "reviewer": "owner"}]

            workspace.save_review_state([confirmed], history, [{"action": "accept_fact", "fact_id": "fact-1"}])
            loaded_relations, loaded_history, loaded_fact_reviews = workspace.load_review_state()

            self.assertEqual([relation.to_dict() for relation in loaded_relations], [confirmed.to_dict()])
            self.assertEqual(loaded_history, history)
            self.assertEqual(loaded_fact_reviews, [{"action": "accept_fact", "fact_id": "fact-1"}])
            self.assertEqual([item.id for item in workspace.load()], ["e1"])

    def test_rescan_save_preserves_review_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            workspace = ProjectWorkspace(root / "compass-state", project)
            confirmed = confirm_relation(candidate_relation(), "owner")
            history = [{"action": "confirm_relation", "relation_id": confirmed.id, "reviewer": "owner"}]

            workspace.save([event("e1")])
            workspace.save_review_state([confirmed], history)
            workspace.save([event("e1"), event("e2")], {"source": "rescan"})

            loaded_relations, loaded_history, loaded_fact_reviews = workspace.load_review_state()

        self.assertEqual([item.id for item in loaded_relations], [confirmed.id])
        self.assertEqual(loaded_history, history)
        self.assertEqual(loaded_fact_reviews, [])


if __name__ == "__main__":
    unittest.main()
