from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from project_compass.cli import main
from project_compass.fact_engine import derive_facts
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events


class DirectionScanProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        project=Path(self.tmp.name)/'project';project.mkdir()
        self.ws=ProjectWorkspace(Path(self.tmp.name)/'memory',project)

    def source(self,identity,text,seconds=0):
        return replace(sample_events()[0],id=identity,kind='user_message',content=text,
            observed_at=sample_events()[0].observed_at+timedelta(seconds=seconds),
            metadata={'member_id':'Alice','role':'user','source_ref':'public:'+identity})

    def start(self,expected=0):
        with patch('project_compass.cli.run_dashboard') as run:
            self.assertEqual(main(['--open',str(self.ws.project_path),'--workspace-root',str(self.ws.workspace_root),'--web']),expected)
        return run.call_args

    def test_older_than_two_hundred_sources_is_eventually_scanned(self):
        raw=[self.source('old','云端功能继续暂缓。')]
        raw += [self.source(str(i),'今天记录项目进展。',i+1) for i in range(205)]
        self.ws.save(raw)
        first=self.start();second=self.start()
        self.assertEqual([f.quote for f in derive_facts(self.ws.load())],['云端功能继续暂缓。'])
        status=second.kwargs['intake_status'].get('direction_scan',{})
        self.assertEqual(status.get('pending_sources'),0)
        self.assertEqual(status.get('completed_sources'),206)
        self.assertEqual(first.kwargs['intake_status']['direction_scan']['batch_sources'],200)

    def test_long_tail_and_thirteen_declarations_resume_without_duplicates(self):
        text='普通进展。'*1300+'\n'+''.join(f'功能{i}继续暂缓。\n' for i in range(13))
        source=self.source('long',text);self.ws.save([source])
        for _ in range(5):last=self.start()
        facts=derive_facts(self.ws.load())
        self.assertEqual(len(facts),13)
        self.assertEqual(len({f.id for f in facts}),13)
        self.assertTrue(all(f.quote in text for f in facts))
        self.assertEqual(last.kwargs['intake_status']['direction_scan']['pending_sources'],0)
        self.assertEqual(self.ws.load()[0].to_dict(),source.to_dict())
        self.assertEqual(self.ws.load_review_state()[2],[])

    def test_window_fragments_do_not_drop_negation_or_lose_complete_statement(self):
        # A long sentence with negation must not become a declaration at a slice boundary.
        text='不要'+('普通文字'*1500)+'决定放弃旧插件方案。\n'+'普通进展。'*850+'\n云端同步继续暂缓。'
        self.ws.save([self.source('boundary',text)])
        for _ in range(5):self.start()
        self.assertEqual([f.quote for f in derive_facts(self.ws.load())],['云端同步继续暂缓。'])

    def test_rule_cursor_is_separate_from_model_coverage_and_changed_content(self):
        self.ws.save([self.source('first','记录进展。')])
        model=self.ws.state_path.parent/'extraction-cache'/'coverage-v1.json'
        model.parent.mkdir(parents=True);model.write_text('model-ledger-sentinel',encoding='utf8')
        self.start()
        self.ws.save([self.source('second','插件功能继续暂缓。')])
        self.start()
        self.assertEqual(len(derive_facts(self.ws.load())),1)
        self.assertEqual(model.read_text(encoding='utf8'),'model-ledger-sentinel')
        self.assertTrue((self.ws.state_path.parent/'direction-rules'/'coverage-v1.json').exists())

    def test_failed_proposal_save_does_not_acknowledge_source(self):
        self.ws.save([self.source('failure','云端同步继续暂缓。')]);before=self.ws.state_path.read_bytes()
        with patch.object(ProjectWorkspace,'save',side_effect=OSError('synthetic save failure')):
            self.start(expected=2)
        self.assertEqual(self.ws.state_path.read_bytes(),before)
        progress=self.ws.state_path.parent/'direction-rules'/'coverage-v1.json'
        self.assertFalse(progress.exists())
        last=self.start()
        self.assertEqual(len(derive_facts(self.ws.load())),1)
        self.assertEqual(last.kwargs['intake_status'].get('direction_scan',{}).get('pending_sources'),0)

    def test_failed_progress_write_retries_without_duplicate_proposals(self):
        self.ws.save([self.source('retry','云端同步继续暂缓。')])
        from project_compass.extraction_progress import ExtractionProgress
        with patch.object(ExtractionProgress,'acknowledge',side_effect=OSError('synthetic cursor failure')):
            self.start(expected=2)
        before=[e.to_dict() for e in self.ws.load()]
        self.assertEqual(len(derive_facts(self.ws.load())),1)
        status=self.start().kwargs['intake_status']
        self.assertEqual(status['new_direction_proposals'],0)
        self.assertEqual(status['direction_scan']['pending_sources'],0)
        self.assertEqual([e.to_dict() for e in self.ws.load()],before)

    def test_content_key_changes_and_excluded_records_have_no_rule_coverage(self):
        from project_compass.directions import scan_explicit_directions
        source=self.source('same','记录进展。')
        _,cursors,_=scan_explicit_directions([source])
        changed=replace(source,content='新插件继续暂缓。')
        hidden=replace(source,id='hidden',content='旧插件继续暂缓。',metadata={'phase':'analysis'})
        proposals,_,status=scan_explicit_directions([changed,hidden],cursors=cursors)
        self.assertEqual(len(proposals),1)
        self.assertEqual(status['eligible_sources'],1)
        self.assertFalse(status['semantic_coverage'])

    def test_partial_complete_statement_is_preserved_at_each_window_boundary(self):
        from project_compass.directions import scan_explicit_directions
        for offset in [4350,4399,4400,4450,5980,6000,6010]:
            with self.subTest(offset=offset):
                text='x'*offset+'\n功能继续暂缓。\n'
                source=self.source('offset-'+str(offset),text);events=[source];cursors={}
                for _ in range(4):
                    proposals,updates,_=scan_explicit_directions(events,cursors=cursors)
                    events.extend(proposals);cursors.update(updates)
                self.assertEqual([f.quote for f in derive_facts(events)],['功能继续暂缓。'])

    def test_recovered_deferral_query_is_not_also_labelled_unsupported(self):
        from project_compass.directions import scan_explicit_directions
        from project_compass.investigator import investigate
        source=self.source('ai','AI 代查等功能继续暂缓。')
        proposals,_,_=scan_explicit_directions([source])
        answer=investigate([source,*proposals],'AI 代查有哪些暂缓记录？').to_dict()
        self.assertEqual([row['quote'] for row in answer['findings']],[source.content])
        self.assertFalse(any('此问题尚未得到回答' in gap or '没有找到与该问题类型' in gap for gap in answer['evidence_gaps']))
        self.assertTrue(any('负责人确认' in gap for gap in answer['evidence_gaps']))

    def test_non_native_refresh_advances_without_a_model_or_answer_revision_change(self):
        self.ws.save([self.source(str(i),'记录进展。',i) for i in range(205)])
        args=self.start();events=args.args[0]
        callback=args.kwargs['refresh_callback']
        self.assertIsNotNone(callback,'A project without native intake needs a rule-scan continuation')
        from project_compass.web_server import create_dashboard_server
        server=create_dashboard_server(events,port=0,intake_status=args.kwargs['intake_status'],refresh_callback=callback)
        self.addCleanup(server.server_close)
        revision=server.state_revision();next_status=callback()
        self.assertEqual(next_status['direction_scan']['pending_sources'],0)
        self.assertFalse(next_status['updated'])
        self.assertEqual(server.state_revision(),revision)


if __name__=='__main__':unittest.main()
