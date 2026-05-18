import tempfile
import unittest
from pathlib import Path

from harness_agent.config import AgentConfig, HarnessConfig
from harness_agent.static_scan import StaticScanner
from harness_agent.workflow import FILE_PLAN_PATH, Workflow


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


def config_with_integrator() -> HarnessConfig:
    cfg = config()
    cfg.agents["integrator"] = AgentConfig(
        name="integrator",
        role="integrator role",
        model="test-model",
        base_url="https://example.test/v1",
        api_key_env="TEST_API_KEY",
        temperature=0.0,
        max_steps=1,
        skills=[],
    )
    return cfg


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
            self.assertIn("跳过 lead 规划阶段", objective)

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

            self.assertIn("lead agent 负责初始计划", objective)

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
            self.assertTrue((root / FILE_PLAN_PATH).exists())
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
            self.assertIn("分配的业务切片", coder_prompt)
            self.assertIn("`coder.md` 仅用于总览审核", coder_prompt)
            self.assertIn("Create account business rule.", coder_prompt)
            self.assertNotIn("Invoice business rule.", coder_prompt)
            self.assertIn("刻意只内嵌下方分配到的业务切片", coder_prompt)
            self.assertIn(FILE_PLAN_PATH, coder_prompt)

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

    def test_execution_context_includes_tree_and_completed_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "src").mkdir(parents=True)
            skills.mkdir()
            (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")

            context = workflow._execution_context(
                {
                    "coder_1": {
                        "status": "completed",
                        "summary": "implemented accounts",
                        "changedFiles": ["src/app.py"],
                        "agentPrompt": ".harness/agent-prompts/coder_1.md",
                    }
                }
            )

            self.assertIn("src/", context)
            self.assertIn("src/app.py", context)
            self.assertIn("coder_1", context)
            self.assertIn("read_file", context)

    def test_file_plan_is_preserved_when_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            (root / "docs" / "requirements.md").write_text("Reviewed requirements.\n", encoding="utf-8")
            (root / FILE_PLAN_PATH).write_text("Manual file plan.\n", encoding="utf-8")
            workflow = Workflow(root, config(), skills, "")

            workflow.generate_prompts("Build a TODO API.")

            self.assertEqual((root / FILE_PLAN_PATH).read_text(encoding="utf-8"), "Manual file plan.\n")

    def test_integrator_runs_before_reviewer_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            skills = base / "skills"
            (root / "docs").mkdir(parents=True)
            skills.mkdir()
            (root / "docs" / "requirements.md").write_text("Reviewed requirements.\n", encoding="utf-8")
            workflow = Workflow(root, config_with_integrator(), skills, "")
            workflow.generate_prompts("Build a TODO API.")

            order = workflow._build_execution_order()

            self.assertLess(order.index("integrator"), order.index("reviewer"))
            self.assertEqual(workflow._missing_agent_prompts(order), [])

    def test_static_scanner_reports_duplicate_definitions_and_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "a.py").write_text(
                "from pkg import b\n\n"
                "def duplicate():\n    return 1\n\n"
                "def duplicate():\n    return 2\n",
                encoding="utf-8",
            )
            (root / "pkg" / "b.py").write_text("from pkg import a\n", encoding="utf-8")

            report = StaticScanner(root).scan()

            self.assertGreaterEqual(report["blockingIssueCount"], 2)
            self.assertTrue(report["duplicateDefinitions"])
            self.assertTrue(report["circularImports"])

    def test_static_scanner_supports_common_project_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            samples = {
                "src/app.js": "import './util.js';\nfunction dup() {}\nfunction dup() {}\n",
                "src/app.ts": "export class Thing {}\nexport class Thing {}\n",
                "src/main.go": "package main\nfunc Start() {}\nfunc Start() {}\n",
                "src/native.c": "int run() { return 1; }\nint run() { return 2; }\n",
                "src/App.java": "public class App {}\nclass App {}\n",
                "src/app.dart": "class Screen {}\nclass Screen {}\n",
                "src/app.php": "<?php\nfunction handle() {}\nfunction handle() {}\n",
                "src/Program.cs": "public class Program {}\nclass Program {}\n",
            }
            for path, content in samples.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            report = StaticScanner(root).scan()
            languages = {item["language"] for item in report["scannedFiles"]}
            duplicate_languages = {item["language"] for item in report["duplicateDefinitions"]}

            self.assertTrue(
                {
                    "javascript",
                    "typescript",
                    "go",
                    "c",
                    "java",
                    "dart",
                    "php",
                    "csharp",
                }.issubset(languages)
            )
            self.assertTrue({"javascript", "typescript", "go", "c", "java", "dart", "php", "csharp"}.issubset(duplicate_languages))

    def test_static_scanner_reports_javascript_cycles_and_generic_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.js").write_text("import './b.js';\nexport function a() {}\n", encoding="utf-8")
            (root / "src" / "b.js").write_text("import './a.js';\nexport function b() {}\n", encoding="utf-8")
            (root / "src" / "broken.ts").write_text("export function broken() {\n", encoding="utf-8")

            report = StaticScanner(root).scan()

            self.assertTrue(report["circularImports"])
            self.assertTrue(report["syntaxErrors"])


if __name__ == "__main__":
    unittest.main()
