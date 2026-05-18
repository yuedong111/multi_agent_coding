import tempfile
import unittest
from pathlib import Path

from harness_agent.config import AgentConfig, HarnessConfig
from harness_agent.workflow import Workflow


def config() -> HarnessConfig:
    agents = {
        name: AgentConfig(
            name=name,
            role=f"{name} role",
            model="test-model",
            base_url="https://example.test/v1",
            api_key_env="TEST_API_KEY",
            temperature=0.0,
            max_steps=1,
            skills=[],
        )
        for name in ["lead", "architect", "coder", "tester", "reviewer", "release"]
    }
    return HarnessConfig(agents=agents)


class WorkflowRequirementsGateTest(unittest.TestCase):
    def test_existing_requirements_skip_lead_planning_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            (root / "docs" / "requirements.md").write_text("Confirmed rule.\n", encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")

            objective = workflow._objective("Build the app", mode="build", use_existing_requirements=True)
            state = workflow._load_or_begin_state(
                "build",
                objective,
                ["architect", "coder", "tester", "reviewer", "coder", "tester", "release"],
            )

            self.assertEqual(state["order"][0], "architect")
            self.assertNotIn("lead", state["order"])
            self.assertIn("lead planning stage was skipped", objective)

    def test_empty_requirements_keeps_lead_planning_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            (root / "docs" / "requirements.md").write_text("", encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")

            self.assertFalse(workflow._requirements_has_content())
            objective = workflow._objective("Build the app", mode="build")

            self.assertIn("The lead agent owns the initial plan", objective)

    def test_agent_prompt_is_generated_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            (root / "docs" / "requirements.md").write_text("Confirmed rule.\n", encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")
            workflow.tasks.create("Implement feature", "Use the confirmed rule.", owner="coder")

            prompt = workflow._ensure_agent_prompt("coder", "coder role", "Build the app", "build")

            prompt_path = root / ".harness" / "agent-prompts" / "coder.md"
            self.assertTrue(prompt_path.exists())
            self.assertIn("Confirmed rule.", prompt)
            self.assertIn("Implement feature", prompt)
            self.assertIn("coder role", prompt)

    def test_non_empty_agent_prompt_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            skills.mkdir()
            workflow = Workflow(root, config(), skills, "")
            prompt_path = root / ".harness" / "agent-prompts" / "coder.md"
            prompt_path.parent.mkdir(parents=True)
            prompt_path.write_text("Manual prompt.\n", encoding="utf-8")

            prompt = workflow._ensure_agent_prompt("coder", "new role", "Build the app", "build")

            self.assertEqual(prompt, "Manual prompt.\n")
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), "Manual prompt.\n")


if __name__ == "__main__":
    unittest.main()
