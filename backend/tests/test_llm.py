import unittest
from unittest.mock import patch

import httpx

from app import llm, runtime_config
from app.llm import heuristic_classify
from app.models import Intent, Sentiment


class HeuristicClassifyTest(unittest.TestCase):
    def test_question(self):
        result = heuristic_classify("这个面霜油皮可以用吗？")
        self.assertEqual(result.intent, Intent.question)

    def test_spam(self):
        result = heuristic_classify("加微信聊聊")
        self.assertEqual(result.intent, Intent.spam)

    def test_sensitive_complaint(self):
        result = heuristic_classify("这是假货，我要投诉")
        self.assertEqual(result.sentiment, Sentiment.negative)
        self.assertTrue(result.is_sensitive)

    def test_price_inquiry(self):
        result = heuristic_classify("这个多少钱")
        self.assertEqual(result.intent, Intent.price_inquiry)


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://test/chat/completions")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


class IsRetryableTest(unittest.TestCase):
    def test_timeout_and_transport_are_retryable(self):
        self.assertTrue(llm._is_retryable(httpx.ConnectTimeout("t")))
        self.assertTrue(llm._is_retryable(httpx.ConnectError("t")))

    def test_5xx_and_429_are_retryable(self):
        for code in (500, 502, 503, 504, 429):
            self.assertTrue(llm._is_retryable(_status_error(code)), code)

    def test_4xx_is_not_retryable(self):
        for code in (400, 401, 403, 404, 422):
            self.assertFalse(llm._is_retryable(_status_error(code)), code)


class FakeClient:
    """Yields queued outcomes (responses or exceptions) per POST call."""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, path, json=None):
        self.calls += 1
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok_response() -> httpx.Response:
    request = httpx.Request("POST", "http://test/chat/completions")
    return httpx.Response(200, json={"ok": True}, request=request)


class PostRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_error_then_succeeds(self):
        fake = FakeClient([_status_error(503), _status_error(503), _ok_response()])
        sleeps: list[float] = []

        async def record_sleep(seconds):
            sleeps.append(seconds)

        original_client, original_sleep = llm._client, llm._sleep
        llm._client, llm._sleep = lambda *a, **kw: fake, record_sleep
        try:
            resp = await llm._post("/chat/completions", {})
        finally:
            llm._client, llm._sleep = original_client, original_sleep
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(fake.calls, 3)
        self.assertEqual(sleeps, [0.5, 1.0])  # exponential backoff

    async def test_does_not_retry_client_error(self):
        fake = FakeClient([_status_error(400)])

        async def no_sleep(_):
            raise AssertionError("should not sleep on 4xx")

        original_client, original_sleep = llm._client, llm._sleep
        llm._client, llm._sleep = lambda *a, **kw: fake, no_sleep
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                await llm._post("/chat/completions", {})
            self.assertEqual(fake.calls, 1)
        finally:
            llm._client, llm._sleep = original_client, original_sleep

    async def test_gives_up_after_max_attempts(self):
        fake = FakeClient([_status_error(503)] * llm.MAX_ATTEMPTS)

        async def record_sleep(_):
            return None

        original_client, original_sleep = llm._client, llm._sleep
        llm._client, llm._sleep = lambda *a, **kw: fake, record_sleep
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                await llm._post("/chat/completions", {})
            self.assertEqual(fake.calls, llm.MAX_ATTEMPTS)
        finally:
            llm._client, llm._sleep = original_client, original_sleep


class LocalEndpointTest(unittest.IsolatedAsyncioTestCase):
    """本地 Ollama 等无需 Key 的服务视为已配置。"""

    def test_local_url_is_configured_without_key(self):
        self.assertTrue(
            llm.is_configured(
                {"llm_api_key": "", "llm_base_url": "http://localhost:11434/v1"}
            )
        )
        self.assertTrue(
            llm.is_configured(
                {"llm_api_key": "", "llm_base_url": "http://127.0.0.1:11434/v1"}
            )
        )

    def test_remote_url_needs_key(self):
        self.assertFalse(
            llm.is_configured(
                {"llm_api_key": "", "llm_base_url": "https://api.openai.com/v1"}
            )
        )
        self.assertTrue(
            llm.is_configured(
                {"llm_api_key": "sk-x", "llm_base_url": "https://api.openai.com/v1"}
            )
        )

    async def test_client_omits_auth_header_without_key(self):
        client = llm._client("http://localhost:11434/v1", "")
        try:
            self.assertNotIn("Authorization", client.headers)
        finally:
            await client.aclose()

    async def test_client_sends_bearer_when_key_present(self):
        client = llm._client("https://api.openai.com/v1", "sk-abc")
        try:
            self.assertEqual(client.headers["Authorization"], "Bearer sk-abc")
        finally:
            await client.aclose()


class EmbeddingConfigTest(unittest.TestCase):
    """对话与向量可分别配置不同接口，留空自动回退。"""

    def _cfg(self, **overrides):
        base = {
            "llm_api_key": "sk-chat",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_model": "gpt-4o-mini",
            "llm_embedding_model": "text-embedding-3-small",
            "llm_embedding_base_url": "",
            "llm_embedding_api_key": "",
        }
        base.update(overrides)
        return base

    def test_falls_back_to_chat_endpoint(self):
        with patch.object(runtime_config, "get_llm_config", return_value=self._cfg()):
            emb = runtime_config.get_embedding_config()
        self.assertEqual(emb["base_url"], "https://api.openai.com/v1")
        self.assertEqual(emb["api_key"], "sk-chat")

    def test_separate_endpoint_and_key(self):
        cfg = self._cfg(
            llm_embedding_base_url="http://localhost:11434/v1",
            llm_embedding_api_key="ollama",
        )
        with patch.object(runtime_config, "get_llm_config", return_value=cfg):
            emb = runtime_config.get_embedding_config()
        self.assertEqual(emb["base_url"], "http://localhost:11434/v1")
        self.assertEqual(emb["api_key"], "ollama")
        self.assertEqual(emb["model"], "text-embedding-3-small")


if __name__ == "__main__":
    unittest.main()

