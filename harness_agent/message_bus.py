from __future__ import annotations

import json
from pathlib import Path
from time import time


class MessageBus:
    def __init__(self, root: Path):
        self.dir = root / ".team" / "inbox"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events = root / ".team" / "events.jsonl"
        self.events.parent.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, to: str, content: str) -> dict:
        msg = {
            "type": "message",
            "from": sender,
            "to": to,
            "content": content,
            "timestamp": time(),
        }
        path = self.dir / f"{to}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.log({"event": "send", **msg})
        return msg

    def read_inbox(self, name: str) -> list[dict]:
        path = self.dir / f"{name}.jsonl"
        if not path.exists():
            return []
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        # Reading drains the inbox so each message is consumed once by the
        # target agent loop.
        path.write_text("", encoding="utf-8")
        messages = [json.loads(line) for line in lines]
        if messages:
            self.log({"event": "drain", "agent": name, "count": len(messages), "timestamp": time()})
        return messages

    def log(self, event: dict) -> None:
        with self.events.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
