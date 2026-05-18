from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentConfig:
    name: str
    role: str
    model: str
    base_url: str
    api_key_env: str
    temperature: float
    max_steps: int
    skills: list[str]
    enabled: bool = True


@dataclass(frozen=True)
class HarnessConfig:
    agents: dict[str, AgentConfig]


def load_config(path: Path) -> HarnessConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    defaults: dict[str, Any] = raw.get("defaults", {})
    agents: dict[str, AgentConfig] = {}

    for name, value in raw.get("agents", {}).items():
        merged = {**defaults, **value}
        agents[name] = AgentConfig(
            name=name,
            role=merged.get("role", ""),
            model=merged["model"],
            base_url=merged["base_url"].rstrip("/"),
            api_key_env=merged["api_key_env"],
            temperature=float(merged.get("temperature", 0.2)),
            max_steps=int(merged.get("max_steps", 12)),
            skills=list(merged.get("skills", [])),
            enabled=bool(merged.get("enabled", True)),
        )

    if not agents:
        raise ValueError(f"No agents configured in {path}")
    return HarnessConfig(agents=agents)
