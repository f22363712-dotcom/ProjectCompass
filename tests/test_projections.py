from datetime import datetime, timezone
from dataclasses import replace
import unittest

from project_compass.fact_engine import derive_facts
from project_compass.models import EvidenceEvent
from project_compass.projections import build_views


def event(event_id, author, value):
    return EvidenceEvent(
        event_id,
        f"session-{author}",
        author,
        "assistant_message",
        value,
        datetime(2026, 10, 24, tzinfo=timezone.utc),
        {"facts": [{"kind": "task", "subject_key": "task.demo", "value": value}]},
    )


class ProjectionTest(unittest.TestCase):
    def test_bound_legacy_session_groups_agent_records_under_developer(self):
        legacy=replace(event('legacy','codex','Public legacy report'),metadata={'role':'assistant'})
        explicit=replace(legacy,id='explicit',metadata={'role':'assistant','member_id':'bob'})
        views=build_views([legacy,explicit],session_members={legacy.session_id:'alice'})
        self.assertEqual(views['personal']['alice']['evidence_ids'],['legacy'])
        self.assertEqual(views['personal']['bob']['evidence_ids'],['explicit'])
        self.assertNotIn('codex',views['personal'])

    def test_raw_records_create_owner_views_without_waiting_for_semantic_facts(self):
        first=replace(event('a','alice','Public user plan'),metadata={'member_id':'alice','role':'user'},kind='user_message')
        assistant=replace(first,id='b',author='codex',kind='assistant_message',content='Public assistant report',metadata={'member_id':'alice','role':'assistant'})
        other=replace(first,id='c',author='bob',metadata={'member_id':'bob','role':'user'})
        document=replace(first,id='d',author='scanner',kind='file_snapshot',metadata={'source_ref':'README.md'})
        views=build_views([first,assistant,other,document])
        self.assertEqual(set(views['personal']['alice']['evidence_ids']),{'a','b'})
        self.assertEqual(views['personal']['alice']['facts'],[])
        self.assertEqual(views['personal']['bob']['evidence_ids'],['c'])
        self.assertEqual(views['personal']['project-documents']['evidence_ids'],['d'])
        self.assertEqual(set(views['team']['evidence_ids']),{'a','b','c','d'})

    def test_team_truth_preserves_conflict_and_marks_owner_selected_fact(self):
        events = [event("e1", "alice", "Ship board"), event("e2", "bob", "Pause board")]
        facts = derive_facts(events)
        selected = next(fact for fact in facts if fact.value == "Ship board")

        views = build_views(events, accepted_fact_ids=[selected.id])

        team_facts = {fact["id"]: fact for fact in views["team"]["facts"]}
        self.assertEqual(team_facts[selected.id]["status"], "accepted")
        self.assertEqual(len(views["team"]["conflicts"]), 1)
        self.assertEqual(
            {side["value"] for side in views["team"]["conflicts"][0]["sides"]},
            {"Ship board", "Pause board"},
        )
        self.assertEqual(views["personal"]["alice"]["facts"][0]["status"], "conflicted")


if __name__ == "__main__":
    unittest.main()
