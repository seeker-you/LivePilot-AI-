import asyncio
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import httpx

from app.main import app
from app.platform import (
    DouyinAdapter,
    WxliveAdapter,
    build_adapter,
    wxlive_dispatch,
    wxlive_register,
    wxlive_unregister,
)


def _douyin_msg(msg_type, data, style="camel"):
    """Build a DouyinBarrageGrab broadcast in old (PascalCase) or new style."""
    if style == "pascal":
        return {"Type": msg_type, "Data": data}
    return {"type": msg_type, "data": data}


class DouyinParseTest(unittest.TestCase):
    def test_chat_camelcase(self):
        event = DouyinAdapter.parse_message(
            _douyin_msg(1, {"user": {"nickname": "小鹿", "id": 42}, "content": "多少钱"}),
            "s1",
        )
        self.assertEqual(event.type, "chat")
        self.assertEqual(event.payload["user"], "小鹿")
        self.assertEqual(event.payload["text"], "多少钱")
        self.assertEqual(event.payload["platform_user_id"], "42")
        self.assertEqual(event.session_id, "s1")

    def test_chat_pascalcase_with_json_string_data(self):
        data = '{"User": {"Nickname": "奶茶"}, "Content": "发货快吗"}'
        event = DouyinAdapter.parse_message(
            _douyin_msg(1, data, style="pascal"), "s1"
        )
        self.assertEqual(event.type, "chat")
        self.assertEqual(event.payload["user"], "奶茶")
        self.assertEqual(event.payload["text"], "发货快吗")

    def test_gift_value_is_count_times_repeat(self):
        event = DouyinAdapter.parse_message(
            _douyin_msg(
                5, {"user": {"nickname": "阿杰"}, "giftName": "玫瑰",
                    "giftCount": 2, "repeatCount": 5}
            ),
            "s1",
        )
        self.assertEqual(event.type, "gift")
        self.assertEqual(event.payload["gift"], "玫瑰")
        self.assertEqual(event.payload["value"], 10)

    def test_type_mapping(self):
        cases = {3: "user_enter", 4: "follow", 2: "like"}
        for code, expected in cases.items():
            event = DouyinAdapter.parse_message(
                _douyin_msg(code, {"user": {"nickname": "路人"}}), "s1"
            )
            self.assertEqual(event.type, expected, code)

    def test_unknown_type_and_invalid_json(self):
        self.assertIsNone(DouyinAdapter.parse_message(_douyin_msg(99, {}), "s1"))
        self.assertIsNone(DouyinAdapter.parse_message("not json", "s1"))

    def test_accepts_raw_string_broadcast(self):
        import json

        raw = json.dumps(_douyin_msg(1, {"user": {"nickname": "柚子"}, "content": "支持"}))
        event = DouyinAdapter.parse_message(raw, "s1")
        self.assertEqual(event.payload["text"], "支持")


class WxliveNormalizeTest(unittest.TestCase):
    def test_comment_maps_to_chat_with_message_id(self):
        event = WxliveAdapter.normalize_event(
            {"seq": "17", "decoded_type": "comment", "nickname": "栗子", "content": "有券吗"},
            "s1",
        )
        self.assertEqual(event.type, "chat")
        self.assertEqual(event.payload["message_id"], "wxlive:17")
        self.assertEqual(event.payload["text"], "有券吗")

    def test_type_mapping(self):
        cases = {
            "enter": "user_enter",
            "gift": "gift",
            "like": "like",
            "follow": "follow",
        }
        for decoded, expected in cases.items():
            event = WxliveAdapter.normalize_event(
                {"seq": "1", "decoded_type": decoded, "nickname": "晚风"}, "s1"
            )
            self.assertEqual(event.type, expected, decoded)

    def test_unknown_type_returns_none(self):
        self.assertIsNone(WxliveAdapter.normalize_event({"decoded_type": "stats"}, "s1"))


class WxliveDispatchTest(unittest.TestCase):
    def test_dispatch_without_session(self):
        self.assertEqual(wxlive_dispatch([{"decoded_type": "comment"}]), 0)

    def test_dispatch_delivers_with_session_id(self):
        queue: asyncio.Queue = asyncio.Queue()
        wxlive_register("s9", queue)
        try:
            delivered = wxlive_dispatch(
                [{"seq": "3", "decoded_type": "enter", "nickname": "豆豆"},
                 {"decoded_type": "ignored"}]
            )
            self.assertEqual(delivered, 1)
            event = queue.get_nowait()
            self.assertEqual(event.session_id, "s9")
            self.assertEqual(event.type, "user_enter")
        finally:
            wxlive_unregister("s9")

    def test_unregister_stops_delivery(self):
        queue: asyncio.Queue = asyncio.Queue()
        wxlive_register("s1", queue)
        wxlive_unregister("s1")
        self.assertEqual(wxlive_dispatch([{"decoded_type": "comment"}]), 0)


class BuildAdapterTest(unittest.TestCase):
    def test_factory(self):
        from app.platform import DouyinAdapter, DouyinDirectAdapter, MockAdapter

        self.assertIsInstance(build_adapter("mock", "s", 2.0), MockAdapter)
        self.assertIsInstance(
            build_adapter("douyin", "s", 2.0, room_id="856477039359"),
            DouyinDirectAdapter,
        )
        self.assertIsInstance(build_adapter("douyin_grab", "s", 2.0), DouyinAdapter)
        self.assertIsInstance(build_adapter("wxlive", "s", 2.0), WxliveAdapter)

    def test_unknown_platform(self):
        with self.assertRaises(ValueError):
            build_adapter("kuaishou", "s", 2.0)


class IngestWebhookTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        wxlive_register("s-webhook", self.queue)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        wxlive_unregister("s-webhook")
        await self.client.aclose()

    async def test_webhook_batch_reaches_queue(self):
        resp = await self.client.post(
            "/api/ingest/wxlive",
            json={"events": [
                {"seq": "5", "decoded_type": "comment", "nickname": "Momo", "content": "多少钱"},
                {"seq": "6", "decoded_type": "gift", "nickname": "Momo", "gift_name": "玫瑰"},
            ]},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["received"], 2)
        self.assertEqual(body["delivered"], 2)

        first = self.queue.get_nowait()
        second = self.queue.get_nowait()
        self.assertEqual(first.type, "chat")
        self.assertEqual(second.payload["gift"], "玫瑰")

    async def test_webhook_rejects_bad_payload(self):
        resp = await self.client.post("/api/ingest/wxlive", json={"foo": 1})
        self.assertEqual(resp.status_code, 422)

    async def test_webhook_requires_token_when_configured(self):
        fake = SimpleNamespace(wxlive_ingest_token="secret")
        with patch("app.routers.ingest.settings", fake):
            denied = await self.client.post("/api/ingest/wxlive", json={"events": []})
            self.assertEqual(denied.status_code, 401)

            allowed = await self.client.post(
                "/api/ingest/wxlive",
                json={"events": []},
                headers={"X-Ingest-Token": "secret"},
            )
            self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
