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


if __name__ == "__main__":
    unittest.main()
