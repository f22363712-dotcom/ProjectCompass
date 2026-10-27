from pathlib import Path
import tempfile
import os
import unittest

from project_compass.cli import main
from project_compass.env_loader import load_env_file


class EnvLoaderTest(unittest.TestCase):
    def test_loads_simple_values_without_overwriting_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# local credentials\n"
                "TYPESAFE_API_KEY=from-file\n"
                "QUOTED=\"hello world\"\n"
                "export PLAIN=ok\n",
                encoding="utf-8",
            )
            environment = {"TYPESAFE_API_KEY": "from-process"}

            loaded = load_env_file(path, environment)

        self.assertEqual(loaded, {"TYPESAFE_API_KEY": "from-file", "QUOTED": "hello world", "PLAIN": "ok"})
        self.assertEqual(environment["TYPESAFE_API_KEY"], "from-process")
        self.assertEqual(environment["QUOTED"], "hello world")
        self.assertEqual(environment["PLAIN"], "ok")

    def test_missing_file_is_a_noop(self):
        environment = {"KEEP": "yes"}

        loaded = load_env_file(Path("does-not-exist.env"), environment)

        self.assertEqual(loaded, {})
        self.assertEqual(environment, {"KEEP": "yes"})

    def test_cli_loads_explicit_env_file_before_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("PROJECT_COMPASS_TEST_KEY=loaded\n", encoding="utf-8")
            os.environ.pop("PROJECT_COMPASS_TEST_KEY", None)

            result = main(["--sample", "--env-file", str(path)])

            self.assertEqual(result, 0)
            self.assertEqual(os.environ.get("PROJECT_COMPASS_TEST_KEY"), "loaded")
            os.environ.pop("PROJECT_COMPASS_TEST_KEY", None)


if __name__ == "__main__":
    unittest.main()
