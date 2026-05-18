from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Callable
from time import time
from datetime import datetime, timezone

from .message_bus import MessageBus
from .skills import SkillLoader
from .task_manager import TaskManager

UserQuestionHandler = Callable[[dict[str, str]], str]


class ToolRuntime:
    def __init__(
        self,
        root: Path,
        tasks: TaskManager,
        bus: MessageBus,
        skills: SkillLoader,
        journal_path: Path | None = None,
        user_question_handler: UserQuestionHandler | None = None,
        requirements_path: str = "docs/requirements.md",
    ):
        self.root = root.resolve()
        self.tasks = tasks
        self.bus = bus
        self.skills = skills
        self.journal_path = journal_path
        self.user_question_handler = user_question_handler
        self.requirements_path = requirements_path
        self.command_failures: list[dict[str, Any]] = []

    def dispatch(self, agent: str, action: dict[str, Any]) -> dict[str, Any]:
        name = action.get("tool")
        args = action.get("args", {})
        try:
            if name == "load_skill":
                return self._ok(self.load_skill(args["name"]))
            if name == "list_files":
                return self._ok(self.list_files(args.get("path", ".")))
            if name == "read_file":
                return self._ok(self.read_file(args["path"]))
            if name == "write_file":
                return self._ok(self.write_file(args["path"], args["content"]))
            if name == "append_file":
                return self._ok(self.append_file(args["path"], args["content"]))
            if name == "run_command":
                return self._ok(self.run_command(args["command"]))
            if name == "create_task":
                task = self.tasks.create(
                    args["subject"],
                    args.get("description", ""),
                    args.get("blockedBy", []),
                    args.get("owner", ""),
                )
                return self._ok(task.__dict__)
            if name == "update_task":
                task = self.tasks.update(args["id"], args.get("status"), args.get("owner"))
                return self._ok(task.__dict__)
            if name == "send_message":
                return self._ok(self.bus.send(agent, args["to"], args["content"]))
            if name == "ask_user":
                return self._ok(
                    self.ask_user(
                        agent,
                        question=args["question"],
                        impact=args.get("impact", ""),
                        path=args.get("path"),
                    )
                )
            if name == "record_requirement":
                return self._ok(
                    self.record_requirement(
                        question=args["question"],
                        impact=args.get("impact", ""),
                        answer=args["answer"],
                        path=args.get("path"),
                    )
                )
            if name == "finish":
                return {
                    "ok": True,
                    "finished": True,
                    "result": args.get("summary", ""),
                    "status": args.get("status", "completed"),
                }
            return {"ok": False, "error": f"Unknown tool {name}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def load_skill(self, name: str) -> dict[str, str]:
        skill = self.skills.load(name)
        return {
            "name": skill["name"],
            "description": skill["description"],
            "content": f'<skill name="{skill["name"]}">\n{skill["body"]}\n</skill>',
        }

    def list_files(self, path: str) -> str:
        base = self._safe(path)
        lines = []
        for item in sorted(base.rglob("*")):
            if self._is_hidden_state(item):
                continue
            rel = item.relative_to(self.root)
            suffix = "/" if item.is_dir() else ""
            lines.append(f"{rel}{suffix}")
            if len(lines) >= 300:
                lines.append("... truncated")
                break
        return "\n".join(lines) or "(empty)"

    def read_file(self, path: str) -> str:
        target = self._safe(path)
        text = target.read_text(encoding="utf-8")
        return text[:60000]

    def write_file(self, path: str, content: str) -> str:
        target = self._safe(path)
        before = self._read_optional(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._journal_file_change("write_file", target, before, content)
        return f"wrote {target.relative_to(self.root)}"

    def append_file(self, path: str, content: str) -> str:
        target = self._safe(path)
        before = self._read_optional(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
        after = self._read_optional(target)
        self._journal_file_change("append_file", target, before, after)
        return f"appended {target.relative_to(self.root)}"

    def run_command(self, command: str) -> str:
        completed = subprocess.run(
            command,
            cwd=self.root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-20000:],
            "stderr": completed.stderr[-20000:],
        }
        if completed.returncode != 0:
            self.command_failures.append({"command": command, **output})
        return json.dumps(output, ensure_ascii=False, indent=2)

    def ask_user(self, agent: str, question: str, impact: str = "", path: str | None = None) -> dict[str, str]:
        if not self.user_question_handler:
            raise RuntimeError("User input is required, but no user question handler is configured")
        payload = {
            "agent": agent,
            "question": question.strip(),
            "impact": impact.strip(),
            "path": path or self.requirements_path,
        }
        answer = self.user_question_handler(payload).strip()
        if not answer:
            raise RuntimeError("User answer cannot be empty")
        requirements_path = self.record_requirement(
            question=payload["question"],
            impact=payload["impact"],
            answer=answer,
            path=payload["path"],
        )
        return {
            "question": payload["question"],
            "impact": payload["impact"],
            "answer": answer,
            "requirementsPath": requirements_path,
        }

    def record_requirement(self, question: str, impact: str, answer: str, path: str | None = None) -> str:
        target = self._safe(path or self.requirements_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = self._read_optional(target)
        if not target.exists():
            target.write_text("# Business Requirements\n\n", encoding="utf-8")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = [
            "## User Clarification",
            "",
            f"- Time: {timestamp}",
            f"- Question: {question.strip()}",
        ]
        if impact.strip():
            entry.append(f"- Impact: {impact.strip()}")
        entry.extend(
            [
                f"- Answer: {answer.strip()}",
                "",
            ]
        )
        with target.open("a", encoding="utf-8") as file:
            file.write("\n".join(entry))
        self._journal_file_change("record_requirement", target, before, self._read_optional(target))
        return target.relative_to(self.root).as_posix()

    def _safe(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"Path escapes project root: {path}")
        return target

    def _is_hidden_state(self, path: Path) -> bool:
        parts = set(path.relative_to(self.root).parts)
        return bool(parts & {".git", ".tasks", ".team", ".harness", "__pycache__", ".venv"})

    def _ok(self, result: Any) -> dict[str, Any]:
        return {"ok": True, "result": result}

    def _read_optional(self, target: Path) -> str | None:
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def _journal_file_change(self, operation: str, target: Path, before: str | None, after: str | None) -> None:
        if not self.journal_path:
            return
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time(),
            "operation": operation,
            "path": str(target.relative_to(self.root)),
            "before": before,
            "after": after,
        }
        with self.journal_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


TOOL_SPEC = """
You may call exactly one tool per turn by returning strict JSON:
{"thought":"short reasoning","tool":"tool_name","args":{...}}

Available tools:
- load_skill {"name":"skill-name"}
- list_files {"path":"."}
- read_file {"path":"relative/path"}
- write_file {"path":"relative/path","content":"full file content"}
- append_file {"path":"relative/path","content":"text"}
- run_command {"command":"shell command to run inside project root"}
- create_task {"subject":"title","description":"details","blockedBy":[1],"owner":"agent"}
- update_task {"id":1,"status":"pending|blocked|in_progress|completed|failed","owner":"agent or empty"}
- send_message {"to":"agent","content":"message"}
- ask_user {"question":"concise question","impact":"which behavior/files this affects","path":"docs/requirements.md"}
- record_requirement {"question":"question or decision","impact":"which behavior/files this affects","answer":"confirmed business rule","path":"docs/requirements.md"}
- finish {"summary":"what you completed","status":"completed|failed"}

Rules:
- Return JSON only. No Markdown outside JSON.
- Use relative paths only.
- Use load_skill before applying a skill that is listed as available but not already loaded.
- Make small, verifiable steps.
- Do not rewrite unrelated files.
- During planning, call ask_user before creating tasks when unresolved business logic would change behavior.
- Continue implementation only after user clarifications are recorded in the requirements document.
"""
