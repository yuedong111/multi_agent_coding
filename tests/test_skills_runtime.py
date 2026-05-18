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


if __name__ == "__main__":
    unittest.main()
