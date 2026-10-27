from dataclasses import replace
from datetime import timedelta
import unittest

from project_compass.candidate_extraction import validate_candidates, select_batch
from project_compass.fact_engine import derive_facts
from project_compass.projections import build_views
from project_compass.evidence_linking import build_evidence_index
from project_compass.investigator import investigate
from project_compass.sample_data import sample_events


class CandidateExtractionTest(unittest.TestCase):
    def test_bound_legacy_source_owner_precedes_extracting_member(self):
        source=replace(self.source(),metadata={'role':'assistant'})
        proposals=validate_candidates([source],self.payload(),member_id='extractor-user',
            session_members={source.session_id:'source-owner'})
        self.assertEqual(proposals[0].metadata['member_id'],'source-owner')

    def test_extracting_member_cannot_reassign_an_explicit_source_owner(self):
        source=self.source()
        proposals=validate_candidates([source],self.payload(),member_id='another-developer')
        self.assertEqual(proposals[0].metadata['member_id'],'developer-a')
        views=build_views([source,*proposals])
        self.assertIn('developer-a',views['personal'])
        self.assertNotIn('another-developer',views['personal'])

    def source(self):
        return replace(sample_events()[0], id="public-1", author="codex", kind="assistant_message",
                       content="The candidate was built. Phone verification is still pending.",
                       metadata={"role": "assistant", "member_id": "developer-a", "source_ref": "session:42"})

    def payload(self, **updates):
        candidate = {"source_id": "public-1", "kind": "task", "subject_key": "candidate.phone-check",
                     "quote": "Phone verification is still pending.", "task_state": "pending"}
        candidate.update(updates)
        return {"candidates": [candidate]}

    def test_verbatim_candidate_cites_original_material_and_stays_proposed(self):
        source = self.source()
        proposals = validate_candidates([source], self.payload())
        facts = derive_facts([source, *proposals])
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].evidence_ids, (source.id,))
        self.assertEqual(facts[0].status.value, "proposed")
        self.assertEqual(facts[0].quote, "Phone verification is still pending.")
        self.assertEqual(facts[0].claim_type, "assistant_report")
        self.assertEqual(facts[0].source_ref, "session:42")
        self.assertNotIn("facts", source.metadata)

    def test_missing_source_or_fabricated_quote_is_rejected(self):
        for updates in ({"source_id": "missing"}, {"quote": "Phone verification passed."}, {"quote": ""}, {"kind": "accepted"}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_candidates([self.source()], self.payload(**updates))

    def test_proposal_identity_is_stable_and_preserves_shared_semantic_subject(self):
        source = self.source()
        first = validate_candidates([source], self.payload())
        self.assertEqual(first[0].id, validate_candidates([source], self.payload())[0].id)
        newer = replace(source, id="public-2")
        second = validate_candidates([newer], self.payload(source_id=newer.id))
        self.assertNotEqual(first[0].id, second[0].id)
        self.assertEqual(derive_facts([source, *first])[0].subject_key,
                         derive_facts([newer, *second])[0].subject_key)

    def test_two_reports_for_one_subject_remain_a_visible_conflict(self):
        first = self.source()
        second = replace(first, id="other", content="Phone verification is complete.", author="other-developer")
        proposals = validate_candidates([first, second], {"candidates": [self.payload()["candidates"][0],
            self.payload(source_id=second.id, quote=second.content, task_state="reported_complete")["candidates"][0]]})
        self.assertEqual(len(build_views([first, second, *proposals])["team"]["conflicts"]), 1)

    def test_newest_pending_source_is_selected_instead_of_append_order(self):
        older = self.source()
        newer = replace(older, id="newer", content="Verify candidate v2.", observed_at=older.observed_at + timedelta(days=1))
        proposals = validate_candidates([older, newer], {"candidates": [self.payload()["candidates"][0],
            self.payload(source_id=newer.id, subject_key="candidate-v2", quote=newer.content)["candidates"][0]]})
        self.assertEqual(investigate([older, newer, *proposals], "下一步做什么？").next_action, newer.content)

    def test_one_developers_user_and_assistant_facts_share_personal_board(self):
        assistant = self.source()
        user = replace(assistant, id="public-user", author="owner", kind="user_message",
                       metadata={**assistant.metadata, "role": "user"})
        proposals = validate_candidates([assistant, user], {"candidates": [
            self.payload()["candidates"][0], self.payload(source_id=user.id)["candidates"][0]]})
        views = build_views([assistant, user, *proposals])
        self.assertEqual(list(views["personal"]), ["developer-a"])
        self.assertEqual({fact["claim_type"] for fact in views["personal"]["developer-a"]["facts"]},
                         {"assistant_report", "user_statement"})
        self.assertTrue(all(f["status"] == "proposed" for f in views["team"]["facts"]))

    def test_tampered_persisted_proposal_cannot_bypass_quote_validation(self):
        source = self.source()
        proposal = validate_candidates([source], self.payload())[0]
        bad = replace(proposal, metadata={**proposal.metadata, "facts": [
            {**proposal.metadata["facts"][0], "quote": "Invented approval"}]})
        with self.assertRaises(ValueError):
            derive_facts([source, bad])

    def test_source_role_wins_over_a_tampered_claim_type(self):
        source = self.source()
        proposal = validate_candidates([source], self.payload())[0]
        tampered = replace(proposal, metadata={**proposal.metadata, "facts": [
            {**proposal.metadata["facts"][0], "claim_type": "user_statement"}]})
        self.assertEqual(derive_facts([source, tampered])[0].claim_type, "assistant_report")

    def test_derived_proposal_is_not_its_own_supporting_raw_evidence(self):
        source = self.source()
        proposals = validate_candidates([source], self.payload())
        index = build_evidence_index([source, *proposals])
        self.assertEqual([e.id for e in index.catalog], [source.id])
        self.assertEqual({r.evidence_id for r in index.candidate_relations}, {source.id})
        self.assertEqual(build_views([source, *proposals])["evidence_count"], 1)

    def test_historical_platform_control_record_is_retained_but_excluded_from_sources(self):
        source = self.source()
        control = replace(source, id="platform-control", kind="user_message",
                          content='<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>')
        events = [source, control]
        self.assertEqual([e.id for e in build_evidence_index(events).catalog], [source.id])
        self.assertEqual([e.id for e in select_batch(events)], [source.id])
        self.assertEqual(build_views(events)["evidence_count"], 1)
        self.assertEqual(len(events), 2)

    def test_real_candidates_answer_next_action_without_claiming_owner_acceptance(self):
        source = self.source()
        proposals = validate_candidates([source], self.payload())
        answer = investigate([source, *proposals], "下一步该做什么？").to_dict()
        self.assertEqual(answer["next_action"], "Phone verification is still pending.")
        self.assertTrue(answer["findings"])
        self.assertEqual(answer["findings"][0]["evidence_ids"], [source.id])
        self.assertTrue(answer["evidence_gaps"])
        self.assertNotIn("按已确认", answer["next_action"])

    def test_direction_and_blocker_questions_select_different_source_claims(self):
        source = replace(self.source(), content="Use the local workflow. Phone verification is still pending.")
        proposals = validate_candidates([source], {"candidates": [
            self.payload(kind="decision", quote="Use the local workflow.", task_state="unknown")["candidates"][0],
            self.payload()["candidates"][0],
        ]})
        direction = investigate([source, *proposals], "为什么改变方向？").to_dict()
        blockers = investigate([source, *proposals], "当前有哪些未完成任务？").to_dict()
        self.assertEqual(direction["findings"][0]["quote"], "Use the local workflow.")
        self.assertEqual(blockers["findings"][0]["quote"], "Phone verification is still pending.")

    def test_current_state_answer_surfaces_owner_acceptance_and_its_reviewer(self):
        source = replace(self.source(), content="Phone verification is still pending. The diagnostic result needs rechecking.")
        proposals = validate_candidates([source], {"candidates": [self.payload()["candidates"][0],
            self.payload(kind="assumption", subject_key="diagnostic", quote="The diagnostic result needs rechecking.", task_state="unknown")["candidates"][0]]})
        events = [source, *proposals]
        fact = next(fact for fact in derive_facts(events) if fact.kind.value == "task")
        reviews = [{"action": "accept_fact", "fact_id": fact.id, "subject_key": fact.subject_key,
                    "reviewer": "owner", "observed_at": "2026-10-25T07:12:24+00:00"}]
        answer = investigate(events, "为什么是当前状态，下一步是什么？", fact_reviews=reviews).to_dict()
        self.assertEqual(answer["findings"][0]["status"], "accepted")
        self.assertEqual(answer["findings"][0]["reviewed_by"], "owner")
        self.assertEqual(answer["next_action_status"], "accepted")
        self.assertTrue(any(item["status"] == "proposed" for item in answer["findings"]))

    def test_batch_is_bounded_and_skips_existing_proposals(self):
        source = self.source()
        proposal = validate_candidates([source], self.payload())[0]
        batch = select_batch([source, proposal], max_chars=100)
        self.assertEqual([item.id for item in batch], [source.id])
        self.assertLessEqual(sum(len(item.content) for item in batch), 100)

    def test_many_reviews_do_not_crowd_out_goal_context_and_latest_conversation(self):
        source = self.source()
        documents = [replace(source, id=f"review-{i}", kind="file_snapshot", content="r" * 3000,
                             metadata={"source_ref": f"docs/reviews/{i}.md"}) for i in range(8)]
        context = replace(source, id="context", kind="file_snapshot", content="Goal context", metadata={"source_ref": "CONTEXT.md"})
        batch = select_batch([*documents, context, source])
        self.assertIn("context", {e.id for e in batch})
        self.assertIn(source.id, {e.id for e in batch})


if __name__ == "__main__":
    unittest.main()
