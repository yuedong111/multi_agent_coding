from __future__ import annotations

import json
from pathlib import Path
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
    ):
        self.config = config
        self.root = root
        self.tasks = tasks
        self.bus = bus
        self.skills = skills
        self.runtime = runtime
        self.client = OpenAICompatibleClient(config)

    def run(self, objective: str) -> str:
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
            messages.append({"role": "user", "content": f"<tool_result>{json.dumps(result, ensure_ascii=False)}</tool_result>"})
            if result.get("finished"):
                final = str(result.get("result", ""))
                break
        return final or f"{self.config.name} reached max steps"

    def _system_prompt(self) -> str:
        return f"""
You are agent `{self.config.name}`.
Role: {self.config.role}

Project root: {self.root}

Skills available:
{self.skills.descriptions()}

Loaded skills:
{self.skills.render(self.config.skills)}

{TOOL_SPEC}
""".strip()

    def _state_snapshot(self, step: int) -> str:
        tasks = [task.__dict__ for task in self.tasks.list()]
        return json.dumps(
            {
                "step": step,
                "agent": self.config.name,
                "tasks": tasks,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _parse_action(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            action = json.loads(text)
        except json.JSONDecodeError:
            action = {"tool": "finish", "args": {"summary": raw}, "thought": "non-json final"}
        if "tool" not in action:
            return {"tool": "finish", "args": {"summary": raw}, "thought": "missing tool"}
        return action
