from pathlib import Path
import shutil,subprocess,unittest


class ReasonPreviewWebTests(unittest.TestCase):
    def test_reason_cards_preserve_read_only_scope_and_escape_all_model_text(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node unavailable')
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([node,str(root/'tests/web_reason_preview_test.cjs'),str(root/'src/project_compass/web/app.js')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
