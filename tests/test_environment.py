from __future__ import annotations

import unittest

import project_compass


class EnvironmentSmokeTest(unittest.TestCase):
    def test_package_imports(self) -> None:
        self.assertEqual(project_compass.__version__, "0.1.0")


if __name__ == "__main__":
    unittest.main()

