"""Specific follow-up questions should retrieve the existing relevant quote."""
from dataclasses import replace
from datetime import timedelta
import unittest

from project_compass.candidate_extraction import validate_candidates
from project_compass.fact_engine import derive_facts
from project_compass.investigator import investigate
from project_compass.sample_data import sample_events


class QuestionRelevanceTests(unittest.TestCase):
    def record(self, identity, quote, kind='decision', *, member='Alice', days=0):
        source=replace(sample_events()[0],id=identity,author='codex',kind='assistant_message',
            content=quote,observed_at=sample_events()[0].observed_at+timedelta(days=days),
            metadata={'member_id':member,'role':'assistant'})
        payload={'source_id':identity,'kind':kind,'subject_key':identity,'quote':quote,
            'task_state':'pending' if kind=='task' else 'unknown'}
        return [source,*validate_candidates([source],{'candidates':[payload]})]

    def records(self):
        return (self.record('old-choice','选择本地检索索引；允许离线查找，证据片段留在本机。')+
            self.record('new-layout','更新按钮布局，保留原来的页面间距。',days=2)+
            self.record('next','检查候选包的安装情况。','task',days=3)+
            self.record('gap','数据库迁移的结果仍待验证。','assumption',days=4))

    def test_chinese_why_finds_older_decision_instead_of_generic_current_state(self):
        events=self.records()
        answer=investigate(events,'为什么选择本地检索索引？').to_dict()
        self.assertEqual(answer['findings'][0]['quote'],events[0].content)
        self.assertEqual(answer['findings'][0]['status'],'proposed')
        self.assertIn('old-choice',answer['findings'][0]['evidence_ids'])

    def test_specific_direction_question_ranks_topic_before_newer_unrelated_decision(self):
        events=self.records()
        answer=investigate(events,'本地检索索引的决策是什么？').to_dict()
        self.assertEqual(answer['findings'][0]['quote'],events[0].content)

    def test_why_does_not_broaden_an_explicit_decision_question_to_tasks(self):
        events=self.record('choice','Use DeltaCache for the index.')+self.record(
            'later-task','Review DeltaCache before installation.','task',days=3)
        answer=investigate(events,'为什么这个决策选择 DeltaCache？').to_dict()
        self.assertTrue(answer['findings'])
        self.assertTrue(all(f['kind']=='decision' for f in answer['findings']))

    def test_why_does_not_broaden_an_explicit_blocker_question_to_decisions(self):
        events=self.record('blocked','DeltaCache storage is unverified.','assumption')+self.record(
            'later-choice','Use DeltaCache for the index.',days=3)
        answer=investigate(events,'为什么项目 DeltaCache 仍然阻塞？').to_dict()
        self.assertTrue(answer['findings'])
        self.assertTrue(all(f['kind']=='assumption' for f in answer['findings']))

    def test_ascii_identifiers_are_case_insensitive_and_repetition_does_not_dominate(self):
        exact=self.record('choice','Use deterministic DeltaCache lookup for archived evidence.')
        noisy=self.record('noise','The cache view changed. '*20,days=3)
        answer=investigate(exact+noisy,'Why choose DELTACACHE?').to_dict()
        self.assertTrue(answer['findings'],'Specific why question missed the existing decision')
        self.assertEqual(answer['findings'][0]['quote'],exact[0].content)

    def test_ascii_identifier_before_sentence_punctuation_still_matches(self):
        events=self.record('choice','Use DeltaCache.')
        answer=investigate(events,'Why choose DeltaCache?').to_dict()
        self.assertTrue(answer['findings'],'Sentence punctuation hid the existing identifier')
        self.assertEqual(answer['findings'][0]['quote'],'Use DeltaCache.')

    def test_fullwidth_topic_and_long_suffix_keep_exact_original_quote(self):
        events=self.record('choice','Use DeltaCache for local lookup.')
        answer=investigate(events,'Why choose ＤＥＬＴＡＣＡＣＨＥ？'+'噪声'*5000).to_dict()
        self.assertTrue(answer['findings'])
        self.assertEqual(answer['findings'][0]['quote'],events[0].content)

    def test_equal_literal_scores_keep_existing_recency_order(self):
        events=self.record('old','Use DeltaCache for cold storage.')+self.record(
            'new','Use DeltaCache for warm storage.',days=2)
        answer=investigate(events,'DeltaCache 的决策是什么？').to_dict()
        self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['new','old'])

    def test_matching_an_old_file_does_not_resurrect_superseded_evidence(self):
        source=replace(sample_events()[0],id='old-file',kind='file_snapshot',author='scanner',
            content='Use DeltaCache for lookup.',metadata={'source_ref':'plan.md'})
        newer=replace(source,id='new-file',content='Retain the existing index.',
            observed_at=source.observed_at+timedelta(days=1))
        events=[]
        for record in [source,newer]:
            events.extend([record,*validate_candidates([record],{'candidates':[{
                'source_id':record.id,'kind':'decision','subject_key':'index',
                'quote':record.content,'task_state':'unknown'}]})])
        answer=investigate(events,'为什么这个决策选择 DeltaCache？').to_dict()
        self.assertTrue(all('old-file' not in f['evidence_ids'] for f in answer['findings']))
        self.assertTrue(answer['source_warnings'])

    def test_literal_matching_never_imports_another_members_subject(self):
        events=self.record('alice','Alice keeps the existing screen.',member='Alice')+self.record(
            'bob','Use DeltaCache for archived evidence.',member='Bob')
        answer=investigate(events,'Alice 为什么选择 DeltaCache？').to_dict()
        self.assertEqual(answer['scope']['member_id'],'Alice')
        self.assertTrue(all(f['member_id']=='Alice' for f in answer['findings']))
        self.assertNotIn('bob',answer['evidence_ids'])

    def test_broad_member_name_and_routing_words_do_not_hide_newer_work(self):
        events=self.record('old','Alice recently 负责 old layout. 最近的记录。')+self.record(
            'new','Verify the installation.','task',days=3)
        for question in ['Alice 做了什么？','看看 Alice 最近做了什么？',
                         'What did Alice do recently?','Alice负责什么？']:
            with self.subTest(question=question):
                answer=investigate(events,question).to_dict()
                self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['new','old'])
                self.assertEqual(answer['question'],question)

    def test_long_member_identity_cannot_outweigh_specific_topic(self):
        member='developer-alice-long-name'
        events=self.record('choice','Use DeltaCache for archived evidence.',member=member)+self.record(
            'name',member+' kept the layout.',member=member,days=3)
        answer=investigate(events,member+' 为什么选择 DeltaCache？').to_dict()
        self.assertEqual(answer['findings'][0]['evidence_ids'],['choice'])
        self.assertEqual(answer['scope']['member_id'],member)

    def test_member_strip_preserves_a_topic_identifier_with_the_same_prefix(self):
        events=self.record('choice','Use AnnCache.',member='Ann')+self.record(
            'new','Retain the screen.','task',member='Ann',days=3)
        answer=investigate(events,'Ann 为什么选择 AnnCache？').to_dict()
        self.assertEqual(answer['findings'][0]['evidence_ids'],['choice'])

    def test_topic_ranking_does_not_change_owner_confirmed_next_action(self):
        events=self.records()
        task=next(f for f in derive_facts(events) if f.kind.value=='task')
        reviews=[{'action':'accept_fact','fact_id':task.id,'subject_key':task.subject_key,'reviewer':'owner'}]
        before=[e.to_dict() for e in events]
        answer=investigate(events,'为什么选择本地检索索引？',reviews).to_dict()
        self.assertEqual(answer['findings'][0]['quote'],events[0].content)
        self.assertEqual(answer['next_action_basis']['fact_id'],task.id)
        self.assertEqual(answer['next_action_status'],'accepted')
        self.assertEqual([e.to_dict() for e in events],before)

    def test_broad_state_question_and_unsupported_question_keep_existing_contract(self):
        events=self.records()
        task=next(f for f in derive_facts(events) if f.kind.value=='task')
        reviews=[{'action':'accept_fact','fact_id':task.id,'subject_key':task.subject_key,'reviewer':'owner'}]
        broad=investigate(events,'为什么项目会处于当前状态，下一步该做什么？',reviews).to_dict()
        self.assertEqual(broad['findings'][0]['fact_id'],task.id)
        unsupported=investigate(events,'Translate the DeltaCache note to French.').to_dict()
        self.assertEqual(unsupported['findings'],[])
        self.assertTrue(any('尚未得到回答' in gap for gap in unsupported['evidence_gaps']))

    def test_english_broad_state_routing_words_do_not_rerank_unrelated_decision(self):
        events=self.records()+self.record('title','Current metadata belongs in the index.',days=6)
        task=next(f for f in derive_facts(events) if f.kind.value=='task')
        reviews=[{'action':'accept_fact','fact_id':task.id,'subject_key':task.subject_key,'reviewer':'owner'}]
        answer=investigate(events,'Why is the project in its current state, and what should happen next?',reviews).to_dict()
        self.assertEqual(answer['findings'][0]['fact_id'],task.id)


if __name__=='__main__':unittest.main()
