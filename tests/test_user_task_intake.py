from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from project_compass.cli import main
from project_compass.fact_engine import derive_facts
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events


class UserTaskIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        project=Path(self.tmp.name)/'project';project.mkdir()
        self.ws=ProjectWorkspace(Path(self.tmp.name)/'memory',project)

    def source(self,key,text,**metadata):
        return replace(sample_events()[0],id=key,kind='user_message',author='person',content=text,
            observed_at=sample_events()[0].observed_at+timedelta(seconds=int(key) if key.isdigit() else 0),
            metadata={'member_id':'person','role':'user',**metadata})

    def start(self,expected=0):
        with patch('project_compass.cli.run_dashboard') as run:
            self.assertEqual(main(['--open',str(self.ws.project_path),'--workspace-root',str(self.ws.workspace_root),'--web']),expected)
        return run.call_args

    def test_arbitrary_explicit_priorities_keep_full_scope_and_human_attribution(self):
        quotes=['那就先处理缓存失效，按原分工定位、修复并验证，其他功能先不动。',
            '请优先修复上传失败，保留旧记录。','First fix the invoice parser; keep other features unchanged.']
        sources=[self.source(str(i),q) for i,q in enumerate(quotes)];self.ws.save(sources)
        self.start();facts=derive_facts(self.ws.load())
        self.assertEqual({f.quote for f in facts},set(quotes))
        self.assertTrue(all(f.claim_type=='user_statement' and f.author=='person' and f.task_state=='pending' for f in facts))
        self.assertEqual(self.ws.load_review_state()[2],[])
        self.assertEqual([e.to_dict() for e in self.ws.load()[:3]],[e.to_dict() for e in sources])

    def test_suggestions_questions_conditions_quotes_and_withdrawals_are_not_tasks(self):
        quotes=['是否先修复上传？','建议先修复上传。','如果失败，那就先修复上传。',
            '先修复上传，只是假设，不是本轮任务。','先修复上传，不过我撤回这个安排。',
            '> 先修复上传。','下面是引用\n先修复上传。','那就先处理缓存失败？',
            'First fix upload if the proposal gets approved.','先修复上传吗。','先修复上传，前提是得到批准。',
            '先修复上传。这不是我的安排。','先修复上传。我还没决定。']
        self.ws.save([self.source(str(i),q) for i,q in enumerate(quotes)])
        self.start();self.assertEqual(derive_facts(self.ws.load()),[])

    def test_failed_progress_write_deduplicates_and_preserves_direction_progress(self):
        from project_compass.extraction_progress import ExtractionProgress
        self.ws.save([self.source('retry','先修复缓存失效。')])
        original=ExtractionProgress.acknowledge
        def fail_user(progress,updates):
            if progress.path.parent.name=='user-task-rules':raise OSError('synthetic user cursor failure')
            return original(progress,updates)
        with patch.object(ExtractionProgress,'acknowledge',fail_user):self.start(expected=2)
        before=self.ws.state_path.read_bytes()
        direction=(self.ws.state_path.parent/'direction-rules'/'coverage-v1.json').read_bytes()
        status=self.start().kwargs['intake_status']
        self.assertEqual(status['new_user_task_proposals'],0)
        self.assertEqual(self.ws.state_path.read_bytes(),before)
        self.assertEqual((self.ws.state_path.parent/'direction-rules'/'coverage-v1.json').read_bytes(),direction)

    def test_existing_task_quote_is_not_duplicated_and_new_source_is_not_skipped(self):
        from project_compass.candidate_extraction import validate_candidates
        source=self.source('existing','先检查网络恢复。')
        proposals=validate_candidates([source],{'candidates':[{'source_id':source.id,'kind':'task','subject_key':'network',
            'quote':source.content,'task_state':'pending'}]})
        self.ws.save([source,*proposals]);self.start()
        self.assertEqual(len(derive_facts(self.ws.load())),1)
        self.ws.save([self.source('new','优先排查渲染失败。')]);self.start()
        self.assertEqual(len(derive_facts(self.ws.load())),2)

    def test_investigation_can_find_directive_without_promoting_it(self):
        from project_compass.investigator import investigate
        source=self.source('reason','那就先处理缓存失效，其他功能先不动。')
        self.ws.save([source]);self.start()
        answer=investigate(self.ws.load(),'为什么先处理缓存失效，其他功能先不动？').to_dict()
        self.assertEqual(answer['findings'][0]['quote'],source.content)
        self.assertEqual(answer['findings'][0]['claim_type'],'user_statement')
        self.assertEqual(answer['findings'][0]['status'],'proposed')

    def test_approval_gates_and_void_arrangements_never_become_pending_next_actions(self):
        from project_compass.investigator import investigate
        quotes=['先修复上传，这项安排已经作废。','先修复上传，等我确认后再动手。',
            'First fix upload only after owner approval.']
        self.ws.save([self.source(str(i),q) for i,q in enumerate(quotes)]);self.start()
        self.assertEqual(derive_facts(self.ws.load()),[])
        self.assertNotIn(investigate(self.ws.load(),'下一步该做什么？').next_action,quotes)

    def test_retrospective_agent_words_cannot_impersonate_a_human_directive(self):
        quotes=['先修复上传。以上摘自 Agent 的答复。',"First fix upload. These are the agent's words."]
        self.ws.save([self.source(str(i),q) for i,q in enumerate(quotes)]);self.start()
        self.assertEqual(derive_facts(self.ws.load()),[])

    def test_unrelated_work_negation_preserves_positive_task_but_target_negation_does_not(self):
        valid=['请优先修复缓存失效，其他功能不要改。','First fix upload; do not change other features.',
            '先处理登录失败。其余模块先不动。']
        invalid=['先修复上传。上传先不动。','First fix upload; do not fix upload.',
            "First fix upload; don't fix upload."]
        self.ws.save([self.source(str(i),q) for i,q in enumerate([*valid,*invalid])]);self.start()
        self.assertEqual({f.quote for f in derive_facts(self.ws.load())},set(valid))

    def test_only_public_standalone_human_statements_qualify(self):
        sources=[self.source('assistant','先修复上传。',role='assistant'),
            self.source('agent','先修复上传。',actor_type='agent'),self.source('hidden','先修复上传。',phase='analysis'),
            self.source('control','<codex_internal_context>先修复上传。</codex_internal_context>'),
            self.source('qa','<send_user_message_question_reply>[{"question":"先修复上传？","answer":"是"}]</send_user_message_question_reply>')]
        self.ws.save(sources);self.start();self.assertEqual(derive_facts(self.ws.load()),[])

    def test_twelve_proposal_bound_and_reopen_dedup_preserve_other_ledgers(self):
        self.ws.save([self.source(str(i),f'先修复模块{i}错误。') for i in range(15)])
        model=self.ws.state_path.parent/'extraction-cache'/'coverage-v1.json'
        model.parent.mkdir(parents=True);model.write_text('unchanged model ledger',encoding='utf8')
        self.start();self.assertEqual(len(derive_facts(self.ws.load())),12)
        second=self.start();self.assertEqual(len(derive_facts(self.ws.load())),15)
        before=self.ws.state_path.read_bytes();self.start();self.assertEqual(self.ws.state_path.read_bytes(),before)
        self.assertEqual(second.kwargs['intake_status']['user_task_scan']['pending_sources'],0)
        self.assertEqual(model.read_text(encoding='utf8'),'unchanged model ledger')

    def test_two_hundred_source_limit_and_long_source_gap_are_explicit(self):
        self.ws.save([self.source(str(i),'今天记录进展。') for i in range(205)]+[self.source('large','先修复'+('很长的上下文'*400))])
        first=self.start().kwargs['intake_status'].get('user_task_scan',{})
        self.assertEqual(first.get('batch_sources'),200)
        self.assertEqual(first.get('skipped_long_sources'),1)
        self.assertFalse(first.get('semantic_coverage',True))
        second=self.start().kwargs['intake_status']['user_task_scan']
        self.assertEqual(second['pending_sources'],0)

    def test_failed_save_never_acknowledges_user_source(self):
        self.ws.save([self.source('save','先修复上传失败。')]);before=self.ws.state_path.read_bytes()
        with patch.object(ProjectWorkspace,'save',side_effect=OSError('synthetic save failure')):self.start(expected=2)
        self.assertEqual(self.ws.state_path.read_bytes(),before)
        self.assertFalse((self.ws.state_path.parent/'user-task-rules'/'coverage-v1.json').exists())
        self.start();self.assertEqual(len(derive_facts(self.ws.load())),1)


if __name__=='__main__':unittest.main()
