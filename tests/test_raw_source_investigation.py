from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import copy
import shutil
import subprocess
import unittest

from project_compass.candidate_extraction import validate_candidates
from project_compass.fact_engine import derive_facts
from project_compass.investigator import investigate
from project_compass.native_command_trace import CAPTURE
from project_compass.sample_data import sample_events
from project_compass.web_server import create_dashboard_server
from project_compass.zetaris_provider import EvidenceRetrieval


class RawSourceInvestigationTests(unittest.TestCase):
    def event(self, identity, kind, content, *, member='Alice', days=0, **metadata):
        return replace(sample_events()[0],id=identity,kind=kind,author='codex',content=content,
            observed_at=sample_events()[0].observed_at+timedelta(days=days),
            metadata={'member_id':member,**metadata})

    def command(self, identity, output='Actual output', *, command=None, **updates):
        return self.event(identity,'command_execution',output,command=command or ['python','checks.py'],capture=CAPTURE,**updates)

    def test_unknown_named_class_does_not_fall_back_to_generic_native_output(self):
        events=[self.command('other','原生模块的原始输出与说明',command=['pwsh','-Command','Get-Content README.md'],exit_code=0)]
        answer=investigate(events,'项目中 MissingRecoveryPolicyTest 的原生命令和原始输出是什么？').to_dict()
        self.assertEqual(answer['findings'],[])
        self.assertTrue(any('未找到匹配' in gap for gap in answer['evidence_gaps']))

    def test_named_class_matches_command_filename_without_borrowing_longer_class_or_boilerplate(self):
        events=[self.command('other','原生模块的原始输出',command=['cat','Other.java']),
            self.command('longer','原生输出',command=['cat','MissingRecoveryPolicyTestOther.java']),
            self.command('right','Read failed; no test was executed.',command=['rg','@Test','src/MissingRecoveryPolicyTest.java'],exit_code=1)]
        answer=investigate(events,'项目中 MissingRecoveryPolicyTest 的原生命令和原始输出是什么？').to_dict()
        self.assertEqual([f['evidence_ids'] for f in answer['findings']],[['right']])
        self.assertEqual(answer['findings'][0]['exit_code'],1)
        self.assertEqual(answer['findings'][0]['status'],'raw')

    def test_named_filename_does_not_match_a_backup_suffix(self):
        events=[self.command('backup','原生模块输出',command=['cat','Widget.java.bak']),
            self.command('right','file contents',command=['cat','src/Widget.java'])]
        answer=investigate(events,'项目 Widget.java 的原生命令和原始输出是什么？').to_dict()
        self.assertEqual([f['evidence_ids'] for f in answer['findings']],[['right']])

    def test_camel_case_member_name_is_not_a_code_subject(self):
        event=self.command('member-output',member='BuildOwner',exit_code=0)
        answer=investigate([event],'BuildOwner 的原生命令和原始输出是什么？').to_dict()
        self.assertEqual(answer['scope']['member_id'],'BuildOwner')
        self.assertEqual([f['evidence_ids'] for f in answer['findings']],[['member-output']])

    def test_unquoted_qualified_paths_keep_directory_constraints_across_slash_styles(self):
        for query_path in ['src/Widget.java',r'src\Widget.java']:
            for stored_path in ['repo/src/Widget.java',r'C:\repo\src\Widget.java']:
                with self.subTest(query=query_path,stored=stored_path):
                    wrong=self.command('other-dir','other contents',command=['cat','other/Widget.java'])
                    right=self.command('right','requested contents',command=['cat',stored_path])
                    question=f'项目 {query_path} 的命令输出是什么？'
                    self.assertEqual([f['evidence_ids'] for f in investigate([wrong,right],question).to_dict()['findings']],[['right']])
                    self.assertEqual(investigate([wrong],question).to_dict()['findings'],[])

    def test_leading_captured_class_and_filename_subjects_are_not_unknown_members(self):
        for subject,path in [('MissingRecoveryPolicyTest','src/MissingRecoveryPolicyTest.java'),('Widget.java','src/Widget.java')]:
            with self.subTest(subject=subject):
                event=self.command('right','read output',command=['cat',path])
                answer=investigate([event],subject+' 的命令输出是什么？').to_dict()
                self.assertIsNone(answer['scope'])
                self.assertEqual([f['evidence_ids'] for f in answer['findings']],[['right']])

    def test_code_scope_preserves_member_priority_and_does_not_assign_unknown_people(self):
        member=self.command('known',member='BuildOwner Team',command=['cat','src/Widget.java'])
        answer=investigate([member],'BuildOwner Team 的命令输出是什么？').to_dict()
        self.assertEqual(answer['scope']['member_id'],'BuildOwner Team')
        self.assertEqual([f['evidence_ids'] for f in answer['findings']],[['known']])
        other=self.command('other','MalloryDev was mentioned',command=['cat','src/MalloryDev.java'])
        answer=investigate([other],'同事 MalloryDev 的命令输出是什么？').to_dict()
        self.assertEqual(answer['scope']['kind'],'member_choice')
        self.assertEqual(answer['findings'],[])
        no_code=replace(other,metadata={**other.metadata,'command':['echo','MalloryDev']})
        self.assertEqual(investigate([no_code],'MalloryDev 的命令输出是什么？').to_dict()['scope']['kind'],'member_choice')
        filename=self.command('bob-file',member='Bob',command=['cat','src/Alice.java'])
        answer=investigate([self.command('alice',member='Alice'),filename],'Alice.java 的命令输出是什么？').to_dict()
        self.assertIsNone(answer['scope'])
        self.assertEqual([f['evidence_ids'] for f in answer['findings']],[['bob-file']])

    def test_numeric_member_ids_do_not_corrupt_other_member_scope(self):
        events=[self.command('alice',member='Alice'),self.command('numeric',member='0')]
        answer=investigate(events,'Alice 的原生命令输出是什么？').to_dict()
        self.assertEqual(answer['scope']['member_id'],'Alice')
        self.assertEqual([f['evidence_ids'] for f in answer['findings']],[['alice']])

    def test_native_word_inside_a_han_topic_is_not_removed_as_boilerplate(self):
        events=[self.command('bridge','原生桥已连接'),self.command('unrelated','其他模块输出')]
        answer=investigate(events,'项目 原生桥 命令输出是什么？').to_dict()
        self.assertEqual([f['evidence_ids'] for f in answer['findings']],[['bridge']])

    def test_git_original_is_queryable_without_a_candidate_fact(self):
        event=self.event('git-a','git_commit','Record indexed cache change.',source_ref='git:abc123')
        answer=investigate([event],'项目最近的 Git 提交原文是什么？').to_dict()
        self.assertEqual(answer['findings'][0]['evidence_ids'],['git-a'])
        self.assertEqual(answer['findings'][0]['source_kind'],'git_commit')
        self.assertEqual(answer['findings'][0]['status'],'raw')
        self.assertEqual(answer['findings'][0]['quote'],event.content)
        self.assertIsNone(answer['next_action_status'])

    def test_command_exit_and_native_origin_are_not_task_acceptance(self):
        events=[self.command('failed',exit_code=1),self.command('passed',exit_code=0,days=1)]
        answer=investigate(events,'项目命令输出和退出码是什么？').to_dict()
        self.assertEqual({f['exit_code'] for f in answer['findings']},{0,1})
        self.assertTrue(all(f['command_origin']=='native_trace' and f['status']=='raw' for f in answer['findings']))
        self.assertIsNone(answer['next_action_basis'])

    def test_agent_marker_wins_over_native_marker_and_original_opens_by_id(self):
        event=self.command('reported',exit_code=0,origin='agent_submission',actor_type='agent')
        finding=investigate([event],'Alice 的命令输出是什么？').to_dict()['findings'][0]
        self.assertEqual(finding['command_origin'],'agent_report')
        self.assertEqual(finding['claim_type'],'assistant_report')
        self.assertEqual(finding['evidence_ids'],['reported'])

    def test_failure_filter_uses_only_normalized_integer_exit(self):
        events=[self.command('bad',exit_code=1),self.command('ok',exit_code=0),
            self.command('boolean',exit_code=False),self.command('floating',exit_code=1.0)]
        answer=investigate(events,'项目失败的命令记录原文是什么？').to_dict()
        self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['bad'])

    def test_member_ownership_excludes_other_members_and_unattributed_project_git(self):
        events=[self.command('alice',member='Alice'),self.command('bob',member='Bob'),
            self.event('project-git','git_commit','Shared project commit.',member='Bob')]
        for question in ['Alice 的Git提交和命令输出是什么？','Alice命令输出是什么？']:
            with self.subTest(question=question):
                answer=investigate(events,question).to_dict()
                self.assertEqual(answer['scope']['member_id'],'Alice')
                self.assertEqual({f['evidence_ids'][0] for f in answer['findings']},{'alice'})
                self.assertNotIn('bob',answer['evidence_ids'])
                self.assertNotIn('project-git',answer['evidence_ids'])

    def test_unknown_member_never_falls_back_to_project_records(self):
        answer=investigate([self.command('alice')],'Unknown 的命令输出是什么？').to_dict()
        self.assertEqual(answer['scope']['kind'],'member_choice')
        self.assertEqual(answer['findings'],[])
        self.assertEqual(answer['evidence_ids'],[])

    def test_quoted_command_fragment_retrieves_only_matching_records(self):
        events=[self.event('unit','tool_result','Unit output',command='run.ps1 -Phase unit',exit_code=1),
            self.event('pages','tool_result','Pages output',command='run.ps1 -Phase pages',exit_code=0,days=1)]
        answer=investigate(events,'项目 `-Phase unit` 命令输出是什么？').to_dict()
        self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['unit'])

    def test_specific_unmatched_literal_returns_gap_instead_of_unrelated_output(self):
        answer=investigate([self.command('old')],'项目 `missing-check.py` 命令输出是什么？').to_dict()
        self.assertEqual(answer['findings'],[])
        self.assertTrue(any('匹配' in gap for gap in answer['evidence_gaps']))

    def test_unmatched_member_source_does_not_cite_unrelated_raw_fallback(self):
        answer=investigate([self.command('old')],'Alice 的 `missing-check.py` 命令输出是什么？').to_dict()
        self.assertEqual(answer['findings'],[])
        self.assertEqual(answer['evidence_ids'],[])

    def test_source_categories_share_bounded_budget_and_keep_exact_excerpt(self):
        events=[self.event('git-'+str(i),'git_commit','Commit '+str(i),days=i) for i in range(8)]
        events += [self.command('command-'+str(i),'Long output '+('z'*2000),command=['python','-m','unittest'],days=i) for i in range(8)]
        answer=investigate(events,'项目最近的 Git 提交和测试输出原文是什么？').to_dict()
        self.assertLessEqual(len(answer['findings']),6)
        self.assertEqual({f['source_kind'] for f in answer['findings']},{'git_commit','command_execution'})
        for finding in answer['findings']:
            self.assertLessEqual(len(finding['quote']),1600)
            source=next(e for e in events if e.id==finding['evidence_ids'][0])
            self.assertTrue(source.content.startswith(finding['quote']))
            if source.kind=='command_execution':self.assertTrue(finding['excerpt_truncated'])

    def test_raw_context_does_not_change_owner_next_action_or_any_events(self):
        source=self.event('task','assistant_message','Install the reviewed candidate.',role='assistant')
        proposals=validate_candidates([source],{'candidates':[{'source_id':'task','kind':'task',
            'subject_key':'install','quote':source.content,'task_state':'pending'}]})
        events=[source,*proposals,self.command('checks',exit_code=0),self.event('git','git_commit','Update source.')]
        fact=derive_facts(events)[0]
        reviews=[{'action':'accept_fact','fact_id':fact.id,'subject_key':fact.subject_key,'reviewer':'owner'}]
        before=copy.deepcopy([e.to_dict() for e in events])
        answer=investigate(events,'项目当前状态对应的Git提交和测试输出原文是什么？',reviews).to_dict()
        self.assertEqual(answer['next_action_basis']['fact_id'],fact.id)
        self.assertEqual(answer['next_action_status'],'accepted')
        self.assertTrue(any(f['source_kind']=='git_commit' for f in answer['findings']))
        self.assertTrue(all(f['status']=='raw' for f in answer['findings'] if f.get('source_kind')=='git_commit'))
        self.assertTrue(any(f.get('fact_id')==fact.id and f['status']=='accepted' for f in answer['findings']))
        self.assertEqual([e.to_dict() for e in events],before)

    def test_test_output_request_excludes_unrelated_newer_document_command(self):
        events=[self.command('tests','Ran 3 checks.',command=['python','-m','unittest']),
            self.command('docs','Plain document content.',command=['cat','README.md'],days=1)]
        answer=investigate(events,'项目测试输出原文是什么？').to_dict()
        self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['tests'])

    def test_explicit_exit_filter_does_not_become_an_unmatched_topic(self):
        events=[self.command('code127',exit_code=127),self.command('other',exit_code=0)]
        answer=investigate(events,'项目退出码为127的命令记录是什么？').to_dict()
        self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['code127'])

    def test_quoted_failure_filename_is_not_a_failure_predicate(self):
        event=self.command('named',command=['python','failed-check.py'],exit_code=0)
        answer=investigate([event],'项目 `failed-check.py` 命令输出是什么？').to_dict()
        self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['named'])

    def test_private_phase_and_proposals_are_never_selected_as_raw_material(self):
        events=[self.command('private','private reasoning',phase='analysis'),
            self.event('proposal','fact_proposal','not an original source')]
        answer=investigate(events,'项目命令原文是什么？').to_dict()
        self.assertEqual(answer['findings'],[])

    def test_searches_full_recorded_command_while_rendering_bounded_preview(self):
        command='start '+('x '*1000)+'needle-middle '+('y '*1000)+' end'
        event=self.command('long',command=command,exit_code=0)
        answer=investigate([event],'项目 `needle-middle` 命令输出是什么？').to_dict()
        self.assertEqual(answer['findings'][0]['evidence_ids'],['long'])
        self.assertLessEqual(len(answer['findings'][0]['command_preview']),1600)

    def test_recognized_selectors_do_not_require_project_prefix_or_become_members(self):
        event=self.command('selected',command=['python','checks.py'],exit_code=1)
        for question in ['`checks.py` 命令输出是什么？','退出码为1的命令记录是什么？']:
            with self.subTest(question=question):
                answer=investigate([event],question).to_dict()
                self.assertIsNone(answer['scope'])
                self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['selected'])

    def test_success_filter_requires_normalized_integer_zero_exit(self):
        events=[self.command('ok',exit_code=0),self.command('bad',exit_code=1),
            self.command('boolean',exit_code=False),self.command('floating',exit_code=0.0),self.command('unknown')]
        answer=investigate(events,'项目成功的命令输出是什么？').to_dict()
        self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['ok'])

    def test_exact_windows_path_searches_original_command_arguments(self):
        event=self.command('windows',command=['python',r'C:\tools\checks.py'])
        answer=investigate([event],r'项目 `C:\tools\checks.py` 命令输出是什么？').to_dict()
        self.assertEqual([f['evidence_ids'][0] for f in answer['findings']],['windows'])

    def test_long_or_unclosed_quoted_selector_never_becomes_partial_overlap(self):
        event=self.command('partial','needle present')
        for size in [160,520]:
            with self.subTest(size=size):
                # Put the raw-source trigger before the long selector too, so
                # truncation does not simply hide the requested query type.
                question='项目命令输出 `needle '+('z'*size)+'` 是什么？'
                answer=investigate([event],question).to_dict()
                self.assertEqual(answer['findings'],[])
                self.assertTrue(answer['evidence_gaps'])

    def test_no_raw_request_preserves_existing_answer(self):
        events=sample_events()
        answer=investigate(events,'为什么项目会处于当前状态，下一步该做什么？').to_dict()
        self.assertEqual(answer['findings'],[])
        self.assertEqual(answer['evidence_gaps'],[])

    def test_provider_retrieves_raw_ids_alongside_authoritative_memory_context(self):
        events=[self.command('native',exit_code=1),self.event('git','git_commit','Update source.')]
        wanted=[]
        class Memory:
            def query(self,events,reviews,relations):
                return SimpleNamespace(facts=[],support_by_fact={},status={'enabled':True,'engine':'synthetic-memory'})
        class Provider:
            def retrieve(self,snapshot,ids,base_url):
                wanted.extend(ids)
                self_ids=set(ids)
                assert self_ids==set(snapshot.payloads)
                return EvidenceRetrieval([e for e in events if e.id in self_ids],
                    {'enabled':True,'queried':True,'source_count':len(ids)})
        server=create_dashboard_server(events,port=0,memory=Memory(),zetaris=Provider())
        self.addCleanup(server.server_close)
        answer=server.investigate('项目Git提交和命令输出原文是什么？')
        self.assertEqual(set(wanted),{'native','git'})
        self.assertEqual(answer['zetaris']['source_count'],2)
        self.assertEqual({f['evidence_ids'][0] for f in answer['findings']},{'native','git'})

    @unittest.skipUnless(shutil.which('node'),'Node required for actual app rendering verification')
    def test_browser_raw_source_identity_origin_exit_and_escaping(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run(['node',str(root/'tests/web_raw_source_test.cjs'),
            str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
