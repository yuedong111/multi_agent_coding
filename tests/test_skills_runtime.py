import tempfile
import unittest
from pathlib import Path

from harness_agent.agents import Agent
from harness_agent.config import AgentConfig
from harness_agent.message_bus import MessageBus
from harness_agent.skills import SkillLoader
from harness_agent.task_manager import TaskManager
from harness_agent.tools import ToolRuntime


class SkillRuntimeTest(unittest.TestCase):
    def test_runtime_can_load_discovered_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            skills_dir = base / "skills"
            skill_dir = skills_dir / "dynamic"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: dynamic
description: A skill loaded on demand.
---

# Dynamic Skill

Use this only after calling load_skill.
""",
                encoding="utf-8",
            )
            root = base / "project"
            root.mkdir()
            tasks = TaskManager(root)
            bus = MessageBus(root)
            skills = SkillLoader(skills_dir)
            runtime = ToolRuntime(root, tasks, bus, skills)

            result = runtime.dispatch("coder", {"tool": "load_skill", "args": {"name": "dynamic"}})

            self.assertTrue(result["ok"])
            self.assertEqual(result["result"]["name"], "dynamic")
            self.assertIn('<skill name="dynamic">', result["result"]["content"])

    def test_agent_prompt_includes_global_instructions_and_skill_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            skills_dir = base / "skills"
            skill_dir = skills_dir / "review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: review
description: Review generated code.
---

# Review
""",
                encoding="utf-8",
            )
            root = base / "project"
            root.mkdir()
            config = AgentConfig(
                name="reviewer",
                role="Review code.",
                model="test-model",
                base_url="https://example.test/v1",
                api_key_env="TEST_API_KEY",
                temperature=0.0,
                max_steps=1,
                skills=[],
            )
            tasks = TaskManager(root)
            bus = MessageBus(root)
            skills = SkillLoader(skills_dir)
            runtime = ToolRuntime(root, tasks, bus, skills)
            agent = Agent(config, root, tasks, bus, skills, runtime, "Global boundary rules.")

            prompt = agent._system_prompt()

            self.assertIn("Global boundary rules.", prompt)
            self.assertIn("- review: Review generated code.", prompt)
            self.assertIn("load_skill", prompt)

    def test_agent_extracts_json_action_from_explanatory_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills_dir = base / "skills"
            root.mkdir()
            skills_dir.mkdir()
            config = AgentConfig(
                name="coder",
                role="Write code.",
                model="test-model",
                base_url="https://example.test/v1",
                api_key_env="TEST_API_KEY",
                temperature=0.0,
                max_steps=1,
                skills=[],
            )
            agent = Agent(
                config,
                root,
                TaskManager(root),
                MessageBus(root),
                SkillLoader(skills_dir),
                ToolRuntime(root, TaskManager(root), MessageBus(root), SkillLoader(skills_dir)),
            )

            action = agent._parse_action(
                'I will write the file now.\n```json\n{"tool":"finish","args":{"summary":"done"}}\n```'
            )

            self.assertEqual(action["tool"], "finish")
            self.assertEqual(action["args"]["summary"], "done")

    def test_agent_retries_when_model_returns_invalid_json_action(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            def complete(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return '{"tool":"finish","args":'
                self.retry_prompt = messages[-1]["content"]
                return '{"tool":"finish","args":{"summary":"done","status":"completed"}}'

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills_dir = base / "skills"
            root.mkdir()
            skills_dir.mkdir()
            config = AgentConfig(
                name="coder",
                role="Write code.",
                model="test-model",
                base_url="https://example.test/v1",
                api_key_env="TEST_API_KEY",
                temperature=0.0,
                max_steps=1,
                skills=[],
            )
            agent = Agent(
                config,
                root,
                TaskManager(root),
                MessageBus(root),
                SkillLoader(skills_dir),
                ToolRuntime(root, TaskManager(root), MessageBus(root), SkillLoader(skills_dir)),
            )
            fake_client = FakeClient()
            agent.client = fake_client

            result = agent.run("Do the task.")

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["summary"], "done")
            self.assertEqual(fake_client.calls, 2)
            self.assertIn("could not be parsed", fake_client.retry_prompt)


if __name__ == "__main__":
    unittest.main()
