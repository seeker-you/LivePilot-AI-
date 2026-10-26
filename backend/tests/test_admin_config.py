import asyncio
import unittest

import httpx

from app import ratelimit, runtime_config
from app.controller import controller
from app.db import db
from app.main import app
from app.platform import DouyinAdapter
from app.routers.auth import seed_demo_users


async def login_as(client: httpx.AsyncClient, username: str, password: str) -> dict:
    seed_demo_users()
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class RuntimeConfigTest(unittest.TestCase):
    def setUp(self):
        runtime_config._cache.clear()
        db.execute("DELETE FROM settings WHERE key LIKE 'llm_%'")

    def tearDown(self):
        runtime_config._cache.clear()
        db.execute("DELETE FROM settings WHERE key LIKE 'llm_%'")

    def test_env_defaults_without_overrides(self):
        cfg = runtime_config.get_llm_config()
        self.assertEqual(cfg["llm_model"], runtime_config.settings.llm_model)
        self.assertIn("llm_base_url", cfg)

    def test_db_override_takes_priority(self):
        runtime_config.set_llm_config({"llm_model": "gpt-test"})
        cfg = runtime_config.get_llm_config()
        self.assertEqual(cfg["llm_model"], "gpt-test")

    def test_none_keeps_value_and_empty_string_clears(self):
        runtime_config.set_llm_config({"llm_model": "m1"})
        runtime_config.set_llm_config({"llm_model": None})  # untouched
        self.assertEqual(runtime_config.get_llm_config()["llm_model"], "m1")
        runtime_config.set_llm_config({"llm_model": ""})  # cleared -> env default
        self.assertEqual(
            runtime_config.get_llm_config()["llm_model"],
            runtime_config.settings.llm_model,
        )

    def test_mask_secret(self):
        self.assertEqual(runtime_config.mask_secret(""), "")
        self.assertEqual(runtime_config.mask_secret("short"), "****")
        self.assertEqual(
            runtime_config.mask_secret("sk-abcdef123456"), "sk-****3456"
        )


class AdminLlmConfigAPITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runtime_config._cache.clear()
        db.execute("DELETE FROM settings WHERE key LIKE 'llm_%'")
        ratelimit.api_limiter._hits.clear()
        ratelimit.login_limiter._hits.clear()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.admin = await login_as(self.client, "admin", "admin123")
        self.host = await login_as(self.client, "host", "host123")

    async def asyncTearDown(self):
        runtime_config._cache.clear()
        db.execute("DELETE FROM settings WHERE key LIKE 'llm_%'")
        ratelimit.api_limiter._hits.clear()
        ratelimit.login_limiter._hits.clear()
        await self.client.aclose()

    async def test_get_masks_api_key(self):
        runtime_config.set_llm_config({"llm_api_key": "sk-abcdef123456"})
        resp = await self.client.get(
            "/api/admin/llm-config", headers=self.admin
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["llm_api_key"], "sk-****3456")
        self.assertTrue(resp.json()["llm_configured"])

    async def test_put_updates_and_effective_config(self):
        resp = await self.client.put(
            "/api/admin/llm-config",
            headers=self.admin,
            json={"llm_model": "gpt-config-test"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(runtime_config.get_llm_config()["llm_model"], "gpt-config-test")

    async def test_masked_key_echoed_back_keeps_current(self):
        runtime_config.set_llm_config({"llm_api_key": "sk-abcdef123456"})
        resp = await self.client.put(
            "/api/admin/llm-config",
            headers=self.admin,
            json={"llm_api_key": "sk-****3456"},
        )
        self.assertEqual(resp.status_code, 200)
        # unchanged behind the mask
        self.assertEqual(
            runtime_config.get_llm_config()["llm_api_key"], "sk-abcdef123456"
        )

    async def test_non_admin_forbidden(self):
        resp = await self.client.get("/api/admin/llm-config", headers=self.host)
        self.assertEqual(resp.status_code, 403)
        resp = await self.client.put(
            "/api/admin/llm-config", headers=self.host, json={}
        )
        self.assertEqual(resp.status_code, 403)

    async def test_connection_check_requires_key(self):
        runtime_config.set_llm_config({"llm_api_key": ""})
        resp = await self.client.post(
            "/api/admin/llm-config/test", headers=self.admin
        )
        self.assertEqual(resp.status_code, 422)


class DouyinRoomFilterTest(unittest.TestCase):
    def _msg(self, room):
        return {"type": 1, "data": {"groupId": room, "content": "hi",
                                    "user": {"nickname": "a"}}}

    def test_matching_room_passes(self):
        event = DouyinAdapter.parse_message(self._msg("123"), "s", room_id="123")
        self.assertIsNotNone(event)

    def test_other_room_dropped(self):
        self.assertIsNone(DouyinAdapter.parse_message(self._msg("999"), "s", room_id="123"))

    def test_no_room_field_passes_when_filter_set(self):
        msg = {"type": 1, "data": {"content": "hi", "user": {"nickname": "a"}}}
        self.assertIsNotNone(DouyinAdapter.parse_message(msg, "s", room_id="123"))

    def test_no_filter_accepts_all_rooms(self):
        self.assertIsNotNone(DouyinAdapter.parse_message(self._msg("999"), "s"))


class RoomIdPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_persists_room_id(self):
        session = await controller.start(
            "闭环测试", 1.0, host_id=1, platform="douyin_grab", room_id="room-xyz"
        )
        try:
            self.assertEqual(session["room_id"], "room-xyz")
        finally:
            await controller.stop(session["id"])
        stored = db.query_one(
            "SELECT room_id FROM live_sessions WHERE id = ?", (session["id"],)
        )
        self.assertEqual(stored["room_id"], "room-xyz")


class PersonaTest(unittest.TestCase):
    def setUp(self):
        runtime_config._cache.clear()
        db.execute("DELETE FROM settings WHERE key LIKE 'persona_%'")

    def tearDown(self):
        runtime_config._cache.clear()
        db.execute("DELETE FROM settings WHERE key LIKE 'persona_%'")

    def test_defaults_and_override(self):
        cfg = runtime_config.get_persona_config()
        self.assertEqual(cfg["persona_name"], "小鹿")
        runtime_config.set_persona_config({"persona_name": "阿宝"})
        self.assertEqual(runtime_config.get_persona_config()["persona_name"], "阿宝")

    def test_build_persona_prompt_layout(self):
        runtime_config.set_persona_config(
            {
                "persona_name": "小美",
                "persona_forbidden_topics": "政治， 医疗诊断",
            }
        )
        prompt = runtime_config.build_persona_prompt()
        self.assertIn("你是小美，一位正在直播的主播。", prompt)
        self.assertIn("禁止谈论：政治、医疗诊断", prompt)

    def test_persona_injected_into_generation_prompts(self):
        from app.content import _system_prompt

        runtime_config.set_persona_config({"persona_name": "测试人设"})
        prompt = _system_prompt("title", "自然亲和")
        self.assertTrue(prompt.startswith("你是测试人设"))


class AdminUsersAPITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        seed_demo_users()
        ratelimit.api_limiter._hits.clear()
        ratelimit.login_limiter._hits.clear()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.admin = await login_as(self.client, "admin", "admin123")
        self.host = await login_as(self.client, "host", "host123")

    async def asyncTearDown(self):
        await self.client.aclose()
        ratelimit.api_limiter._hits.clear()
        ratelimit.login_limiter._hits.clear()

    async def test_list_create_and_duplicate(self):
        db.execute("DELETE FROM users WHERE username IN ('newhost', 'hacker')")
        resp = await self.client.get("/api/admin/users", headers=self.admin)
        self.assertEqual(resp.status_code, 200)
        usernames = [u["username"] for u in resp.json()]
        self.assertIn("admin", usernames)
        self.assertNotIn("password_hash", resp.json()[0])  # secrets never leak

        created = await self.client.post(
            "/api/admin/users",
            headers=self.admin,
            json={
                "username": "newhost",
                "display_name": "新主播",
                "password": "secret66",
                "role": "host",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["role"], "host")

        dup = await self.client.post(
            "/api/admin/users",
            headers=self.admin,
            json={"username": "newhost", "password": "secret66", "role": "host"},
        )
        self.assertEqual(dup.status_code, 409)

        bad_role = await self.client.post(
            "/api/admin/users",
            headers=self.admin,
            json={"username": "hacker", "password": "secret66", "role": "superadmin"},
        )
        self.assertEqual(bad_role.status_code, 422)

    async def test_non_admin_cannot_manage_users(self):
        resp = await self.client.get("/api/admin/users", headers=self.host)
        self.assertEqual(resp.status_code, 403)

    async def test_cannot_change_own_role_or_delete_self(self):
        admin_id = db.query_one("SELECT id FROM users WHERE username = 'admin'")["id"]
        resp = await self.client.patch(
            f"/api/admin/users/{admin_id}", headers=self.admin, json={"role": "operator"}
        )
        self.assertEqual(resp.status_code, 422)
        resp = await self.client.delete(f"/api/admin/users/{admin_id}", headers=self.admin)
        self.assertEqual(resp.status_code, 422)

    async def test_last_admin_protection(self):
        admin_id = db.query_one("SELECT id FROM users WHERE username = 'admin'")["id"]
        # ensure exactly one admin exists
        others = db.query("SELECT id FROM users WHERE role = 'admin' AND id != ?", (admin_id,))
        for row in others:
            db.execute("UPDATE users SET role = 'operator' WHERE id = ?", (row["id"],))
        resp = await self.client.patch(
            f"/api/admin/users/{admin_id}", headers=self.admin, json={"role": "host"}
        )
        self.assertEqual(resp.status_code, 422)
        resp = await self.client.delete(f"/api/admin/users/{admin_id}", headers=self.admin)
        self.assertEqual(resp.status_code, 422)

    async def test_role_change_and_password_reset_flow(self):
        created = await self.client.post(
            "/api/admin/users",
            headers=self.admin,
            json={"username": "promoted", "password": "secret66", "role": "operator"},
        )
        uid = created.json()["id"]

        promoted = await self.client.patch(
            f"/api/admin/users/{uid}", headers=self.admin, json={"role": "host"}
        )
        self.assertEqual(promoted.status_code, 200)
        self.assertEqual(promoted.json()["role"], "host")

        reset = await self.client.put(
            f"/api/admin/users/{uid}/password",
            headers=self.admin,
            json={"password": "newpass99"},
        )
        self.assertEqual(reset.status_code, 200)
        login = await self.client.post(
            "/api/auth/login", json={"username": "promoted", "password": "newpass99"}
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["user"]["role"], "host")

        removed = await self.client.delete(f"/api/admin/users/{uid}", headers=self.admin)
        self.assertEqual(removed.status_code, 200)

    async def test_persona_endpoints(self):
        resp = await self.client.get("/api/admin/persona", headers=self.admin)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("persona_name", resp.json())

        put = await self.client.put(
            "/api/admin/persona",
            headers=self.admin,
            json={"persona_name": "风格测试", "persona_style": "高冷专业"},
        )
        self.assertEqual(put.status_code, 200)
        self.assertEqual(put.json()["persona_name"], "风格测试")
        self.assertEqual(put.json()["persona_style"], "高冷专业")

        forbidden = await self.client.get("/api/admin/persona", headers=self.host)
        self.assertEqual(forbidden.status_code, 403)


if __name__ == "__main__":
    unittest.main()
