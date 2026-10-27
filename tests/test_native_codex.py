from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest
import subprocess
import sys

from project_compass.native_codex import NativeCodexIntake
from project_compass.models import EvidenceEvent
from project_compass.project_workspace import ProjectWorkspace


THREAD = "11111111-1111-4111-8111-111111111111"


class NativeCodexTest(unittest.TestCase):
    def test_sensitive_filter_is_bounded_for_long_text_and_keeps_camelcase_keys(self):
        script="from project_compass.native_codex import SENSITIVE; assert SENSITIVE.search('x'*48000) is None; assert SENSITIVE.search('myPassword=synthetic-only')"
        try:
            result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=3)
        except subprocess.TimeoutExpired:
            self.fail('sensitive marker scan exceeded3 seconds for a bounded text record')
        self.assertEqual(result.returncode,0,result.stderr)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.sessions = root / "sessions"
        self.sessions.mkdir()
        self.path = self.sessions / f"rollout-{THREAD}.jsonl"
        self.workspace = ProjectWorkspace(root / "state", self.project)
        self.events = []

    def message(self, text="Verify the next candidate", *, role="user", channel=None, timestamp="2026-10-25T08:00:00Z"):
        return {"type": "response_item", "timestamp": timestamp, "payload": {
            "type": "message", "role": role, "channel": channel,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}

    def write(self, rows, *, project=None, path=None):
        header = {"type": "session_meta", "payload": {"id": THREAD, "cwd": str(project or self.project), "base_instructions": "DO_NOT_IMPORT"}}
        (path or self.path).write_text("\n".join(json.dumps(row) for row in [header, *rows]) + "\n", encoding="utf-8")

    def reader(self):
        return NativeCodexIntake(self.sessions, self.project, THREAD, "developer-a")

    def test_only_public_messages_are_captured_with_source_and_member(self):
        self.write([self.message(), self.message("Public report", role="assistant", channel="final"),
                    self.message("Hidden reasoning", role="assistant", channel="analysis"),
                    {"type": "response_item", "payload": {"type": "function_call", "arguments": "DO_NOT_IMPORT"}}])
        status = self.reader().sync(self.workspace, self.events)
        self.assertEqual(len(self.events), 2)
        self.assertEqual(status["captured_messages"], 2)
        self.assertEqual({e.metadata["member_id"] for e in self.events}, {"developer-a"})
        self.assertTrue(all(str(self.path) in e.metadata["source_ref"] for e in self.events))
        self.assertFalse(any("Hidden" in e.content or "DO_NOT_IMPORT" in e.content for e in self.events))

    def test_sensitive_messages_are_skipped_with_visible_counts(self):
        self.write([self.message(), self.message("TYPESAFE_API_KEY=example_secret"), self.message("配对码 123456")])
        status = self.reader().sync(self.workspace, self.events)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(status["skipped_sensitive"], 2)

    def test_candidate_count_updates_when_rollout_bytes_have_not_changed(self):
        from project_compass.candidate_extraction import validate_candidates
        self.write([self.message()])
        reader = self.reader()
        self.assertEqual(reader.sync(self.workspace,self.events)['messages_without_candidates'],1)
        source=self.events[0]
        self.workspace.save(validate_candidates(self.events,{'candidates':[{'source_id':source.id,
            'kind':'task','subject_key':'phone','quote':source.content,'task_state':'pending'}]},'developer-a'))
        self.events[:]=self.workspace.load()
        status=reader.sync(self.workspace,self.events)
        self.assertFalse(status['updated'])
        self.assertEqual(status['new_messages'],0)
        self.assertEqual(status['messages_without_candidates'],0)

    def test_named_english_credentials_are_not_imported(self):
        self.write([self.message(), self.message("AWS_SECRET_ACCESS_KEY=synthetic-only"),
                    self.message("password=synthetic-only"), self.message("GITHUB_TOKEN = synthetic-only")])
        status = self.reader().sync(self.workspace, self.events)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(status["skipped_sensitive"], 3)

    def test_unknown_assistant_channel_is_not_treated_as_public(self):
        self.write([self.message(), self.message("Private channel", role="assistant", channel="internal_summary")])
        status = self.reader().sync(self.workspace, self.events)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(status["skipped_nonpublic"], 1)

    def test_platform_control_only_messages_are_not_project_speech(self):
        self.write([self.message(), self.message('<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>')])
        status = self.reader().sync(self.workspace, self.events)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(status["skipped_control"], 1)

    def test_goal_continuation_is_not_a_new_user_decision_but_reply_is_preserved(self):
        goal = '<codex_internal_context source="goal">Continue the active goal.</codex_internal_context>'
        reply = '<send_user_message_question_reply>[{"answer":"Use A."}]</send_user_message_question_reply>'
        self.write([self.message(goal), self.message(reply)])
        status = self.reader().sync(self.workspace, self.events)
        self.assertEqual([e.content for e in self.events], [reply])
        self.assertEqual(status['skipped_control'], 1)

    def test_separate_envelope_quotes_cannot_hide_user_decision_between_them(self):
        control = '<codex_internal_context source="goal">Continue.</codex_internal_context>'
        content = control + '\nMy decision is to ship option B.\n' + control
        self.write([self.message(content)])
        status = self.reader().sync(self.workspace, self.events)
        self.assertEqual([e.content for e in self.events], [content])
        self.assertEqual(status['skipped_control'], 0)

    def test_malformed_complete_record_preserves_saved_state(self):
        self.write([self.message()])
        reader = self.reader()
        reader.sync(self.workspace, self.events)
        before = self.workspace.state_path.read_bytes()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "response_item", "payload": "invalid shape"}) + "\n")
        with self.assertRaises(ValueError):
            reader.sync(self.workspace, self.events)
        self.assertEqual(self.workspace.state_path.read_bytes(), before)

    def test_wrong_project_is_rejected_without_saving(self):
        other = self.project.parent / "other"
        other.mkdir()
        self.write([self.message()], project=other)
        with self.assertRaisesRegex(ValueError, "project"):
            self.reader().sync(self.workspace, self.events)
        self.assertFalse(self.workspace.exists())

    def test_duplicate_segments_deduplicate_but_distinct_times_survive(self):
        row = self.message()
        self.write([row, self.message(timestamp="2026-10-25T08:01:00Z")])
        self.write([row], path=self.sessions / f"rollout-{THREAD}_fork.jsonl")
        self.reader().sync(self.workspace, self.events)
        self.assertEqual(len(self.events), 2)

    def test_reuses_existing_legacy_identity_and_preserves_owner_reviews(self):
        row = self.message()
        legacy = EvidenceEvent("legacy-id", THREAD, "developer-a", "user_message", "Verify the next candidate",
                               datetime(2026, 10, 25, 8, tzinfo=timezone.utc), {"role": "user"})
        self.workspace.save([legacy])
        self.workspace.save_review_state([], [{"action": "accept_fact", "fact_id": "existing"}], [{"action": "accept_fact", "fact_id": "existing"}])
        self.events[:] = self.workspace.load()
        self.write([row])
        reader = self.reader()
        reader.sync(self.workspace, self.events)
        state_before = self.workspace.state_path.read_bytes()
        status = reader.sync(self.workspace, self.events)
        self.assertEqual([e.id for e in self.events], ["legacy-id"])
        self.assertFalse(status["updated"])
        self.assertEqual(self.workspace.state_path.read_bytes(), state_before)
        self.assertEqual(self.workspace.load_review_state()[2][0]["fact_id"], "existing")

    def test_old_author_is_preserved_when_new_member_capture_appends_a_message(self):
        legacy = EvidenceEvent("legacy", THREAD, "old-owner", "user_message", "Verify the next candidate",
                               datetime(2026, 10, 25, 8, tzinfo=timezone.utc), {"role": "user"})
        self.workspace.save([legacy])
        self.events[:] = self.workspace.load()
        self.write([self.message(), self.message("New request", timestamp="2026-10-25T08:01:00Z")])
        self.reader().sync(self.workspace, self.events)
        self.assertEqual(len(self.events), 2)
        self.assertEqual(next(e for e in self.events if e.id == "legacy").author, "old-owner")

    def test_partial_tail_waits_then_imports_without_losing_existing_material(self):
        self.write([self.message()])
        tail = json.dumps(self.message("Later message", timestamp="2026-10-25T08:02:00Z"))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(tail[:30])
        reader = self.reader()
        status = reader.sync(self.workspace, self.events)
        self.assertEqual(status["pending_tail_lines"], 1)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(tail[30:] + "\n")
        reader.sync(self.workspace, self.events)
        self.assertEqual(len(self.events), 2)

    def test_repeated_sync_backfills_older_tool_pairs_and_persists_progress(self):
        from project_compass.native_tool_trace import CAPTURE
        def pair(call, timestamp):
            return [
                {"type":"response_item","timestamp":timestamp,"payload":{"type":"custom_tool_call","call_id":call,"name":"exec","input":"text(await tools.exec_command({cmd:\"echo public\"}));"}},
                {"type":"response_item","timestamp":timestamp,"payload":{"type":"custom_tool_call_output","call_id":call,"output":[{"type":"input_text","text":"ok"}]}}
            ]
        self.write([*pair('old','2026-10-25T08:01:00Z'), *pair('new','2026-10-25T09:01:00Z')])
        reader=NativeCodexIntake(self.sessions,self.project,THREAD,'developer-a',include_tools=True,max_tool_pairs=1,backfill_batch_size=1)
        first=reader.sync(self.workspace,self.events)
        self.assertEqual(first['captured_tool_pairs'],1)
        self.assertEqual(first['backfill']['state'],'pending')
        self.assertTrue((self.workspace.state_path.parent/'native-backfill.json').exists())
        second=reader.sync(self.workspace,self.events)
        self.assertEqual(second['new_tool_records'],2)
        self.assertEqual(second['backfill']['state'],'complete')
        self.assertEqual({e.metadata.get('native_call_id') for e in self.events if e.metadata.get('capture')==CAPTURE},{'old','new'})
        reopened=NativeCodexIntake(self.sessions,self.project,THREAD,'developer-a',include_tools=True,max_tool_pairs=1,backfill_batch_size=1)
        third=reopened.sync(self.workspace,self.events)
        self.assertEqual(third['new_tool_records'],0)
        self.assertEqual(third['backfill']['batches_completed'],second['backfill']['batches_completed'])


if __name__ == "__main__":
    unittest.main()
