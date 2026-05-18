from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from time import time


VALID_STATUS = {"pending", "blocked", "in_progress", "completed", "failed"}


@dataclass
class Task:
    id: int
    subject: str
    description: str = ""
    status: str = "pending"
    blockedBy: list[int] | None = None
    blocks: list[int] | None = None
    owner: str = ""
    createdAt: float = 0.0
    updatedAt: float = 0.0

    def __post_init__(self) -> None:
        self.blockedBy = self.blockedBy or []
        self.blocks = self.blocks or []
        now = time()
        self.createdAt = self.createdAt or now
        self.updatedAt = self.updatedAt or now


class TaskManager:
    def __init__(self, root: Path):
        self.dir = root / ".tasks"
        self.dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        owner: str = "",
    ) -> Task:
        task_id = self._next_id()
        task = Task(
            id=task_id,
            subject=subject,
            description=description,
            blockedBy=blocked_by or [],
            owner=owner,
            status="blocked" if blocked_by else "pending",
        )
        self._save(task)
        for blocker in task.blockedBy or []:
            parent = self.load(blocker)
            if parent and task.id not in (parent.blocks or []):
                parent.blocks.append(task.id)
                self._save(parent)
        return task

    def list(self) -> list[Task]:
        tasks = []
        for path in sorted(self.dir.glob("task_*.json")):
            tasks.append(Task(**json.loads(path.read_text(encoding="utf-8"))))
        return tasks

    def load(self, task_id: int) -> Task | None:
        path = self.dir / f"task_{task_id}.json"
        if not path.exists():
            return None
        return Task(**json.loads(path.read_text(encoding="utf-8")))

    def update(self, task_id: int, status: str | None = None, owner: str | None = None) -> Task:
        task = self.load(task_id)
        if not task:
            raise ValueError(f"Unknown task {task_id}")
        if status:
            if status not in VALID_STATUS:
                raise ValueError(f"Invalid status {status}")
            task.status = status
        if owner is not None:
            task.owner = owner
        task.updatedAt = time()
        self._save(task)
        if status == "completed":
            self._clear_dependency(task_id)
        return task

    def ready_tasks(self) -> list[Task]:
        return [
            task
            for task in self.list()
            if task.status == "pending" and not task.owner and not task.blockedBy
        ]

    def _clear_dependency(self, completed_id: int) -> None:
        for task in self.list():
            if completed_id in (task.blockedBy or []):
                task.blockedBy.remove(completed_id)
                if not task.blockedBy and task.status == "blocked":
                    task.status = "pending"
                task.updatedAt = time()
                self._save(task)

    def _next_id(self) -> int:
        ids = []
        for path in self.dir.glob("task_*.json"):
            try:
                ids.append(int(path.stem.split("_", 1)[1]))
            except (IndexError, ValueError):
                continue
        return max(ids, default=0) + 1

    def _save(self, task: Task) -> None:
        path = self.dir / f"task_{task.id}.json"
        path.write_text(json.dumps(asdict(task), indent=2, ensure_ascii=False), encoding="utf-8")
