from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_compass.cli import main


class EvidenceCliTest(unittest.TestCase):
    def test_scan_evidence_index_has_catalog_candidates_and_confirmed_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Demo\nBackground context", encoding="utf-8")
            session = root / "session.jsonl"
            session.write_text(
                "\n".join([
                    json.dumps({"id": "e1", "type": "assistant_message", "session_id": "s1", "author": "alice", "content": "Task proposal", "timestamp": "2026-10-24T08:00:00+00:00", "metadata": {"facts": [{"kind": "task", "subject_key": "task.demo", "value": "Build demo"}]}}),
                    json.dumps({"id": "e2", "type": "test_result", "session_id": "s1", "author": "alice", "content": "7 tests passed", "timestamp": "2026-10-24T08:01:00+00:00"}),
                ]),
                encoding="utf-8",
            )
            output = io.StringIO()
            with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}), redirect_stdout(output):
                result = main(["--scan", str(root), "--scan-codex-jsonl", str(session), "--evidence-index", "--jev", "--env-file", str(root / "missing.env")])

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertIn("evidence", payload)
        self.assertIn("candidate_relations", payload)
        self.assertIn("confirmed_relations", payload)
        self.assertTrue(any(relation["evidence_id"] == "e2" for relation in payload["candidate_relations"]))
        self.assertTrue(any(entry["classification"] == "unclassified" for entry in payload["evidence"]))


if __name__ == "__main__":
    unittest.main()
