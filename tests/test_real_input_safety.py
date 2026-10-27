from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from project_compass.investigator import investigate
from project_compass.project_scanner import scan_project
from project_compass.sample_data import sample_events


class RealInputSafetyTest(unittest.TestCase):
    def test_unstructured_material_is_not_cited_as_support_for_a_missing_fact(self):
        event = replace(sample_events()[0], content="The normal candidate still needs phone verification.", metadata={})

        answer = investigate([event], "Why this state and what next?")

        self.assertTrue(answer.evidence_gaps)
        self.assertEqual(answer.evidence_ids, ())
        self.assertEqual(answer.active_tasks, ())
        self.assertNotIn("按已确认", answer.next_action)
        self.assertNotIn("accepted", answer.acceptance_condition)

    def test_empty_input_is_explicitly_insufficient(self):
        answer = investigate([], "Why this state and what next?")

        self.assertTrue(answer.evidence_gaps)
        self.assertEqual(answer.evidence_ids, ())
        self.assertNotIn("按已确认", answer.next_action)

    def test_unreviewed_nonconflicting_fact_does_not_imply_owner_acceptance(self):
        event = replace(sample_events()[0], metadata={"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Verify the candidate"}]})

        answer = investigate([event], "What next?")

        self.assertEqual(answer.evidence_ids, (event.id,))
        self.assertNotIn("按已确认", answer.next_action)
        self.assertNotIn("accepted", answer.acceptance_condition)

    def test_same_session_material_is_not_automatically_answer_support(self):
        source = sample_events()[0]
        unrelated = replace(source, id="unrelated", content="Unrelated discussion", metadata={})

        answer = investigate([source, unrelated], "Why this state?")

        self.assertIn(source.id, answer.evidence_ids)
        self.assertNotIn(unrelated.id, answer.evidence_ids)

    def test_scan_covers_recent_reviews_and_backlog_without_crawling_private_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "README.md": "Current candidate",
                "CONTEXT.md": "Product terms",
                "docs/current-behavior.md": "Current behavior",
                "docs/reviews/current-phone.md": "Normal-build phone verification pending",
                "docs/plans/backlog.md": "- [ ] Verify wind-down",
                "docs/adr/001-choice.md": "Keep public sources",
                "docs/archive/old.md": "Old unrelated design",
                "docs/reviews/.env.private.md": "DO_NOT_READ_CREDENTIAL",
                "node_modules/notes.md": "DO_NOT_READ_DEPENDENCY",
                ".env": "DO_NOT_READ_SECRET",
            }
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            snapshots = [event for event in scan_project(root) if event.kind == "file_snapshot"]
            sources = {Path(event.metadata["source_ref"]).as_posix() for event in snapshots}

        self.assertEqual(sources, {
            "README.md", "CONTEXT.md", "docs/current-behavior.md",
            "docs/reviews/current-phone.md", "docs/plans/backlog.md", "docs/adr/001-choice.md",
        })
        self.assertFalse(any("DO_NOT_READ" in event.content for event in snapshots))

    def test_changed_file_gets_a_new_evidence_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "README.md"
            path.write_text("Earlier candidate installed", encoding="utf-8")
            before = scan_project(root)[0]
            path.write_text("Later candidate not installed", encoding="utf-8")
            after = scan_project(root)[0]

        self.assertNotEqual(before.id, after.id)
        self.assertNotEqual(before.metadata.get("content_hash"), after.metadata.get("content_hash"))


if __name__ == "__main__":
    unittest.main()
