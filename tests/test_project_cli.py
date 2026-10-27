from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_compass.cli import main
from project_compass.evidence_linking import build_evidence_index, confirm_relation
from project_compass.fact_engine import derive_facts
from project_compass.project_workspace import ProjectWorkspace


class ProjectCliTest(unittest.TestCase):
    def test_scan_persists_and_open_reuses_saved_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            (project / "README.md").write_text("# Demo", encoding="utf-8")
            session = project / "session.jsonl"
            session.write_text(
                json.dumps({"id": "session-e1", "type": "assistant_message", "session_id": "s1", "author": "alice", "content": "goal", "timestamp": "2026-10-24T08:00:00+00:00", "metadata": {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship demo"}]}}),
                encoding="utf-8",
            )
            workspace_root = root / "compass-state"

            first_output = io.StringIO()
            with redirect_stdout(first_output):
                scan_result = main(["--scan", str(project), "--scan-codex-jsonl", str(session), "--workspace-root", str(workspace_root)])
            second_output = io.StringIO()
            with redirect_stdout(second_output):
                open_result = main(["--open", str(project), "--workspace-root", str(workspace_root), "--evidence-index"])

            payload = json.loads(second_output.getvalue())
            self.assertEqual(scan_result, 0)
            self.assertEqual(open_result, 0)
            self.assertTrue(ProjectWorkspace(workspace_root, project).exists())
            self.assertEqual([item["id"] for item in payload["evidence"] if item["id"] == "session-e1"], ["session-e1"])

    def test_open_missing_project_state_returns_useful_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-missing"
            project.mkdir()
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = main(["--open", str(project), "--workspace-root", str(root / "compass-state")])

        self.assertEqual(result, 2)
        self.assertIn("run --scan first", stderr.getvalue())

    def test_open_evidence_index_includes_persisted_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            session = project / "session.jsonl"
            session.write_text(
                json.dumps({"id": "session-e1", "type": "assistant_message", "session_id": "s1", "author": "alice", "content": "goal", "timestamp": "2026-10-24T08:00:00+00:00", "metadata": {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship demo"}]}}),
                encoding="utf-8",
            )
            workspace_root = root / "compass-state"
            main(["--scan", str(project), "--scan-codex-jsonl", str(session), "--workspace-root", str(workspace_root)])
            workspace = ProjectWorkspace(workspace_root, project)
            events = workspace.load()
            relation = build_evidence_index(events, derive_facts(events)).candidate_relations[0]
            workspace.save_review_state([confirm_relation(relation, "owner")], [{"action": "confirm_relation"}])

            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["--open", str(project), "--workspace-root", str(workspace_root), "--evidence-index"])

            payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(len(payload["confirmed_relations"]), 1)

    def test_web_review_callback_accepts_fact_and_persists_team_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            session = project / "session.jsonl"
            session.write_text(
                "\n".join([
                    json.dumps({"id": "e1", "type": "assistant_message", "session_id": "a", "author": "alice", "content": "active", "timestamp": "2026-10-24T08:00:00+00:00", "metadata": {"facts": [{"kind": "task", "subject_key": "task.demo", "value": "Build demo [active]"}]}}),
                    json.dumps({"id": "e2", "type": "assistant_message", "session_id": "b", "author": "bob", "content": "blocked", "timestamp": "2026-10-24T08:01:00+00:00", "metadata": {"facts": [{"kind": "task", "subject_key": "task.demo", "value": "Build demo [blocked]"}]}}),
                ]),
                encoding="utf-8",
            )
            workspace_root = root / "compass-state"
            with patch("project_compass.cli.run_dashboard") as run_dashboard:
                self.assertEqual(main(["--scan", str(project), "--scan-codex-jsonl", str(session), "--workspace-root", str(workspace_root), "--web"]), 0)
                callback = run_dashboard.call_args.kwargs["review_callback"]
                events = ProjectWorkspace(workspace_root, project).load()
                selected = derive_facts(events)[0].id
                state = callback("accept_fact", selected, "owner")

            team_fact = next(fact for fact in state["team"]["facts"] if fact["id"] == selected)
            self.assertEqual(team_fact["status"], "accepted")
            self.assertEqual(len(state["team"]["conflicts"]), 1)


if __name__ == "__main__":
    unittest.main()
