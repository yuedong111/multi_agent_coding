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

    def test_write_file_strips_single_code_fence_for_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_dir = root / "skills"
            skills_dir.mkdir()
            runtime = ToolRuntime(
                root,
                TaskManager(root),
                MessageBus(root),
                SkillLoader(skills_dir),
            )

            runtime.write_file(
                "app.py",
                "Here is the implementation:\n```python\nprint('hello')\n```\nDone.",
            )
            runtime.write_file(
                "README.md",
                "Example:\n```python\nprint('hello')\n```\n",
            )

            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "print('hello')")
            self.assertIn("```python", (root / "README.md").read_text(encoding="utf-8"))

    def test_ask_user_records_answer_in_requirements_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_dir = root / "skills"
            skills_dir.mkdir()
            seen = {}

            def answer_question(payload):
                seen.update(payload)
                return "Use soft delete for user-visible records."

            runtime = ToolRuntime(
                root,
                TaskManager(root),
                MessageBus(root),
                SkillLoader(skills_dir),
                user_question_handler=answer_question,
            )

            result = runtime.dispatch(
                "lead",
                {
                    "tool": "ask_user",
                    "args": {
                        "question": "Should deletes be hard or soft?",
                        "impact": "Affects storage and API delete behavior.",
                    },
                },
            )

            self.assertTrue(result["ok"])
            self.assertEqual(seen["agent"], "lead")
            self.assertEqual(result["result"]["requirementsPath"], "docs/requirements.md")
            requirements = (root / "docs" / "requirements.md").read_text(encoding="utf-8")
            self.assertIn("Should deletes be hard or soft?", requirements)
            self.assertIn("Use soft delete for user-visible records.", requirements)


if __name__ == "__main__":
    unittest.main()
