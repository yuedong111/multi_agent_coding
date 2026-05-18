from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .config import AgentConfig


class LLMError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, config: AgentConfig):
        self.config = config

    def complete(self, messages: list[dict[str, str]]) -> str:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise LLMError(
                f"Missing API key env {self.config.api_key_env} for agent {self.config.name}"
            )

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response: {body}") from exc
