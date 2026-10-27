import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class CodexCliTest(unittest.TestCase):
    def test_jsonl_runs_existing_investigation_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            rows = [
                {"id": "e1", "type": "assistant_message", "session_id": "s1", "author": "codex", "content": "goal", "timestamp": "2026-10-23T09:00:00+00:00", "metadata": {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship demo"}]}},
                {"id": "e2", "type": "test_result", "session_id": "s1", "author": "codex", "content": "tests passed", "timestamp": "2026-10-23T09:01:00+00:00"},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "project_compass.cli", "--codex-jsonl", str(path)], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["goal"], "Ship demo")
            # A same-session test result is not confirmed support for the goal.
            self.assertEqual(payload["evidence_ids"], ["e1"])


if __name__ == "__main__":
    unittest.main()
