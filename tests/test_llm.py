import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_agent.config import AgentConfig, load_config
from harness_agent.llm import AnthropicClient, OpenAICompatibleClient, create_llm_client


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")


def config(**overrides):
    values = {
        "name": "coder",
        "role": "Write code.",
        "model": "test-model",
        "base_url": "https://example.test/v1",
        "api_key_env": "TEST_API_KEY",
        "temperature": 0.0,
        "max_steps": 1,
        "skills": [],
    }
    values.update(overrides)
    return AgentConfig(**values)


class LLMProviderTest(unittest.TestCase):
    def test_factory_creates_anthropic_client(self):
        client = create_llm_client(config(provider="anthropic"))

        self.assertIsInstance(client, AnthropicClient)

    def test_openai_compatible_client_posts_chat_completion(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse({"choices": [{"message": {"content": "done"}}]})

        client = OpenAICompatibleClient(config(max_tokens=123))

        with patch.dict("os.environ", {"TEST_API_KEY": "secret"}), patch(
            "urllib.request.urlopen", fake_urlopen
        ):
            result = client.complete([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "done")
        self.assertEqual(captured["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(captured["payload"]["max_tokens"], 123)
        self.assertEqual(captured["timeout"], 120)

    def test_anthropic_client_posts_messages_payload(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"content": [{"type": "text", "text": '{"tool":"finish"}'}]})

        client = AnthropicClient(
            config(provider="anthropic", model="claude-test", max_tokens=2048)
        )

        with patch.dict("os.environ", {"TEST_API_KEY": "secret"}), patch(
            "urllib.request.urlopen", fake_urlopen
        ):
            result = client.complete(
                [
                    {"role": "system", "content": "system rules"},
                    {"role": "user", "content": "do it"},
                    {"role": "assistant", "content": "thinking"},
                ]
            )

        self.assertEqual(result, '{"tool":"finish"}')
        self.assertEqual(captured["url"], "https://example.test/v1/messages")
        self.assertEqual(captured["headers"]["X-api-key"], "secret")
        self.assertEqual(captured["headers"]["Anthropic-version"], "2023-06-01")
        self.assertEqual(captured["payload"]["model"], "claude-test")
        self.assertEqual(captured["payload"]["max_tokens"], 2048)
        self.assertEqual(captured["payload"]["system"], "system rules")
        self.assertEqual(
            captured["payload"]["messages"],
            [
                {"role": "user", "content": "do it"},
                {"role": "assistant", "content": "thinking"},
            ],
        )

    def test_load_config_reads_provider_and_max_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.json"
            path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "provider": "openai-compatible",
                            "base_url": "https://example.test/v1",
                            "api_key_env": "TEST_API_KEY",
                            "model": "default-model",
                        },
                        "agents": {
                            "coder": {
                                "provider": "anthropic",
                                "model": "claude-test",
                                "max_tokens": 2048,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_config(path)

        self.assertEqual(loaded.agents["coder"].provider, "anthropic")
        self.assertEqual(loaded.agents["coder"].max_tokens, 2048)


if __name__ == "__main__":
    unittest.main()
