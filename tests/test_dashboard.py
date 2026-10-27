import json
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from project_compass.dashboard import build_dashboard_state
from project_compass.evidence_linking import build_evidence_index, confirm_relation
from project_compass.fact_engine import derive_facts
from project_compass.sample_data import sample_events
from project_compass.web_server import create_dashboard_server


class DashboardTest(unittest.TestCase):
    def test_state_keeps_personal_team_and_relation_layers(self):
        payload = build_dashboard_state(sample_events())

        self.assertIn("personal", payload)
        self.assertIn("team", payload)
        self.assertIn("evidence", payload)
        self.assertIn("candidate_relations", payload)
        self.assertIn("confirmed_relations", payload)
        self.assertNotEqual(payload["personal"], payload["team"])

    def test_http_api_serves_state_and_exact_evidence(self):
        events = sample_events()
        server = create_dashboard_server(events, host="127.0.0.1", port=0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base + "/api/state") as response:
                state = json.loads(response.read().decode("utf-8"))
            self.assertIn("team", state)
            self.assertIn("evidence", state)

            with urlopen(base + "/api/evidence?id=e1") as response:
                evidence = json.loads(response.read().decode("utf-8"))
            self.assertEqual(evidence["id"], "e1")
            self.assertIn("content", evidence)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_investigation_answers_from_team_truth_and_evidence(self):
        events = sample_events()
        selected = next(fact for fact in derive_facts(events) if fact.value.endswith("[active]"))
        server = create_dashboard_server(
            events,
            host="127.0.0.1",
            port=0,
            fact_reviews=[{"action": "accept_fact", "fact_id": selected.id, "subject_key": selected.subject_key, "reviewer": "owner"}],
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            request = Request(
                base + "/api/investigate",
                data=json.dumps({"question": "Why is the project in its current state, and what should happen next?"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                answer = json.loads(response.read().decode("utf-8"))
            self.assertIn(selected.value, answer["active_tasks"])
            self.assertIn("e1", answer["evidence_ids"])
            self.assertEqual(answer["conflicts"][0]["sides"][0]["status"], "accepted")
            self.assertTrue(answer["next_action"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_review_confirms_relation_and_rejects_blank_reviewer(self):
        events = sample_events()
        confirmed = []

        def review(action, relation_id, reviewer):
            if action != "confirm_relation":
                raise ValueError("unsupported review action")
            if not reviewer.strip():
                raise ValueError("reviewer is required")
            candidate = next(item for item in build_evidence_index(events).candidate_relations if item.id == relation_id)
            relation = confirm_relation(candidate, reviewer)
            confirmed.append({"relation_id": relation_id, "reviewer": reviewer})
            return build_dashboard_state(events, confirmed_relations=[relation], review_history=confirmed)

        server = create_dashboard_server(events, host="127.0.0.1", port=0, review_callback=review)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base + "/api/state") as response:
                initial = json.loads(response.read().decode("utf-8"))
            relation_id = initial["candidate_relations"][0]["id"]
            request = Request(
                base + "/api/review",
                data=json.dumps({"relation_id": relation_id, "reviewer": "owner"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertIn("team", payload)
            self.assertEqual(confirmed, [{"relation_id": relation_id, "reviewer": "owner"}])
            with urlopen(base + "/api/state") as response:
                current = json.loads(response.read().decode("utf-8"))
            self.assertEqual(current["review_history"], confirmed)
            self.assertEqual(len(current["confirmed_relations"]), 1)

            invalid = Request(
                base + "/api/review",
                data=json.dumps({"relation_id": relation_id, "reviewer": " "}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(invalid)
            self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_accept_fact_updates_team_truth_and_keeps_conflict_history(self):
        events = sample_events()
        fact_reviews = []

        def review(action, fact_id, reviewer):
            if action != "accept_fact" or not reviewer.strip():
                raise ValueError("reviewer is required")
            fact_reviews.append({"action": action, "fact_id": fact_id, "reviewer": reviewer})
            return build_dashboard_state(events, fact_reviews=fact_reviews, review_history=fact_reviews)

        server = create_dashboard_server(events, host="127.0.0.1", port=0, review_callback=review)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base + "/api/state") as response:
                initial = json.loads(response.read().decode("utf-8"))
            selected = initial["team"]["facts"][0]["id"]
            request = Request(
                base + "/api/review",
                data=json.dumps({"action": "accept_fact", "fact_id": selected, "reviewer": "owner"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                reviewed = json.loads(response.read().decode("utf-8"))
            team_fact = next(item for item in reviewed["team"]["facts"] if item["id"] == selected)
            self.assertEqual(team_fact["status"], "accepted")
            self.assertEqual(len(reviewed["team"]["conflicts"]), 1)
            self.assertEqual(len(reviewed["review_history"]), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_cli_web_flag_delegates_scanned_events_to_dashboard(self):
        from project_compass.cli import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Demo", encoding="utf-8")
            with patch("project_compass.cli.run_dashboard") as run_dashboard:
                result = main(["--scan", str(root), "--web", "--port", "8799"])

        self.assertEqual(result, 0)
        run_dashboard.assert_called_once()
        self.assertEqual(run_dashboard.call_args.kwargs["port"], 8799)
        self.assertEqual(run_dashboard.call_args.kwargs["project_path"], str(root.resolve()))

    def test_layered_view_contract_exposes_overview_process_and_source_layers(self):
        root=Path(__file__).parents[1]/'src/project_compass/web'
        html=(root/'index.html').read_text(encoding='utf8')
        app=(root/'app.js').read_text(encoding='utf8')
        self.assertIn('data-layer="overview"',html)
        self.assertIn('data-layer="process"',html)
        self.assertIn('data-layer="source"',html)
        self.assertIn('概览',html);self.assertIn('过程',html);self.assertIn('原文',html)
        self.assertIn('backfill',app)


if __name__ == "__main__":
    unittest.main()
