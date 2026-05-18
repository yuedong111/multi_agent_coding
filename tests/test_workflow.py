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

    def test_plan_stage_generates_requirements_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            skills.mkdir()
            workflow = Workflow(root, config(), skills, "")

            result = workflow.plan("Build a TODO API.")

            requirements = root / "docs" / "requirements.md"
            self.assertTrue(requirements.exists())
            self.assertEqual(result["plan"]["status"], "completed")
            self.assertIn("Build a TODO API.", requirements.read_text(encoding="utf-8"))

    def test_plan_stage_preserves_non_empty_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            requirements = root / "docs" / "requirements.md"
            requirements.write_text("Reviewed requirements.\n", encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")

            result = workflow.plan("Build a TODO API.")

            self.assertEqual(result["plan"]["status"], "skipped")
            self.assertEqual(requirements.read_text(encoding="utf-8"), "Reviewed requirements.\n")

    def test_prompts_stage_generates_execution_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            (root / "docs" / "requirements.md").write_text("Reviewed requirements.\n", encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")

            result = workflow.generate_prompts("Build a TODO API.")

            self.assertIn("architect", result)
            self.assertIn("coder", result)
            self.assertIn("coder_1", result)
            self.assertNotIn("lead", result)
            self.assertTrue((root / ".harness" / "agent-prompts" / "coder.md").exists())
            self.assertTrue((root / ".harness" / "agent-prompts" / "coder_1.md").exists())
            self.assertEqual(workflow._missing_agent_prompts(["architect", "coder"]), [])

    def test_prompts_stage_splits_large_requirements_into_coder_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            requirements = "\n\n".join(
                [
                    "# Business Requirements",
                    "## Accounts\n" + ("Create account business rule. " * 120),
                    "## Billing\n" + ("Invoice business rule. " * 120),
                    "## Notifications\n" + ("Notify business rule. " * 120),
                ]
            )
            (root / "docs" / "requirements.md").write_text(requirements, encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")

            result = workflow.generate_prompts("Build the platform.")
            order = workflow._build_execution_order()

            self.assertIn("coder_2", result)
            self.assertTrue((root / ".harness" / "agent-prompts" / "coder_2.md").exists())
            self.assertEqual(order.count("coder"), workflow._reviewed_coder_prompt_count())
            coder_prompt = (root / ".harness" / "agent-prompts" / "coder_1.md").read_text(encoding="utf-8")
            self.assertIn("Assigned Business Slice", coder_prompt)
            self.assertIn("coder.md` is an audit overview only", coder_prompt)
            self.assertIn("Create account business rule.", coder_prompt)
            self.assertNotIn("Invoice business rule.", coder_prompt)
            self.assertIn("intentionally embeds only the assigned business slice", coder_prompt)

    def test_execute_requires_reviewed_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            (root / "docs" / "requirements.md").write_text("Reviewed requirements.\n", encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")

            with self.assertRaisesRegex(RuntimeError, "Run the prompts stage first"):
                workflow.execute("Build a TODO API.")


if __name__ == "__main__":
    unittest.main()
