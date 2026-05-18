from __future__ import annotations

import json
from pathlib import Path
from time import time

from .agents import Agent
from .config import HarnessConfig
from .message_bus import MessageBus
from .skills import SkillLoader
from .task_manager import TaskManager
from .tools import ToolRuntime


DEFAULT_ORDER = ["lead", "architect", "coder", "tester", "reviewer", "coder", "tester", "release"]


class Workflow:
    def __init__(self, root: Path, config: HarnessConfig, skills_dir: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.harness_dir = self.root / ".harness"
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskManager(self.root)
        self.bus = MessageBus(self.root)
        self.skills = SkillLoader(skills_dir)
        self.runtime = ToolRuntime(self.root, self.tasks, self.bus)
        self.config = config

    def run(self, goal: str) -> dict:
        self._bootstrap_team()
        self._bootstrap_tasks(goal)
        objective = self._objective(goal, mode="build")
        return self._run_order(objective)

    def refine(self, request: str, files: list[str] | None = None) -> dict:
        scope = f"\nAllowed files: {', '.join(files)}" if files else "\nAllowed files: infer minimal scope"
        task = self.tasks.create(
            subject=f"Refine: {request[:80]}",
            description=f"{request}{scope}",
            owner="",
        )
        objective = self._objective(f"{request}{scope}", mode="refine", task_id=task.id)
        return self._run_order(objective, order=["lead", "coder", "tester", "reviewer", "release"])

    def _run_order(self, objective: str, order: list[str] | None = None) -> dict:
        results = {}
        for name in order or DEFAULT_ORDER:
            config = self.config.agents.get(name)
            if not config or not config.enabled:
                continue
            agent = Agent(config, self.root, self.tasks, self.bus, self.skills, self.runtime)
            results[name] = agent.run(objective)
            self._write_summary(results)
        return results

    def _bootstrap_team(self) -> None:
        team_path = self.root / ".team" / "config.json"
        team_path.parent.mkdir(parents=True, exist_ok=True)
        team = {
            name: {"name": name, "role": cfg.role, "model": cfg.model, "status": "ready"}
            for name, cfg in self.config.agents.items()
            if cfg.enabled
        }
        team_path.write_text(json.dumps(team, ensure_ascii=False, indent=2), encoding="utf-8")

    def _bootstrap_tasks(self, goal: str) -> None:
        if self.tasks.list():
            return
        setup = self.tasks.create("Plan architecture", goal, owner="")
        code = self.tasks.create("Implement project code", goal, blocked_by=[setup.id], owner="")
        test = self.tasks.create("Write and run tests", goal, blocked_by=[code.id], owner="")
        review = self.tasks.create("Review and fix issues", goal, blocked_by=[test.id], owner="")
        self.tasks.create("Prepare release notes", goal, blocked_by=[review.id], owner="")

    def _objective(self, text: str, mode: str, task_id: int | None = None) -> str:
        return f"""
Mode: {mode}
Target project root: {self.root}
Task id: {task_id or "initial"}

User goal/request:
{text}

Work as a multi-agent software team. Build incrementally:
1. Inspect current files.
2. Follow the task graph.
3. Generate or modify only needed files.
4. Run suitable verification commands.
5. Leave concise artifacts under .harness when useful.
6. Finish when your role's work is done.
""".strip()

    def _write_summary(self, results: dict) -> None:
        payload = {
            "updatedAt": time(),
            "results": results,
            "tasks": [task.__dict__ for task in self.tasks.list()],
        }
        (self.harness_dir / "run-summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
