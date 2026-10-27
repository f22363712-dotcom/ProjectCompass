from datetime import timezone
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_compass.project_scanner import scan_project
from project_compass.projections import build_views


class ProjectScanTest(unittest.TestCase):
    def test_scans_selected_markdown_and_jsonl_and_builds_two_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Demo\nProject goal", encoding="utf-8")
            (root / "notes.txt").write_text("ignore me", encoding="utf-8")
            (root / "session.jsonl").write_text(json.dumps({"id": "j1", "type": "assistant_message", "session_id": "s1", "author": "alice", "content": "decision", "timestamp": "2026-10-24T08:00:00+00:00", "metadata": {"facts": [{"kind": "decision", "subject_key": "auth", "value": "session"}]}}), encoding="utf-8")
            events = scan_project(root, codex_paths=[root / "session.jsonl"])
            views = build_views(events)
            self.assertGreaterEqual(len(events), 2)
            self.assertIn("alice", views["personal"])
            self.assertEqual(views["team"]["facts"][0]["subject_key"], "auth")
            self.assertTrue(all(event.observed_at.tzinfo is not None for event in events))

    def test_scanning_same_project_is_deterministic_for_selected_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("stable", encoding="utf-8")
            first = scan_project(root)
            second = scan_project(root)
            self.assertEqual([event.id for event in first], [event.id for event in second])

    @unittest.skipUnless(shutil.which('git'), 'Git is required for history isolation regression')
    def test_git_history_is_scoped_before_limit_and_untracked_child_does_not_inherit_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def git(*args):
                return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True,timeout=10)
            git('init','-q')
            first=root/'project-a';other=root/'project-b';empty=root/'untracked-project'
            for path in (first,other,empty):path.mkdir()
            for path,text in [(first,'selected first'),(other,'sibling first'),(first,'selected latest'),(other,'sibling latest')]:
                (path/'README.md').write_text(text,encoding='utf8')
                git('add',str(path/'README.md'))
                git('-c','user.name=Synthetic Test','-c','user.email=test@example.invalid','commit','-q','-m',text)
            (empty/'README.md').write_text('untracked',encoding='utf8')
            def subjects(path,limit=15):
                return [event.content for event in scan_project(path,max_commits=limit) if event.kind=='git_commit']
            self.assertEqual(subjects(first,1),['selected latest'])
            self.assertEqual(subjects(first),['selected latest','selected first'])
            self.assertEqual(subjects(empty),[])
            self.assertEqual(len(subjects(root)),4)

    @unittest.skipUnless(shutil.which('git'), 'Git is required for history preservation regression')
    def test_repository_root_preserves_merge_and_empty_checkpoint_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def git(*args):
                return subprocess.run(['git','-C',str(root),'-c','user.name=Synthetic Test','-c','user.email=test@example.invalid',*args],capture_output=True,text=True,check=True,timeout=10)
            git('init','-q')
            (root/'README.md').write_text('initial',encoding='utf8');git('add','README.md');git('commit','-q','-m','initial')
            branch=git('symbolic-ref','--short','HEAD').stdout.strip()
            git('checkout','-q','-b','feature')
            (root/'README.md').write_text('feature',encoding='utf8');git('add','README.md');git('commit','-q','-m','feature content')
            git('checkout','-q',branch);git('merge','--no-ff','feature','-m','merge reviewed feature')
            git('commit','--allow-empty','-q','-m','release checkpoint')
            subjects=lambda limit:[e.content for e in scan_project(root,max_commits=limit) if e.kind=='git_commit']
            self.assertEqual(subjects(1),['release checkpoint'])
            self.assertEqual(subjects(15),['release checkpoint','merge reviewed feature','feature content','initial'])


if __name__ == "__main__":
    unittest.main()
