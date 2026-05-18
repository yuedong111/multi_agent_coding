from __future__ import annotations

import json
from pathlib import Path
from time import time

from .agents import Agent
from .config import HarnessConfig
from .message_bus import MessageBus
from .run_state import RunState
from .skills import SkillLoader
from .task_manager import TaskManager
from .tools import ToolRuntime


DEFAULT_ORDER = ["lead", "architect", "coder", "tester", "reviewer", "coder", "tester", "release"]


class Workflow:
    def __init__(self, root: Path, config: HarnessConfig, skills_dir: Path, global_prompt: str = ""):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.harness_dir = self.root / ".harness"
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskManager(self.root)
        self.bus = MessageBus(self.root)
        self.skills = SkillLoader(skills_dir)
        self.runtime = ToolRuntime(self.root, self.tasks, self.bus, self.skills)
        self.state = RunState(self.root)
        self.config = config
        self.global_prompt = global_prompt

    def run(self, goal: str) -> dict:
        self._bootstrap_team()
        self._bootstrap_tasks(goal)
        objective = self._objective(goal, mode="build")
        return self._run_order(objective, mode="build")

    def refine(self, request: str, files: list[str] | None = None) -> dict:
        scope = f"\nAllowed files: {', '.join(files)}" if files else "\nAllowed files: infer minimal scope"
        task = self.tasks.create(
            subject=f"Refine: {request[:80]}",
            description=f"{request}{scope}",
            owner="",
        )
        objective = self._objective(f"{request}{scope}", mode="refine", task_id=task.id)
        return self._run_order(objective, mode="refine", order=["lead", "coder", "tester", "reviewer", "release"])

    def _run_order(self, objective: str, mode: str, order: list[str] | None = None) -> dict:
        order = order or DEFAULT_ORDER
        state = self._load_or_begin_state(mode, objective, order)
        results = state.get("results", {})
        start_index = int(state.get("currentIndex", 0))
        for index, name in enumerate(order[start_index:], start=start_index):
            config = self.config.agents.get(name)
            if not config or not config.enabled:
                continue
            checkpoint = self.state.make_checkpoint(state["runId"], name, index)
            isolated_root = self.state.prepare_isolation(state["runId"], name, index)
            journal_path = self.state.journal_path(state["runId"], name, index)
            state.update(
                {
                    "currentIndex": index,
                    "currentAgent": name,
                    "checkpointId": checkpoint.id,
                    "status": "in_progress",
                    "phase": "agent_started",
                }
            )
            self.state.save(state)

            isolated_runtime = ToolRuntime(isolated_root, self.tasks, self.bus, self.skills, journal_path)
            agent = Agent(config, isolated_root, self.tasks, self.bus, self.skills, isolated_runtime, self.global_prompt)
            try:
                agent_objective = objective.replace(str(self.root), str(isolated_root))
                result = agent.run(agent_objective)
                if name == "tester" and isolated_runtime.command_failures:
                    result = {
                        **result,
                        "status": "failed",
                        "commandFailures": isolated_runtime.command_failures,
                        "summary": f"tester command failed; rolled back {name}",
                    }
                if result.get("status") != "completed":
                    self.state.restore_checkpoint(checkpoint.id)
                    results[name] = {**result, "rolledBackTo": checkpoint.id}
                    state.update({"status": "failed", "results": results, "phase": "rolled_back"})
                    self.state.save(state)
                    self._write_summary(results)
                    self.state.cleanup_isolation(isolated_root)
                    return results
                changed = self.state.merge_isolation(isolated_root)
                results[name] = {**result, "checkpointId": checkpoint.id, "changedFiles": changed}
                self.state.cleanup_isolation(isolated_root)
            except Exception as exc:
                self.state.restore_checkpoint(checkpoint.id)
                results[name] = {
                    "status": "failed",
                    "summary": str(exc),
                    "checkpointId": checkpoint.id,
                    "rolledBackTo": checkpoint.id,
                }
                state.update({"status": "failed", "results": results, "phase": "rolled_back"})
                self.state.save(state)
                self._write_summary(results)
                self.state.cleanup_isolation(isolated_root)
                return results

            state.update(
                {
                    "currentIndex": index + 1,
                    "currentAgent": "",
                    "checkpointId": "",
                    "results": results,
                    "phase": "agent_completed",
                }
            )
            self.state.save(state)
            self._write_summary(results)
        state.update({"status": "completed", "currentIndex": len(order), "currentAgent": "", "checkpointId": ""})
        self.state.save(state)
        return results

    def _load_or_begin_state(self, mode: str, objective: str, order: list[str]) -> dict:
        state = self.state.load()
        if not state or state.get("status") != "in_progress":
            return self.state.begin(mode, objective, order)
        if state.get("mode") != mode or state.get("objective") != objective or state.get("order") != order:
            return self.state.begin(mode, objective, order)
        checkpoint_id = state.get("checkpointId")
        if checkpoint_id:
            self.state.restore_checkpoint(checkpoint_id)
            state["phase"] = "resumed_after_rollback"
            self.state.save(state)
        return state

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
