from pathlib import Path
import shutil
import subprocess
import unittest


class WebIntakeRaceTest(unittest.TestCase):
    def test_initial_state_recovery_is_bounded_read_only_and_does_not_hide_bad_state(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node required')
        root=Path(__file__).parents[1]
        result=subprocess.run([node,str(root/'tests/web_startup_recovery_test.cjs'),str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,encoding='utf8',timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_agent_api_only_board_polls_and_keeps_configured_owner(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node required')
        root=Path(__file__).parents[1]
        result=subprocess.run([node,str(root/'tests/web_agent_api_test.cjs'),str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,encoding='utf8',timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_tool_source_drawer_links_to_its_counterpart_without_fact_promotion(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node required')
        root=Path(__file__).parents[1]
        result=subprocess.run([node,str(root/'tests/web_tool_pair_test.cjs'),str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,encoding='utf8',timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_connected_member_and_scoped_spine_preserve_shared_evidence_links(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node required')
        root=Path(__file__).parents[1]
        result=subprocess.run([node,str(root/'tests/web_personal_scope_test.cjs'),str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,encoding='utf8',timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_candidate_only_completion_updates_a_document_only_board(self):
        node = shutil.which('node')
        if not node: self.skipTest('Node required')
        root = Path(__file__).parents[1]
        result = subprocess.run([node,str(root/'tests/web_live_extraction_test.cjs'),str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,encoding='utf8',timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_old_capture_response_cannot_roll_back_completed_rescan(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node required for async UI race')
        root = Path(__file__).parents[1]
        result = subprocess.run([node, str(root / 'tests/web_rescan_race_test.cjs'), str(root / 'src/project_compass/web/app.js')], capture_output=True, text=True, encoding='utf8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_delayed_answer_cannot_replace_new_evidence_warning(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the async UI race regression")
        root = Path(__file__).parents[1]
        result = subprocess.run([node, str(root / "tests/web_intake_race_test.cjs"), str(root / "src/project_compass/web/app.js")], capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
