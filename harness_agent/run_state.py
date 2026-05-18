from __future__ import annotations

from dataclasses import dataclass
import filecmp
import json
from pathlib import Path
import shutil
from time import time
from uuid import uuid4


STATE_DIRS = {".git", ".tasks", ".team", ".harness", "__pycache__", ".venv", "venv"}


@dataclass(frozen=True)
class Checkpoint:
    id: str
    path: Path


class RunState:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.harness_dir = self.root / ".harness"
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.harness_dir / "run-state.json"
        self.checkpoints_dir = self.harness_dir / "checkpoints"
        self.isolation_dir = self.harness_dir / "isolated"
        self.journal_dir = self.harness_dir / "patch-journal"
        for directory in (self.checkpoints_dir, self.isolation_dir, self.journal_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict | None:
        if not self.state_path.exists():
            return None
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, payload: dict) -> None:
        payload["updatedAt"] = time()
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def begin(self, mode: str, objective: str, order: list[str], results: dict | None = None) -> dict:
        payload = {
            "runId": uuid4().hex,
            "mode": mode,
            "objective": objective,
            "order": order,
            "currentIndex": 0,
            "currentAgent": "",
            "status": "in_progress",
            "startedAt": time(),
            "updatedAt": time(),
            "checkpointId": "",
            "results": results or {},
        }
        self.save(payload)
        return payload

    def make_checkpoint(self, run_id: str, agent: str, index: int) -> Checkpoint:
        checkpoint_id = f"{run_id}-{index:02d}-{agent}"
        target = self.checkpoints_dir / checkpoint_id
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        self._copy_tree(self.root, target)
        return Checkpoint(checkpoint_id, target)

    def restore_checkpoint(self, checkpoint_id: str) -> None:
        checkpoint = self.checkpoints_dir / checkpoint_id
        if not checkpoint.exists():
            raise ValueError(f"Missing checkpoint {checkpoint_id}")
        self._replace_managed_files(self.root, checkpoint)

    def prepare_isolation(self, run_id: str, agent: str, index: int) -> Path:
        target = self.isolation_dir / f"{run_id}-{index:02d}-{agent}"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        self._copy_tree(self.root, target)
        return target

    def journal_path(self, run_id: str, agent: str, index: int) -> Path:
        path = self.journal_dir / run_id / f"{index:02d}-{agent}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def merge_isolation(self, isolated_root: Path) -> list[str]:
        changed: list[str] = []
        self._delete_removed_files(self.root, isolated_root, changed)
        for source in self._managed_files(isolated_root):
            rel = source.relative_to(isolated_root)
            target = self.root / rel
            if not target.exists() or not filecmp.cmp(source, target, shallow=False):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                changed.append(str(rel))
        return sorted(set(changed))

    def cleanup_isolation(self, isolated_root: Path) -> None:
        if isolated_root.exists() and isolated_root.is_relative_to(self.isolation_dir):
            shutil.rmtree(isolated_root)

    def _copy_tree(self, source: Path, target: Path) -> None:
        for path in self._managed_files(source):
            rel = path.relative_to(source)
            destination = target / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)

    def _replace_managed_files(self, target_root: Path, snapshot_root: Path) -> None:
        removed: list[str] = []
        self._delete_removed_files(target_root, snapshot_root, removed)
        for source in self._managed_files(snapshot_root):
            rel = source.relative_to(snapshot_root)
            target = target_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _delete_removed_files(self, target_root: Path, source_root: Path, changed: list[str]) -> None:
        for target in sorted(self._managed_files(target_root), reverse=True):
            rel = target.relative_to(target_root)
            if not (source_root / rel).exists():
                target.unlink()
                changed.append(str(rel))
        for directory in sorted(self._managed_dirs(target_root), reverse=True):
            if directory == target_root:
                continue
            try:
                directory.rmdir()
            except OSError:
                pass

    def _managed_files(self, root: Path) -> list[Path]:
        if not root.exists():
            return []
        return [
            path
            for path in root.rglob("*")
            if path.is_file() and not self._is_state_path(path.relative_to(root))
        ]

    def _managed_dirs(self, root: Path) -> list[Path]:
        if not root.exists():
            return []
        return [
            path
            for path in root.rglob("*")
            if path.is_dir() and not self._is_state_path(path.relative_to(root))
        ]

    def _is_state_path(self, rel: Path) -> bool:
        return bool(set(rel.parts) & STATE_DIRS)
