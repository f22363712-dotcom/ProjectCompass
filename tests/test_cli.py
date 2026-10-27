import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
        return subprocess.run([sys.executable, "-m", "project_compass.cli", *args], cwd=ROOT, env=env, capture_output=True, text=True)

    def test_sample_outputs_structured_answer(self):
        result = self.run_cli("--sample")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("conclusion", payload)
        self.assertIn("conflicts", payload)

    def test_malformed_input_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{bad", encoding="utf-8")
            result = self.run_cli("--input", str(path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid evidence JSON", result.stderr)


if __name__ == "__main__":
    unittest.main()
