import json
import threading
import unittest
import urllib.request
import urllib.error
from dataclasses import replace
from pathlib import Path
import tempfile
import io
from types import SimpleNamespace

from project_compass.sample_data import sample_events
from project_compass.web_server import create_dashboard_server, _DashboardHandler
from project_compass.cli import _review_callback
from project_compass.native_codex import NativeCodexIntake
from project_compass.project_workspace import ProjectWorkspace
from project_compass.fact_engine import derive_facts


class NativeIntakeWebTest(unittest.TestCase):
    def test_refresh_and_rescan_consume_json_before_responding(self):
        class Connection:
            def settimeout(self,value):pass
        for path,configured in [('/api/refresh',False),('/api/refresh',True),('/api/rescan',True)]:
            with self.subTest(path=path,configured=configured):
                handler=object.__new__(_DashboardHandler)
                handler.path=path;handler.command='POST';handler.headers={'Content-Length':'2'}
                handler.rfile=io.BytesIO(b'{}');handler.connection=Connection()
                handler.server=SimpleNamespace(agent_inbox=None,refresh_callback=(lambda:{'enabled':True}) if configured else None,
                    rescan_callback=lambda:{'enabled':True},state_lock=threading.RLock(),dashboard_state=lambda:{})
                responses=[];handler._json=lambda status,payload:responses.append((status,handler.rfile.tell()))
                handler.do_POST()
                self.assertEqual(responses,[(200 if configured else 409,2)])

    def test_appended_native_message_reaches_web_without_losing_accepted_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, sessions = root / "project", root / "sessions"
            project.mkdir()
            sessions.mkdir()
            thread = "11111111-1111-4111-8111-111111111111"
            path = sessions / f"rollout-{thread}.jsonl"
            header = {"type": "session_meta", "payload": {"id": thread, "cwd": str(project)}}
            path.write_text(json.dumps(header) + "\n", encoding="utf-8")
            workspace = ProjectWorkspace(root / "state", project)
            events = sample_events()
            workspace.save(events)
            review, confirmed, history, accepted = _review_callback(events, workspace, str(project), [], [], [])
            fact = derive_facts(events)[0]
            review("accept_fact", fact.id, "fixture-owner")
            source = NativeCodexIntake(sessions, project, thread, "fixture-owner")
            server = create_dashboard_server(events, port=0, project_path=str(project), confirmed_relations=confirmed,
                review_history=history, fact_reviews=accepted, review_callback=review,
                refresh_callback=lambda: source.sync(workspace, events), intake_status={"enabled": True})
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"type": "response_item", "timestamp": "2026-10-25T08:00:00Z", "payload": {
                        "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Synthetic refresh fixture"}]}}) + "\n")
                request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/refresh", data=b"{}", method="POST")
                with urllib.request.urlopen(request, timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state["project"]["evidence_count"], 3)
                self.assertEqual(state["intake"]["new_messages"], 1)
                self.assertTrue(any(f["id"] == fact.id and f["status"] == "accepted" for f in state["team"]["facts"]))
                self.assertEqual(len(workspace.load_review_state()[2]), 1)
            finally:
                server.shutdown()
                server.server_close()

    def test_refresh_updates_same_board_and_exposes_intake_status(self):
        events = [sample_events()[0]]
        def refresh():
            events.append(replace(events[0], id="later", metadata={}, content="New public message"))
            return {"enabled": True, "updated": True, "captured_messages": 2, "new_messages": 1}
        server = create_dashboard_server(events, port=0, refresh_callback=refresh, intake_status={"enabled": True})
        self.addCleanup(server.server_close)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        request = urllib.request.Request(base + "/api/refresh", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            state = json.load(response)
        self.assertEqual(state["project"]["evidence_count"], 2)
        self.assertEqual(state["intake"]["new_messages"], 1)
        with urllib.request.urlopen(base + "/api/state", timeout=5) as response:
            self.assertEqual(json.load(response)["intake"]["captured_messages"], 2)

    def test_unconfigured_refresh_returns_explicit_conflict(self):
        server = create_dashboard_server(sample_events(), port=0)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/refresh", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 409)


if __name__ == "__main__":
    unittest.main()
