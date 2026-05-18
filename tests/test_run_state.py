import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.message_bus import MessageBus
from harness_agent.run_state import RunState
from harness_agent.skills import SkillLoader
from harness_agent.task_manager import TaskManager
from harness_agent.tools import ToolRuntime


class RunStateTest(unittest.TestCase):
    def test_checkpoint_restore_removes_incomplete_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("stable\n", encoding="utf-8")
            state = RunState(root)
            checkpoint = state.make_checkpoint("run", "coder", 0)

            (root / "app.py").write_text("partial\n", encoding="utf-8")
            (root / "new.py").write_text("unfinished\n", encoding="utf-8")

            state.restore_checkpoint(checkpoint.id)

            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "stable\n")
            self.assertFalse((root / "new.py").exists())

    def test_isolation_merges_only_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("stable\n", encoding="utf-8")
            state = RunState(root)

            isolated = state.prepare_isolation("run", "coder", 0)
            (isolated / "app.py").write_text("changed\n", encoding="utf-8")
            (isolated / "created.py").write_text("created\n", encoding="utf-8")

            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "stable\n")

            changed = state.merge_isolation(isolated)

            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "changed\n")
            self.assertEqual((root / "created.py").read_text(encoding="utf-8"), "created\n")
            self.assertEqual(changed, ["app.py", "created.py"])


class ToolRuntimeJournalTest(unittest.TestCase):
    def test_file_writes_are_journaled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_dir = root / "skills"
            skills_dir.mkdir()
            journal = root / ".harness" / "patch-journal" / "run" / "00-coder.jsonl"
            runtime = ToolRuntime(
                root,
                TaskManager(root),
                MessageBus(root),
                SkillLoader(skills_dir),
                journal_path=journal,
            )

            runtime.write_file("app.py", "one\n")
            runtime.append_file("app.py", "two\n")

            entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(entries[0]["operation"], "write_file")
            self.assertIsNone(entries[0]["before"])
            self.assertEqual(entries[0]["after"], "one\n")
            self.assertEqual(entries[1]["operation"], "append_file")
            self.assertEqual(entries[1]["before"], "one\n")
            self.assertEqual(entries[1]["after"], "one\ntwo\n")


if __name__ == "__main__":
    unittest.main()
