from pathlib import Path
import shutil
import subprocess
import unittest


class VisualTraceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for offline UI verification')
    def test_scoped_trace_provenance_reviews_and_scroll(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', str(root/'tests/web_visual_trace_test.cjs'),
            str(root/'src/project_compass/web/app.js')], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
