from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import shutil
import subprocess
import unittest

from project_compass.candidate_extraction import validate_candidates
from project_compass.cli import _review_callback
from project_compass.dashboard import build_dashboard_state
from project_compass.fact_engine import derive_facts
from project_compass.investigator import investigate
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events


class DirectionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        target=Path(self.tmp.name)/'project';target.mkdir()
        self.workspace=ProjectWorkspace(Path(self.tmp.name)/'memory',target)

    def material(self,identity,quote,*,subject='option',kind='decision',days=0,member='Alice'):
        source=replace(sample_events()[0],id=identity,kind='user_message',author=member,content=quote,
            observed_at=sample_events()[0].observed_at+timedelta(days=days),
            metadata={'member_id':member,'role':'user','source_ref':'record:'+identity})
        proposals=validate_candidates([source],{'candidates':[{'source_id':identity,'kind':kind,
            'subject_key':subject,'quote':quote,'task_state':'pending' if kind=='task' else 'unknown'}]})
        return [source,*proposals]

    def setup_review(self,events,reviews=()):
        self.workspace.save(events)
        if reviews:self.workspace.save_review_state([],list(reviews),list(reviews))
        callback,confirmed,history,accepted=_review_callback(events,self.workspace,str(self.workspace.project_path),[],list(reviews),list(reviews))
        return callback,history,accepted

    def perform(self,callback,action,fact_id):
        try:return callback(action,fact_id,'owner')
        except ValueError as exc:self.fail('Direction review unexpectedly rejected: '+str(exc))

    def directions(self,state,member=None):
        view=state['personal'][member] if member else state['team']
        self.assertIn('directions',view,'Direction state/history projection is missing')
        return view['directions']

    def test_explicit_quotes_are_candidates_and_unused_suggestion_is_not_abandoned(self):
        events=self.material('drop','决定放弃旧插件方案，先交付网页看板。')
        events+=self.material('defer','暂缓云端同步，等本地流程通过验收。',subject='cloud')
        events+=self.material('unused','建议增加一个新的主题配色。',subject='theme')
        state=build_dashboard_state(events)
        personal=self.directions(state,'Alice')
        self.assertEqual({r['direction_state'] for r in personal['candidates']},{'abandoned','deferred'})
        self.assertTrue(all(r['status']=='proposed' for r in personal['candidates']))
        self.assertEqual(self.directions(state)['current'],[])

    def test_negation_questions_and_mixed_states_are_not_classified(self):
        events=[]
        for i,quote in enumerate(['不要放弃插件方案。','是否应该放弃插件方案？','暂缓A同时放弃B。',
                '不放弃插件方案。','尚未决定暂缓云端同步。','如果预算不够就放弃插件方案。','We should not abandon this plan.']):
            events+=self.material(str(i),quote,subject=str(i))
        self.assertEqual(self.directions(build_dashboard_state(events),'Alice')['candidates'],[])

    def test_legacy_acceptance_does_not_retroactively_confirm_direction_state(self):
        events=self.material('drop','决定放弃旧插件方案。')
        fact=derive_facts(events)[0]
        reviews=[{'action':'accept_fact','fact_id':fact.id,'subject_key':fact.subject_key,'reviewer':'owner'}]
        self.assertEqual(self.directions(build_dashboard_state(events,fact_reviews=reviews))['current'],[])

    def test_owner_confirm_restore_and_restart_preserve_all_history(self):
        events=self.material('defer','暂缓云端同步，先交付本地看板。')
        before=json.dumps([e.to_dict() for e in events],sort_keys=True)
        fact=derive_facts(events)[0];callback,history,accepted=self.setup_review(events)
        first=self.perform(callback,'confirm_deferred',fact.id)
        self.assertEqual(self.directions(first)['current'][0]['direction_state'],'deferred')
        self.assertEqual(first['team']['facts'][0]['status'],'accepted')
        final=self.perform(callback,'restore_direction',fact.id)
        directions=self.directions(final)
        self.assertEqual(directions['current'][0]['direction_state'],'active')
        self.assertEqual([r['direction_state'] for r in directions['history']],['deferred','active'])
        self.assertEqual(len(history),2);self.assertEqual(len(accepted),2)
        self.assertEqual(json.dumps([e.to_dict() for e in events],sort_keys=True),before)
        relations,reopened_history,reopened_reviews=self.workspace.load_review_state()
        reopened=build_dashboard_state(self.workspace.load(),fact_reviews=reopened_reviews,review_history=reopened_history)
        self.assertEqual(self.directions(reopened),directions)

    def test_failed_save_leaves_memory_and_disk_unchanged_and_explicit_retry_works(self):
        events=self.material('drop','决定放弃旧插件方案。');fact=derive_facts(events)[0]
        callback,history,accepted=self.setup_review(events);before=self.workspace.state_path.read_bytes()
        with patch.object(self.workspace,'save_review_state',side_effect=OSError('test-only failure')):
            with self.assertRaises(OSError):callback('confirm_abandoned',fact.id,'owner')
        self.assertEqual(history,[]);self.assertEqual(accepted,[])
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
        result=self.perform(callback,'confirm_abandoned',fact.id)
        self.assertEqual(self.directions(result)['current'][0]['direction_state'],'abandoned')

    def test_untrusted_proposal_does_not_replace_owner_direction(self):
        events=self.material('drop','决定放弃旧插件方案。');fact=derive_facts(events)[0]
        callback,history,accepted=self.setup_review(events)
        self.perform(callback,'confirm_abandoned',fact.id)
        events+=self.material('new','现在建议重新实现插件。',days=1)
        result=build_dashboard_state(events,fact_reviews=accepted,review_history=history)
        self.assertEqual(self.directions(result)['current'][0]['fact_id'],fact.id)
        self.assertEqual(self.directions(result)['current'][0]['direction_state'],'abandoned')

    def test_ordinary_replacement_acceptance_is_not_an_implicit_restore(self):
        events=self.material('drop','决定放弃旧插件方案。')+self.material('other','决定采用另一个方案。',days=1)
        first,other=derive_facts(events);callback,history,accepted=self.setup_review(events)
        self.perform(callback,'confirm_abandoned',first.id)
        result=self.perform(callback,'accept_fact',other.id)
        self.assertEqual(self.directions(result)['current'],[])
        self.assertFalse(self.directions(result)['history'][0]['current'])
        with self.assertRaises(ValueError):callback('restore_direction',first.id,'owner')

    def test_restore_requires_current_confirmation_and_duplicate_direction_is_rejected(self):
        events=self.material('defer','暂缓云端同步。');fact=derive_facts(events)[0]
        callback,history,accepted=self.setup_review(events)
        with self.assertRaises(ValueError):callback('restore_direction',fact.id,'owner')
        self.perform(callback,'confirm_deferred',fact.id)
        with self.assertRaises(ValueError):callback('confirm_deferred',fact.id,'owner')
        self.assertEqual(len(history),1)

    def test_direction_confirmation_requires_matching_explicit_decision_quote(self):
        events=self.material('unused','建议使用插件。')+self.material('task','决定放弃旧插件方案。',subject='task',kind='task')
        callback,history,accepted=self.setup_review(events)
        for fact in derive_facts(events):
            with self.subTest(fact=fact.id),self.assertRaisesRegex(ValueError,'explicit'):
                callback('confirm_abandoned',fact.id,'owner')
        self.assertEqual(history,[])

    def test_investigation_returns_direction_history_without_changing_next_basis(self):
        events=self.material('defer','暂缓云端同步，先交付本地看板。')+self.material('task','复核本地看板的持久化。',subject='next',kind='task')
        decision,task=derive_facts(events);callback,history,accepted=self.setup_review(events)
        self.perform(callback,'accept_fact',task.id)
        self.perform(callback,'confirm_deferred',decision.id)
        self.perform(callback,'restore_direction',decision.id)
        base=investigate(events,'下一步做什么？',accepted).to_dict()
        answer=investigate(events,'为什么暂缓这个方案，现在恢复了吗？',accepted).to_dict()
        self.assertEqual(answer['next_action_basis'],base['next_action_basis'])
        self.assertEqual({f.get('direction_state') for f in answer['findings']},{'deferred','active'})
        self.assertTrue(all(f['quote']==decision.quote for f in answer['findings']))
        self.assertTrue(set(decision.evidence_ids)<=set(answer['evidence_ids']))

    def test_direction_investigation_respects_known_and_unknown_members(self):
        events=self.material('drop','决定放弃旧插件方案。')+self.material('defer','暂缓云端同步。',subject='cloud',member='Bob')
        callback,history,accepted=self.setup_review(events)
        for fact in derive_facts(events):self.perform(callback,'confirm_abandoned' if fact.author=='Alice' else 'confirm_deferred',fact.id)
        answer=investigate(events,'Alice 的舍弃方向是什么？',accepted).to_dict()
        self.assertEqual({f['member_id'] for f in answer['findings']},{'Alice'})
        unknown=investigate(events,'Unknown 的放弃方向是什么？',accepted).to_dict()
        self.assertIsNotNone(unknown['scope'])
        self.assertEqual(unknown['scope']['kind'],'member_choice')
        self.assertEqual(unknown['findings'],[])

    def test_stale_source_cannot_be_confirmed_or_restored(self):
        source=replace(self.material('file','暂缓云端同步。')[0],kind='file_snapshot',metadata={'source_ref':'plan.md'})
        events=[source,*validate_candidates([source],{'candidates':[{'source_id':source.id,'kind':'decision','subject_key':'cloud','quote':source.content}]})]
        fact=derive_facts(events)[0];callback,history,accepted=self.setup_review(events)
        self.perform(callback,'confirm_deferred',fact.id)
        unavailable=replace(source,id='unavailable',kind='file_unavailable',content='not available',metadata={'source_ref':'plan.md','source_id':source.id})
        events.append(unavailable);self.workspace.save([unavailable])
        with self.assertRaisesRegex(ValueError,'source'):callback('restore_direction',fact.id,'owner')
        self.assertEqual(self.directions(build_dashboard_state(events,fact_reviews=accepted))['current'],[])
        self.assertEqual(len(history),1)

    def test_specific_direction_query_keeps_matching_history_and_unknown_topic_gap(self):
        events=self.material('cloud','决定暂缓云端同步方案。',subject='cloud')+self.material('plugin','决定放弃旧插件方案。',subject='plugin')
        callback,history,accepted=self.setup_review(events)
        cloud,plugin=derive_facts(events)
        self.perform(callback,'confirm_deferred',cloud.id);self.perform(callback,'restore_direction',cloud.id)
        self.perform(callback,'confirm_abandoned',plugin.id)
        answer=investigate(events,'云端同步方案为什么暂缓，后来恢复了吗？',accepted).to_dict()
        self.assertEqual({f.get('fact_id') for f in answer['findings']},{cloud.id})
        missing=investigate(events,'支付方案为什么放弃？',accepted).to_dict()
        self.assertEqual(missing['findings'],[])
        self.assertTrue(any('匹配' in gap for gap in missing['evidence_gaps']))

    def test_initial_cli_intake_proposes_an_exact_public_deferral_without_model(self):
        source=self.material('raw','AI 代查等功能继续暂缓。')[0]
        self.workspace.save([source]);before=source.to_dict()
        from project_compass.cli import main
        with patch('project_compass.cli.run_dashboard') as run:
            code=main(['--open',str(self.workspace.project_path),'--workspace-root',str(self.workspace.workspace_root),'--web'])
        self.assertEqual(code,0)
        state=build_dashboard_state(run.call_args.args[0])
        candidate=self.directions(state,'Alice')['candidates']
        self.assertEqual(len(candidate),1)
        self.assertEqual(candidate[0]['quote'],source.content)
        self.assertEqual(candidate[0]['evidence_ids'],[source.id])
        self.assertEqual(self.workspace.load()[0].to_dict(),before)
        self.assertEqual(self.workspace.load()[1].metadata['derivation'],'literal-direction-v1')
        self.assertEqual(self.workspace.load_review_state()[2],[])
        count=len(self.workspace.load())
        with patch('project_compass.cli.run_dashboard'):main(['--open',str(self.workspace.project_path),'--workspace-root',str(self.workspace.workspace_root),'--web'])
        self.assertEqual(len(self.workspace.load()),count)

    def test_raw_generic_list_and_undiscussed_suggestion_do_not_create_directions(self):
        from project_compass.cli import main
        sources=[self.material(str(i),q)[0] for i,q in enumerate(['清单会标明已完成、待验证及暂缓项。','建议使用插件。','如果失败就放弃插件。'])]
        self.workspace.save(sources)
        with patch('project_compass.cli.run_dashboard') as run:main(['--open',str(self.workspace.project_path),'--workspace-root',str(self.workspace.workspace_root),'--web'])
        state=build_dashboard_state(run.call_args.args[0])
        self.assertEqual(state['project']['fact_count'],0)
        self.assertEqual(self.directions(state)['current'],[])

    def test_public_direction_intake_is_bounded_and_resume_deduplicates(self):
        from project_compass.cli import main
        self.workspace.save([self.material(str(i),f'功能{i}继续暂缓。')[0] for i in range(15)])
        command=['--open',str(self.workspace.project_path),'--workspace-root',str(self.workspace.workspace_root),'--web']
        with patch('project_compass.cli.run_dashboard') as run:main(command)
        self.assertEqual(len(derive_facts(run.call_args.args[0])),12)
        with patch('project_compass.cli.run_dashboard') as run:main(command)
        self.assertEqual(len(derive_facts(run.call_args.args[0])),15)
        before=self.workspace.state_path.read_bytes()
        with patch('project_compass.cli.run_dashboard'):main(command)
        self.assertEqual(self.workspace.state_path.read_bytes(),before)

    def test_review_excludes_suggestions_partial_negations_and_mixed_state(self):
        for i,quote in enumerate(['无需放弃旧插件方案。','建议放弃旧插件方案。','决定放弃A，同时继续推进A。']):
            with self.subTest(quote=quote):
                events=self.material('ambiguous-'+str(i),quote,subject=str(i))
                callback,history,accepted=self.setup_review(events)
                with self.assertRaises(ValueError):callback('confirm_abandoned',derive_facts(events)[0].id,'owner')
                self.assertEqual(history,[])
                self.assertEqual(self.directions(build_dashboard_state(events),'Alice')['candidates'],[])

    def test_old_direction_confirmation_cannot_replay_after_restore_or_replacement(self):
        events=self.material('old','决定放弃旧插件方案。')+self.material('new','决定采用新的插件方案。',days=1)
        old,new=derive_facts(events);callback,history,accepted=self.setup_review(events)
        self.perform(callback,'confirm_abandoned',old.id);self.perform(callback,'restore_direction',old.id)
        with self.assertRaises(ValueError):callback('confirm_abandoned',old.id,'owner')
        self.perform(callback,'accept_fact',new.id)
        before=self.workspace.state_path.read_bytes()
        with self.assertRaises(ValueError):callback('confirm_abandoned',old.id,'owner')
        self.assertEqual(self.workspace.state_path.read_bytes(),before)
        self.assertEqual(len(history),3)

    def test_list_and_topic_direction_queries_are_not_unknown_member_requests(self):
        events=self.material('cloud','决定暂缓云端同步方案。',subject='cloud')
        callback,history,accepted=self.setup_review(events)
        fact=derive_facts(events)[0];self.perform(callback,'confirm_deferred',fact.id)
        for question in ['哪些方向已暂缓？','云端同步方案暂缓了吗？','云端同步方案的暂缓记录是什么？']:
            with self.subTest(question=question):
                answer=investigate(events,question,accepted).to_dict()
                self.assertIsNone(answer['scope'])
                self.assertEqual({f['fact_id'] for f in answer['findings']},{fact.id})

    def test_general_state_answer_labels_restored_direction_beside_original_quote(self):
        events=self.material('cloud','决定暂缓云端同步方案。',subject='cloud')
        callback,history,accepted=self.setup_review(events);fact=derive_facts(events)[0]
        self.perform(callback,'confirm_deferred',fact.id);self.perform(callback,'restore_direction',fact.id)
        answer=investigate(events,'为什么项目会处于当前状态？',accepted).to_dict()
        finding=next(f for f in answer['findings'] if f['fact_id']==fact.id)
        self.assertEqual(finding.get('direction_state'),'active')
        self.assertTrue(finding['current'])
        self.assertEqual(finding['quote'],fact.quote)

    @unittest.skipUnless(shutil.which('node'),'Node required for actual app verification')
    def test_actual_web_direction_rendering_and_actions(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node',str(root/'tests/web_direction_history_test.cjs'),str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'),'Node required for startup race verification')
    def test_investigation_waits_for_initial_project_state(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node',str(root/'tests/web_investigation_ready_test.cjs'),str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
