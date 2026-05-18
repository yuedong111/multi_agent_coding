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
        if self._requirements_has_content():
            self._bootstrap_tasks(goal)
            objective = self._objective(goal, mode="build", use_existing_requirements=True)
            return self._run_order(objective, mode="build", order=DEFAULT_ORDER[1:])
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

            question_handler = self._make_user_question_handler(state, name)
            isolated_runtime = ToolRuntime(
                isolated_root,
                self.tasks,
                self.bus,
                self.skills,
                journal_path,
                user_question_handler=question_handler,
            )
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
        requirements_note = "Use docs/requirements.md as the confirmed business requirements."
        setup = self.tasks.create("Plan architecture from requirements", f"{goal}\n\n{requirements_note}", owner="")
        code = self.tasks.create("Implement project code", goal, blocked_by=[setup.id], owner="")
        test = self.tasks.create("Write and run tests", goal, blocked_by=[code.id], owner="")
        review = self.tasks.create("Review and fix issues", goal, blocked_by=[test.id], owner="")
        self.tasks.create("Prepare release notes", goal, blocked_by=[review.id], owner="")

    def _requirements_has_content(self) -> bool:
        path = self.root / "docs" / "requirements.md"
        return path.exists() and bool(path.read_text(encoding="utf-8").strip())

    def _objective(
        self,
        text: str,
        mode: str,
        task_id: int | None = None,
        use_existing_requirements: bool = False,
    ) -> str:
        planning_gate = ""
        if mode == "build" and use_existing_requirements:
            planning_gate = """
Requirements gate:
- docs/requirements.md already contains confirmed business requirements, so the lead planning stage was skipped.
- Read docs/requirements.md before making architecture or implementation decisions.
- Treat docs/requirements.md and the task graph as the source of truth.
- Do not ask planning-stage clarification questions unless the existing requirements contradict the user request or make implementation impossible.
"""
        elif mode == "build":
            planning_gate = """
Planning gate:
- The lead agent owns the initial plan and task graph.
- Before creating implementation tasks, inspect the request and existing files for business logic ambiguity.
- If any unresolved business question could change code behavior, call ask_user with a concise question and impact.
- After ask_user returns, read or rely on docs/requirements.md, then create the task graph from the confirmed requirements.
- Downstream agents must treat docs/requirements.md and the task graph as the source of truth.
"""
        return f"""
Mode: {mode}
Target project root: {self.root}
Task id: {task_id or "initial"}

User goal/request:
{text}

{planning_gate}

Work as a multi-agent software team. Build incrementally:
1. Inspect current files.
2. Resolve planning-stage business ambiguity before implementation.
3. Follow the task graph.
4. Generate or modify only needed files.
5. Run suitable verification commands.
6. Leave concise artifacts under .harness when useful.
7. Finish when your role's work is done.
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

    def _make_user_question_handler(self, state: dict, agent_name: str):
        def ask(payload: dict[str, str]) -> str:
            pending = {
                "agent": agent_name,
                "question": payload["question"],
                "impact": payload.get("impact", ""),
                "requirementsPath": payload.get("path", "docs/requirements.md"),
            }
            state.update(
                {
                    "status": "blocked_waiting_user",
                    "phase": "waiting_for_user",
                    "pendingQuestion": pending,
                }
            )
            self.state.save(state)
            print("\n[question] Business clarification required")
            print(f"Agent: {agent_name}")
            print(f"Question: {pending['question']}")
            if pending["impact"]:
                print(f"Impact: {pending['impact']}")
            answer = input("Answer: ")
            state.update(
                {
                    "status": "in_progress",
                    "phase": "user_answered",
                    "pendingQuestion": "",
                }
            )
            self.state.save(state)
            return answer

        return ask
