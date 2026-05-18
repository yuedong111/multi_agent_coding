from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Protocol

from .config import AgentConfig


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        ...


def create_llm_client(config: AgentConfig) -> LLMClient:
    if config.provider == "openai-compatible":
        return OpenAICompatibleClient(config)
    if config.provider == "anthropic":
        return AnthropicClient(config)
    raise LLMError(
        f"Unsupported LLM provider {config.provider!r} for agent {config.name}"
    )


def _api_key(config: AgentConfig) -> str:
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise LLMError(
            f"Missing API key env {config.api_key_env} for agent {config.name}"
        )
    return api_key


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc


class OpenAICompatibleClient:
    def __init__(self, config: AgentConfig):
        self.config = config

    def complete(self, messages: list[dict[str, str]]) -> str:
        # The client only assumes the OpenAI-compatible chat completions shape;
        # provider-specific behavior is kept in the external config.
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens

        body = _post_json(
            f"{self.config.base_url}/chat/completions",
            payload=payload,
            headers={"Authorization": f"Bearer {_api_key(self.config)}"},
        )

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response: {body}") from exc


class AnthropicClient:
    def __init__(self, config: AgentConfig):
        self.config = config

    def complete(self, messages: list[dict[str, str]]) -> str:
        system, conversation = self._split_system_messages(messages)
        payload = {
            "model": self.config.model,
            "messages": conversation,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens or 4096,
        }
        if system:
            payload["system"] = system

        body = _post_json(
            f"{self.config.base_url}/messages",
            payload=payload,
            headers={
                "x-api-key": _api_key(self.config),
                "anthropic-version": "2023-06-01",
            },
        )

        try:
            text_parts = [
                part["text"]
                for part in body["content"]
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            if not text_parts:
                raise KeyError("content text")
            return "".join(text_parts)
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response: {body}") from exc

    def _split_system_messages(
        self, messages: list[dict[str, str]]
    ) -> tuple[str, list[dict[str, str]]]:
        system_parts: list[str] = []
        conversation: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role in {"user", "assistant"}:
                conversation.append({"role": role, "content": content})
            else:
                raise LLMError(f"Unsupported message role {role!r} for Anthropic")
        return "\n\n".join(system_parts), conversation
