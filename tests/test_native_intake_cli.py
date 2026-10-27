import io
from contextlib import redirect_stdout
import json
from pathlib import Path
import tempfile
import unittest

from project_compass.cli import main
from project_compass.project_workspace import ProjectWorkspace


class NativeIntakeCliTest(unittest.TestCase):
    def test_scan_imports_selected_native_thread_without_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, sessions = root / "project", root / "sessions"
            project.mkdir()
            sessions.mkdir()
            thread = "11111111-1111-4111-8111-111111111111"
            rows = [{"type": "session_meta", "payload": {"id": thread, "cwd": str(project)}},
                    {"type": "response_item", "timestamp": "2026-10-25T08:00:00Z", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Keep the phone test pending."}]}}]
            (sessions / f"rollout-{thread}.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                result = main(["--scan", str(project), "--codex-thread", thread, "--member-id", "dev",
                               "--codex-sessions-root", str(sessions), "--workspace-root", str(root / "state"),
                               "--env-file", str(root / "missing.env")])
            self.assertEqual(result, 0)
            loaded = ProjectWorkspace(root / "state", project).load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].metadata["member_id"], "dev")
            with (sessions / f'rollout-{thread}.jsonl').open('a',encoding='utf8') as handle:
                for kind,payload in [('custom_tool_call',{'name':'exec','input':'text(await tools.exec_command({cmd:"synthetic text only"}));'}),
                                     ('custom_tool_call_output',{'output':[{'type':'input_text','text':'synthetic output'}]})]:
                    handle.write(json.dumps({'type':'response_item','timestamp':'2026-10-25T08:01:00Z',
                        'payload':{'type':kind,'call_id':'cli-pair',**payload}})+'\n')
            with redirect_stdout(io.StringIO()):
                result=main(['--scan',str(project),'--codex-thread',thread,'--codex-tools','--member-id','dev',
                    '--codex-sessions-root',str(sessions),'--workspace-root',str(root/'state'),'--env-file',str(root/'missing.env')])
            self.assertEqual(result,0)
            self.assertEqual(len(ProjectWorkspace(root/'state',project).load()),3)


if __name__ == "__main__":
    unittest.main()
