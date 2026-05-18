import tempfile
import unittest
from pathlib import Path

from harness_agent.cli import read_goal


class CliGoalFileTest(unittest.TestCase):
    def test_read_goal_from_default_relative_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "goal.md").write_text("Build a TODO API.\n", encoding="utf-8")

            self.assertEqual(read_goal(root, "goal.md"), "Build a TODO API.")

    def test_read_goal_rejects_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "goal.md").write_text("  \n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Goal file is empty"):
                read_goal(root, "goal.md")


if __name__ == "__main__":
    unittest.main()
