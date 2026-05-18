from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .config import AgentConfig
from .llm import OpenAICompatibleClient
from .message_bus import MessageBus
from .skills import SkillLoader
from .task_manager import TaskManager
from .tools import TOOL_SPEC, ToolRuntime


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        root: Path,
        tasks: TaskManager,
        bus: MessageBus,
        skills: SkillLoader,
        runtime: ToolRuntime,
        global_prompt: str = "",
    ):
        self.config = config
        self.root = root
        self.tasks = tasks
        self.bus = bus
        self.skills = skills
        self.runtime = runtime
        self.client = OpenAICompatibleClient(config)
        self.global_prompt = global_prompt.strip()
        self.runtime_loaded_skills = set(config.skills)

    def run(self, objective: str) -> dict[str, Any]:
        # The agent loop is deliberately tool-gated: every model response is
        # parsed into one JSON action, dispatched, then fed back as tool_result.
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": objective},
        ]
        final = ""
        for step in range(self.config.max_steps):
            inbox = self.bus.read_inbox(self.config.name)
            if inbox:
                messages.append({"role": "user", "content": f"<inbox>{json.dumps(inbox, ensure_ascii=False)}</inbox>"})
            messages.append({"role": "user", "content": self._state_snapshot(step)})
            raw = self.client.complete(messages)
            messages.append({"role": "assistant", "content": raw})

            action = self._parse_action(raw)
            result = self.runtime.dispatch(self.config.name, action)
            self._remember_loaded_skill(action, result)
            messages.append({"role": "user", "content": f"<tool_result>{json.dumps(result, ensure_ascii=False)}</tool_result>"})
            if result.get("finished"):
                final = str(result.get("result", ""))
                status = str(result.get("status", "completed"))
                if status not in {"completed", "failed"}:
                    status = "failed"
                return {"status": status, "summary": final, "steps": step + 1}
        return {
            "status": "failed",
            "summary": final or f"{self.config.name} reached max steps",
            "steps": self.config.max_steps,
        }

    def _system_prompt(self) -> str:
        return f"""
You are agent `{self.config.name}`.
Role: {self.config.role}

Project root: {self.root}

Global team instructions:
{self.global_prompt or "(none)"}

Skills available:
{self.skills.descriptions()}

Default loaded skills:
{self.skills.render(self.config.skills)}

{TOOL_SPEC}
""".strip()

    def _state_snapshot(self, step: int) -> str:
        tasks = [task.__dict__ for task in self.tasks.list()]
        return json.dumps(
            {
                "step": step,
                "agent": self.config.name,
                "loadedSkills": sorted(self.runtime_loaded_skills),
                "tasks": tasks,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _parse_action(self, raw: str) -> dict[str, Any]:
        # Non-JSON model output is treated as a final summary instead of being
        # executed, which keeps accidental prose from triggering side effects.
        text = self._extract_json_candidate(raw)
        try:
            action = json.loads(text)
        except json.JSONDecodeError:
            action = {"tool": "finish", "args": {"summary": raw}, "thought": "non-json final"}
        if "tool" not in action:
            return {"tool": "finish", "args": {"summary": raw}, "thought": "missing tool"}
        return action

    def _extract_json_candidate(self, raw: str) -> str:
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            return fenced.group(1).strip()
        if text.startswith("{") and text.endswith("}"):
            return text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return text[start : end + 1].strip()
        return text

    def _remember_loaded_skill(self, action: dict[str, Any], result: dict[str, Any]) -> None:
        if action.get("tool") != "load_skill" or not result.get("ok"):
            return
        payload = result.get("result")
        if isinstance(payload, dict) and isinstance(payload.get("name"), str):
            self.runtime_loaded_skills.add(payload["name"])
