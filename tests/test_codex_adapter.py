import json
import tempfile
import unittest
from pathlib import Path

from project_compass.codex_adapter import CodexAdapter
from project_compass.evidence_store import EvidenceStore


class CodexAdapterTest(unittest.TestCase):
    def make_file(self, lines):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "session.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return path

    def test_normalizes_public_codex_events(self):
        path = self.make_file([
            {"id": "c1", "type": "assistant_message", "session_id": "s1", "author": "codex", "content": "Goal set", "timestamp": "2026-10-23T09:00:00+00:00", "metadata": {"facts": [{"kind": "goal", "subject_key": "project.goal", "value": "Ship demo"}]}},
            {"id": "c2", "type": "tool_result", "session_id": "s1", "author": "codex", "content": "7 tests passed", "timestamp": "2026-10-23T09:01:00+00:00", "tool": "unittest"},
            {"id": "c3", "type": "file_change", "session_id": "s1", "author": "codex", "content": "created src/app.py", "timestamp": "2026-10-23T09:02:00+00:00"},
        ])
        events = CodexAdapter.from_jsonl(path)
        self.assertEqual([event.id for event in events], ["c1", "c2", "c3"])
        self.assertEqual(events[1].kind, "tool_result")
        self.assertEqual(events[1].metadata["tool"], "unittest")
        self.assertEqual(events[0].metadata["facts"][0]["kind"], "goal")

    def test_duplicate_ids_are_rejected_by_store(self):
        path = self.make_file([
            {"id": "same", "type": "user_message", "session_id": "s1", "author": "dev", "content": "a", "timestamp": "2026-10-23T09:00:00+00:00"},
            {"id": "same", "type": "user_message", "session_id": "s1", "author": "dev", "content": "b", "timestamp": "2026-10-23T09:01:00+00:00"},
        ])
        with self.assertRaisesRegex(ValueError, "duplicate evidence id"):
            EvidenceStore.load_jsonl(path)

    def test_malformed_line_reports_line_number(self):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "bad.jsonl"
        path.write_text('{"id":"ok","type":"user_message"}\n{bad', encoding="utf-8")
        self.addCleanup(directory.cleanup)
        with self.assertRaisesRegex(ValueError, "line 2"):
            CodexAdapter.from_jsonl(path)


if __name__ == "__main__":
    unittest.main()
