import tempfile
import unittest
from pathlib import Path

from harness_agent.task_manager import TaskManager


class TaskManagerTest(unittest.TestCase):
    def test_completion_unlocks_dependent_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = TaskManager(Path(tmp))
            first = manager.create("Setup")
            second = manager.create("Code", blocked_by=[first.id])

            self.assertEqual(manager.load(second.id).status, "blocked")

            manager.update(first.id, "completed")

            unlocked = manager.load(second.id)
            self.assertEqual(unlocked.status, "pending")
            self.assertEqual(unlocked.blockedBy, [])


if __name__ == "__main__":
    unittest.main()
