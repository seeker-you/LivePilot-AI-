import unittest

import httpx

from app.controller import LiveController
from app.db import db
from app.main import app
from app.models import LiveEvent
from app.routers.auth import seed_demo_users
from app import ratelimit


def _event(message_id: str | None) -> LiveEvent:
    payload = {"user": "小鹿", "text": "多少钱？"}
    if message_id:
        payload["message_id"] = message_id
    return LiveEvent(id=1, session_id="s1", type="chat", payload=payload, ts=0.0)


class DedupTest(unittest.TestCase):
    def setUp(self):
        self.controller = LiveController()

    def test_same_message_id_is_duplicate(self):
        first = self.controller._is_duplicate("s1", _event("m1"))
        second = self.controller._is_duplicate("s1", _event("m1"))
        self.assertFalse(first)
        self.assertTrue(second)

    def test_different_message_ids_pass(self):
        self.assertFalse(self.controller._is_duplicate("s1", _event("m1")))
        self.assertFalse(self.controller._is_duplicate("s1", _event("m2")))

    def test_events_without_message_id_pass_through(self):
        self.assertFalse(self.controller._is_duplicate("s1", _event(None)))
        self.assertFalse(self.controller._is_duplicate("s1", _event(None)))

    def test_sessions_are_isolated(self):
        self.controller._is_duplicate("s1", _event("m1"))
        self.assertFalse(self.controller._is_duplicate("s2", _event("m1")))


class IdempotencyAPITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        seed_demo_users()
        db.execute("DELETE FROM idempotency")
        ratelimit.api_limiter._hits.clear()
        ratelimit.login_limiter._hits.clear()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        login = await self.client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        self.headers = {
            "Authorization": f"Bearer {login.json()['access_token']}"
        }

    async def asyncTearDown(self):
        await self.client.aclose()
        ratelimit.api_limiter._hits.clear()
        ratelimit.login_limiter._hits.clear()

    async def _create(self, headers: dict) -> dict:
        resp = await self.client.post(
            "/api/knowledge",
            json={"title": "幂等测试", "product": "面霜", "content": "内容"},
            headers={**self.headers, **headers},
        )
        return resp

    async def test_duplicate_key_replays_same_response(self):
        before = len(db.query("SELECT id FROM knowledge"))
        first = await self._create({"Idempotency-Key": "k1"})
        second = await self._create({"Idempotency-Key": "k1"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        after = len(db.query("SELECT id FROM knowledge"))
        self.assertEqual(after - before, 1)

    async def test_same_key_with_different_body_rejected(self):
        await self._create({"Idempotency-Key": "k2"})
        resp = await self.client.post(
            "/api/knowledge",
            json={"title": "另一个", "product": "精华", "content": "内容"},
            headers={**self.headers, "Idempotency-Key": "k2"},
        )
        self.assertEqual(resp.status_code, 422)

    async def test_without_key_executes_each_time(self):
        before = len(db.query("SELECT id FROM knowledge"))
        await self._create({})
        await self._create({})
        after = len(db.query("SELECT id FROM knowledge"))
        self.assertEqual(after - before, 2)

    async def test_error_response_is_replayed_verbatim(self):
        headers = {**self.headers, "Idempotency-Key": "k3"}
        first = await self.client.post("/api/live/none/stop", headers=headers)
        second = await self.client.post("/api/live/none/stop", headers=headers)
        self.assertEqual(first.status_code, 404)
        self.assertEqual(second.status_code, 404)
        self.assertEqual(first.json(), second.json())


if __name__ == "__main__":
    unittest.main()
