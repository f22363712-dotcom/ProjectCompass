from dataclasses import replace
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_compass.candidate_extraction import validate_candidates
from project_compass.cli import main
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events


class ExtractionCliTest(unittest.TestCase):
    def test_explicit_extraction_appends_proposals_and_reopens_with_real_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            workspace = ProjectWorkspace(root / "state", project)
            event = replace(sample_events()[0], kind="user_message", content="Verify the phone build.", metadata={"role": "user"})
            workspace.save([event])
            proposals = validate_candidates([event], {"candidates": [{"source_id": event.id, "kind": "task",
                "subject_key": "phone", "quote": event.content, "task_state": "pending"}]}, "dev")
            with patch("project_compass.cli.CodexExtractor") as factory, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                factory.return_value.extract.return_value = proposals
                factory.return_value.last_result = {"selected_sources": 1}
                result = main(["--open", str(project), "--workspace-root", str(root / "state"),
                               "--extract-codex", "--member-id", "dev", "--env-file", str(root / "missing.env")])
            self.assertEqual(result, 0)
            self.assertEqual([e.id for e in workspace.load()], [event.id, proposals[0].id])
            self.assertEqual(workspace.load_review_state()[2], [])


if __name__ == "__main__":
    unittest.main()
