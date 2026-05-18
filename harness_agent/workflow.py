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
BUILD_EXECUTION_BASE_ORDER = ["architect", "tester", "reviewer", "release"]
BUILD_EXECUTION_ORDER = DEFAULT_ORDER[1:]
AGENT_PROMPTS_DIR = "agent-prompts"
CODER_AGENT = "coder"
CODER_SLICE_TARGET_CHARS = 2400


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
        self.plan(goal)
        self.generate_prompts(goal)
        return self.execute(goal)

    def plan(self, goal: str) -> dict:
        self._bootstrap_team()
        if self._requirements_has_content():
            return {
                "plan": {
                    "status": "skipped",
                    "summary": "docs/requirements.md already has content; preserved for review.",
                    "requirementsPath": "docs/requirements.md",
                }
            }
        self._write_requirements_plan(goal)
        return {
            "plan": {
                "status": "completed",
                "summary": "Generated docs/requirements.md for human review.",
                "requirementsPath": "docs/requirements.md",
            }
        }

    def generate_prompts(self, goal: str) -> dict:
        self._bootstrap_team()
        if not self._requirements_has_content():
            raise RuntimeError("docs/requirements.md is empty or missing. Run the plan stage first.")
        self._bootstrap_tasks(goal)
        objective = self._objective(goal, mode="build", use_existing_requirements=True)
        generated: dict[str, dict[str, str]] = {}
        for name in self._unique_enabled_order(BUILD_EXECUTION_BASE_ORDER):
            config = self.config.agents[name]
            path = self._agent_prompt_path(name)
            existed_with_content = path.exists() and bool(path.read_text(encoding="utf-8").strip())
            self._ensure_agent_prompt(name, config.role, objective, "build")
            generated[name] = {
                "status": "skipped" if existed_with_content else "completed",
                "promptPath": path.relative_to(self.root).as_posix(),
                "summary": "Existing non-empty prompt preserved."
                if existed_with_content
                else "Generated dynamic execution prompt for review.",
            }
        coder_config = self.config.agents.get(CODER_AGENT)
        if coder_config and coder_config.enabled:
            audit_path = self._agent_prompt_path(CODER_AGENT)
            existed_with_content = audit_path.exists() and bool(audit_path.read_text(encoding="utf-8").strip())
            self._ensure_agent_prompt(
                CODER_AGENT,
                coder_config.role,
                objective,
                "build",
                stage_note=self._coder_audit_note(),
            )
            generated[CODER_AGENT] = {
                "status": "skipped" if existed_with_content else "completed",
                "promptPath": audit_path.relative_to(self.root).as_posix(),
                "summary": "Existing non-empty coder audit prompt preserved."
                if existed_with_content
                else "Generated coder audit prompt; execution uses coder_*.md files.",
            }

            coder_slices = self._coder_business_slices()
            for occurrence, business_slice in enumerate(coder_slices, start=1):
                prompt_name = self._coder_prompt_name(occurrence)
                path = self._agent_prompt_path(prompt_name)
                existed_with_content = path.exists() and bool(path.read_text(encoding="utf-8").strip())
                self._ensure_agent_prompt(
                    prompt_name,
                    coder_config.role,
                    objective,
                    "build",
                    agent_name=CODER_AGENT,
                    stage_note=self._coder_stage_note(occurrence, len(coder_slices), business_slice),
                    requirements_snapshot=self._coder_execution_requirements_note(),
                )
                generated[prompt_name] = {
                    "status": "skipped" if existed_with_content else "completed",
                    "promptPath": path.relative_to(self.root).as_posix(),
                    "summary": "Existing non-empty coder stage prompt preserved."
                    if existed_with_content
                    else "Generated coder stage prompt for execution.",
                }
        self._write_summary({"prompts": generated})
        return generated

    def execute(self, goal: str) -> dict:
        self._bootstrap_team()
        if not self._requirements_has_content():
            raise RuntimeError("docs/requirements.md is empty or missing. Run the plan stage first.")
        self._bootstrap_tasks(goal)
        order = self._build_execution_order()
        missing = self._missing_agent_prompts(order, mode="build")
        if missing:
            names = ", ".join(missing)
            raise RuntimeError(f"Missing dynamic prompts for: {names}. Run the prompts stage first.")
        objective = self._objective(goal, mode="build", use_existing_requirements=True)
        return self._run_order(objective, mode="build", order=order, allow_prompt_generation=False)

    def refine(self, request: str, files: list[str] | None = None) -> dict:
        scope = f"\nAllowed files: {', '.join(files)}" if files else "\nAllowed files: infer minimal scope"
        task = self.tasks.create(
            subject=f"Refine: {request[:80]}",
            description=f"{request}{scope}",
            owner="",
        )
        objective = self._objective(f"{request}{scope}", mode="refine", task_id=task.id)
        return self._run_order(objective, mode="refine", order=["lead", "coder", "tester", "reviewer", "release"])

    def _run_order(
        self,
        objective: str,
        mode: str,
        order: list[str] | None = None,
        allow_prompt_generation: bool = True,
    ) -> dict:
        order = order or DEFAULT_ORDER
        state = self._load_or_begin_state(mode, objective, order)
        results = state.get("results", {})
        start_index = int(state.get("currentIndex", 0))
        occurrences: dict[str, int] = {}
        for index, name in enumerate(order[start_index:], start=start_index):
            config = self.config.agents.get(name)
            if not config or not config.enabled:
                continue
            occurrences[name] = self._agent_occurrences(order[: index + 1], name)
            prompt_name = self._execution_prompt_name(name, mode, occurrences[name])
            agent_prompt = (
                self._ensure_agent_prompt(prompt_name, config.role, objective, mode, agent_name=name)
                if allow_prompt_generation
                else self._read_required_agent_prompt(prompt_name)
            )
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
                agent_objective = self._objective_with_agent_prompt(objective, name, agent_prompt)
                agent_objective = agent_objective.replace(str(self.root), str(isolated_root))
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
                    results[prompt_name] = {**result, "rolledBackTo": checkpoint.id}
                    state.update({"status": "failed", "results": results, "phase": "rolled_back"})
                    self.state.save(state)
                    self._write_summary(results)
                    self.state.cleanup_isolation(isolated_root)
                    return results
                changed = self.state.merge_isolation(isolated_root)
                results[prompt_name] = {
                    **result,
                    "checkpointId": checkpoint.id,
                    "changedFiles": changed,
                    "agentPrompt": self._agent_prompt_path(prompt_name).relative_to(self.root).as_posix(),
                }
                self.state.cleanup_isolation(isolated_root)
            except Exception as exc:
                self.state.restore_checkpoint(checkpoint.id)
                results[prompt_name] = {
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

    def _build_execution_order(self) -> list[str]:
        order: list[str] = []
        for name in BUILD_EXECUTION_BASE_ORDER:
            if name == "tester":
                coder_count = self._reviewed_coder_prompt_count()
                order.extend([CODER_AGENT, "tester"] * max(coder_count, 1))
                continue
            order.append(name)
        return order

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

    def _write_requirements_plan(self, goal: str) -> None:
        path = self.root / "docs" / "requirements.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return
        content = f"""# Business Requirements

## Goal

{goal}

## Review Notes

- This file is the plan and business requirements source for the build.
- Review and edit this document before running the prompt generation stage.
- Add confirmed business rules, boundaries, permissions, state transitions, data consistency rules, and exception semantics here.
- If a business rule is not recorded here, downstream agents must not invent it.

## Confirmed Requirements

- TODO: Review the user goal above and replace this line with confirmed requirements before implementation.
"""
        path.write_text(content, encoding="utf-8")

    def _agent_prompt_path(self, agent_name: str) -> Path:
        return self.harness_dir / AGENT_PROMPTS_DIR / f"{agent_name}.md"

    def _ensure_agent_prompt(
        self,
        prompt_name: str,
        role: str,
        objective: str,
        mode: str,
        agent_name: str | None = None,
        stage_note: str = "",
        requirements_snapshot: str | None = None,
    ) -> str:
        path = self._agent_prompt_path(prompt_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing.strip():
                return existing

        content = self._default_agent_prompt(
            agent_name or prompt_name,
            role,
            objective,
            mode,
            stage_note,
            requirements_snapshot,
        )
        path.write_text(content, encoding="utf-8")
        return content

    def _read_required_agent_prompt(self, agent_name: str) -> str:
        path = self._agent_prompt_path(agent_name)
        if not path.exists():
            raise RuntimeError(f"Missing prompt file: {path.relative_to(self.root).as_posix()}")
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            raise RuntimeError(f"Prompt file is empty: {path.relative_to(self.root).as_posix()}")
        return content

    def _missing_agent_prompts(self, order: list[str], mode: str = "build") -> list[str]:
        missing: list[str] = []
        occurrences: dict[str, int] = {}
        for name in order:
            config = self.config.agents.get(name)
            if not config or not config.enabled:
                continue
            occurrences[name] = occurrences.get(name, 0) + 1
            prompt_name = self._execution_prompt_name(name, mode, occurrences[name])
            path = self._agent_prompt_path(prompt_name)
            if not path.exists() or not path.read_text(encoding="utf-8").strip():
                missing.append(prompt_name)
        return missing

    def _agent_occurrences(self, order: list[str], agent_name: str) -> int:
        return sum(
            1
            for name in order
            if name == agent_name and (config := self.config.agents.get(name)) and config.enabled
        )

    def _coder_prompt_name(self, occurrence: int) -> str:
        return f"{CODER_AGENT}_{occurrence}"

    def _execution_prompt_name(self, agent_name: str, mode: str, occurrence: int) -> str:
        if mode == "build" and agent_name == CODER_AGENT:
            return self._coder_prompt_name(occurrence)
        return agent_name

    def _unique_enabled_order(self, order: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for name in order:
            config = self.config.agents.get(name)
            if not config or not config.enabled or name in seen:
                continue
            unique.append(name)
            seen.add(name)
        return unique

    def _default_agent_prompt(
        self,
        agent_name: str,
        role: str,
        objective: str,
        mode: str,
        stage_note: str = "",
        requirements_snapshot: str | None = None,
    ) -> str:
        requirements = self._read_text_if_exists(self.root / "docs" / "requirements.md")
        task_payload = [task.__dict__ for task in self.tasks.list()]
        requirements_text = (
            requirements_snapshot
            if requirements_snapshot is not None
            else requirements.strip() or "(docs/requirements.md is missing or empty.)"
        )
        tasks_text = json.dumps(task_payload, ensure_ascii=False, indent=2) if task_payload else "[]"
        return f"""# Agent Prompt: {agent_name}

## Purpose

This prompt is generated for audit and execution. Runtime creates it only when this file is missing or empty; non-empty prompt files are preserved.

## Agent Role

{role or "(none)"}

## Source Of Truth

- Read and follow `docs/requirements.md` before making behavior decisions.
- Treat confirmed requirements and the task graph as the source of truth.
- Do not invent business rules. If missing business semantics would change behavior, use `ask_user` in planning or report the risk in your result.

## Confirmed Requirements Snapshot

{requirements_text}

## Current Task Graph Snapshot

```json
{tasks_text}
```

## Execution Scope

- Mode: {mode}
- Work only inside the target project root.
- Keep changes minimal and aligned with this role.
- Preserve files outside your role unless a task dependency requires a small coordinated change.
{stage_note}

## Workflow Objective

{objective}
"""

    def _coder_stage_note(self, occurrence: int, total: int, business_slice: str) -> str:
        if occurrence == 1:
            focus = "Implement the first independent slice of the confirmed business requirements and leave later slices untouched unless needed for integration."
        else:
            focus = "Continue from earlier coder output and implement the next independent slice, focusing on incomplete business behavior and review/test feedback."
        return f"""- Coder stage: {occurrence}
- Coder stages total: {total}
- This file is the execution prompt for coder pass {occurrence}; load this prompt instead of `coder.md`.
- `coder.md` is an audit overview only and must not be used as the execution source.
- {focus}
- Keep the implemented slice coherent and verifiable before finishing.

## Assigned Business Slice

Implement only this slice unless a tiny integration change is required:

{business_slice.strip()}"""

    def _coder_audit_note(self) -> str:
        return """- This `coder.md` file is for human audit of the overall coder responsibility only.
- Build execution loads `coder_1.md`, `coder_2.md`, ... as the authoritative code-generation prompts.
- Do not rely on this audit overview as the source for implementation details."""

    def _coder_execution_requirements_note(self) -> str:
        return (
            "Full requirements are available in `docs/requirements.md` and should be read when needed. "
            "This execution prompt intentionally embeds only the assigned business slice below to keep "
            "the coder context focused."
        )

    def _coder_business_slices(self) -> list[str]:
        requirements = self._read_text_if_exists(self.root / "docs" / "requirements.md").strip()
        if not requirements:
            return ["(No confirmed requirements were available.)"]
        sections = self._split_markdown_sections(requirements)
        slices: list[str] = []
        current: list[str] = []
        current_size = 0
        for section in sections:
            section_size = len(section)
            if current and current_size + section_size > CODER_SLICE_TARGET_CHARS:
                slices.append("\n\n".join(current))
                current = []
                current_size = 0
            current.append(section)
            current_size += section_size
        if current:
            slices.append("\n\n".join(current))
        return slices or [requirements]

    def _split_markdown_sections(self, text: str) -> list[str]:
        sections: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            if line.startswith("## ") and any(item.startswith("## ") for item in current):
                sections.append("\n".join(current).strip())
                current = []
            current.append(line)
        if current:
            sections.append("\n".join(current).strip())
        if len(sections) <= 1:
            return self._split_by_paragraphs(text)
        return [section for section in sections if section]

    def _split_by_paragraphs(self, text: str) -> list[str]:
        paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        return paragraphs or [text]

    def _reviewed_coder_prompt_count(self) -> int:
        prompts_dir = self.harness_dir / AGENT_PROMPTS_DIR
        if not prompts_dir.exists():
            return 0
        count = 0
        while True:
            path = prompts_dir / f"{CODER_AGENT}_{count + 1}.md"
            if not path.exists() or not path.read_text(encoding="utf-8").strip():
                break
            count += 1
        return count

    def _read_text_if_exists(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _objective_with_agent_prompt(self, objective: str, agent_name: str, agent_prompt: str) -> str:
        return f"""{objective}

Dynamic execution prompt for `{agent_name}`:
{agent_prompt}
""".strip()

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
