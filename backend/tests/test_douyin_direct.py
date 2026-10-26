"""Unit tests for the Douyin direct-connect adapter (pure, no network)."""

import gzip
import unittest
from unittest.mock import patch

from app.douyin_direct import (
    DouyinDirectAdapter,
    RoomUnresolvableError,
    _resolve_short_link,
    normalize_douyin_room,
    parse_douyin_room_input,
    pb_bytes,
    pb_int,
    pb_varint,
)
from app.models import EventType


# ── protobuf builders mirroring the observed wire format ──────────────────


def common(method="WebcastChatMessage", msg_id=123):
    return pb_bytes(1, method.encode()) + pb_int(2, msg_id)


def user(uid=3308898206622569, nick="小鹿"):
    return pb_int(1, uid) + pb_bytes(3, nick.encode())


def chat_payload(text="多少钱", msg_id=123):
    return (
        pb_bytes(1, common("WebcastChatMessage", msg_id))
        + pb_bytes(2, user())
        + pb_bytes(3, text.encode())
    )


def wrap_response(messages, cursor="c-1", need_ack=True):
    """Response frame: messages=1 (repeated), cursor=2, need_ack=10."""
    resp = b"".join(pb_bytes(1, m) for m in messages)
    resp += pb_bytes(2, cursor.encode())
    if need_ack:
        resp += pb_int(10, 1)
    gz = gzip.compress(resp)
    # PushFrame: logId=2, method=4, payload(gzip)=8
    return pb_int(2, 77) + pb_bytes(4, b"push") + pb_bytes(8, gz)


def message(method, payload):
    """Message wrapper: method=1 (str), payload=2 (bytes)."""
    return pb_bytes(1, method.encode()) + pb_bytes(2, payload)


class PbCodecTest(unittest.TestCase):
    def test_varint_roundtrip(self):
        for v in (0, 1, 127, 128, 300, 2**31, 7699863714162742287):
            encoded = pb_varint(v)
            # decode back via pb_int field wrapping
            field = pb_int(1, v)
            self.assertEqual(field[0], (1 << 3) | 0)
            self.assertEqual(encoded, field[1:])

    def test_pb_bytes_roundtrip(self):
        from app.douyin_direct import pb_decode

        raw = pb_bytes(3, "你好".encode()) + pb_int(4, 7)
        fields = pb_decode(raw)
        self.assertEqual(fields[3][0], "你好".encode())
        self.assertEqual(fields[4][0], 7)


class ParseFrameTest(unittest.TestCase):
    def test_chat_event_and_ack(self):
        frame = wrap_response([message("WebcastChatMessage", chat_payload())])
        events, ack = DouyinDirectAdapter.parse_frame(frame, "s1")
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.type, EventType.chat.value)
        self.assertEqual(ev.payload["user"], "小鹿")
        self.assertEqual(ev.payload["text"], "多少钱")
        self.assertEqual(ev.payload["message_id"], "dy:123")
        self.assertIsNotNone(ack)
        self.assertIn(b"ack", ack)

    def test_no_ack_when_not_required(self):
        frame = wrap_response(
            [message("WebcastChatMessage", chat_payload())], need_ack=False
        )
        events, ack = DouyinDirectAdapter.parse_frame(frame, "s1")
        self.assertEqual(len(events), 1)
        self.assertIsNone(ack)

    def test_garbage_frame_is_ignored(self):
        events, ack = DouyinDirectAdapter.parse_frame(b"\xff\xff\xff", "s1")
        self.assertEqual(events, [])
        self.assertIsNone(ack)


class ParseMessageTest(unittest.TestCase):
    def test_like(self):
        payload = (
            pb_bytes(1, common("WebcastLikeMessage", 9))
            + pb_int(2, 20)  # count
            + pb_bytes(5, user(nick="奶茶"))  # user at field 5 (proto)
        )
        ev = DouyinDirectAdapter.parse_message("WebcastLikeMessage", payload, "s1")
        self.assertEqual(ev.type, "like")
        self.assertEqual(ev.payload["count"], 20)
        self.assertEqual(ev.payload["user"], "奶茶")
        self.assertEqual(ev.payload["message_id"], "dy:9")

    def test_like_user_found_via_bfs(self):
        # user buried in displayText-like structure: 8 → 4 → 2
        payload = pb_bytes(1, common("WebcastLikeMessage", 9)) + pb_bytes(
            8, pb_bytes(4, pb_bytes(2, user(nick="晚风")))
        )
        ev = DouyinDirectAdapter.parse_message("WebcastLikeMessage", payload, "s1")
        self.assertEqual(ev.payload["user"], "晚风")
        self.assertEqual(ev.payload["count"], 1)

    def test_member_message_maps_to_user_enter(self):
        payload = pb_bytes(1, common("WebcastMemberMessage", 10)) + pb_bytes(
            3, user(nick="路人甲")
        )
        ev = DouyinDirectAdapter.parse_message("WebcastMemberMessage", payload, "s1")
        self.assertEqual(ev.type, "user_enter")
        self.assertEqual(ev.payload["user"], "路人甲")

    def test_social_message_maps_to_follow(self):
        payload = pb_bytes(1, common("WebcastSocialMessage", 11)) + pb_bytes(
            2, user(nick="种草选手")
        )
        ev = DouyinDirectAdapter.parse_message("WebcastSocialMessage", payload, "s1")
        self.assertEqual(ev.type, "follow")
        self.assertEqual(ev.payload["user"], "种草选手")

    def test_light_gift(self):
        # Observed shape: {3: repeat, 5: userId, 7: {1: giftId}, 8: {1: value}}
        payload = (
            pb_bytes(1, common("WebcastLightGiftMessage", 12))
            + pb_int(3, 2)  # repeat count
            + pb_int(5, 93119166296)  # userId
            + pb_bytes(7, pb_int(1, 3992))  # giftId
            + pb_bytes(8, pb_int(1, 2000))  # value (milli-douyin)
        )
        ev = DouyinDirectAdapter.parse_message("WebcastLightGiftMessage", payload, "s1")
        self.assertEqual(ev.type, "gift")
        self.assertEqual(ev.payload["gift"], "轻量礼物")
        self.assertEqual(ev.payload["count"], 2)
        self.assertEqual(ev.payload["value"], 2)  # 2000 / 1000
        self.assertEqual(ev.payload["user"], "未知")

    def test_gift_message_heuristics(self):
        # GiftMessage: repeatCount=5, user=7, GiftStruct={1: id, 2: name, 9: diamonds}
        gift_struct = pb_int(1, 7720) + pb_bytes(2, "火箭".encode()) + pb_int(9, 100)
        payload = (
            pb_bytes(1, common("WebcastGiftMessage", 13))
            + pb_int(5, 3)
            + pb_bytes(7, user(nick="榜一大哥"))
            + pb_bytes(16, gift_struct)
        )
        ev = DouyinDirectAdapter.parse_message("WebcastGiftMessage", payload, "s1")
        self.assertEqual(ev.type, "gift")
        self.assertEqual(ev.payload["gift"], "火箭")
        self.assertEqual(ev.payload["count"], 3)
        self.assertEqual(ev.payload["value"], 100)
        self.assertEqual(ev.payload["user"], "榜一大哥")

    def test_emoji_chat_fallback_text(self):
        payload = pb_bytes(1, common("WebcastEmojiChatMessage", 14)) + pb_bytes(
            2, user(nick="Sakura")
        )
        ev = DouyinDirectAdapter.parse_message("WebcastEmojiChatMessage", payload, "s1")
        self.assertEqual(ev.type, "chat")
        self.assertEqual(ev.payload["text"], "[表情]")

    def test_unknown_method_returns_none(self):
        payload = pb_bytes(1, common("WebcastRoomDataSyncMessage", 15))
        self.assertIsNone(
            DouyinDirectAdapter.parse_message("WebcastRoomDataSyncMessage", payload, "s1")
        )


class RoomInputTest(unittest.TestCase):
    def test_plain_rid(self):
        self.assertEqual(normalize_douyin_room("856477039359"), "856477039359")

    def test_alphanumeric_rid(self):
        self.assertEqual(normalize_douyin_room("KXyy12345678"), "KXyy12345678")

    def test_full_url(self):
        self.assertEqual(
            normalize_douyin_room("https://live.douyin.com/856477039359?from=share"),
            "856477039359",
        )

    def test_invalid(self):
        for bad in ("", "  ", "room-xyz", "ab", "x" * 30):
            self.assertEqual(normalize_douyin_room(bad), "", bad)

    def test_adapter_requires_room(self):
        with self.assertRaises(ValueError):
            DouyinDirectAdapter("s1", room_id="")


class ShortLinkTest(unittest.TestCase):
    """v.douyin.com 分享短链/整段分享文案的解析。"""

    def test_short_link_from_full_share_text(self):
        text = (
            "#在抖音，记录美好生活#【NICEID换季工厂店】正在直播，来和我一起支持Ta吧。"
            "复制下方链接，打开【抖音】，直接观看直播！ "
            "`https://v.douyin.com/boEdIkgzBaQ/` 1@2.com :1pm"
        )
        self.assertEqual(parse_douyin_room_input(text), ("short", "boEdIkgzBaQ"))

    def test_short_link_prefers_live_url_when_both_present(self):
        text = "https://v.douyin.com/boEdIkgzBaQ/ 或 https://live.douyin.com/856477039359"
        self.assertEqual(parse_douyin_room_input(text), ("rid", "856477039359"))

    def test_share_link_is_not_a_web_rid(self):
        self.assertEqual(normalize_douyin_room("https://v.douyin.com/boEdIkgzBaQ/"), "")


class _FakeResponse:
    def __init__(self, text="", url="https://v.douyin.com/boEdIkgzBaQ/"):
        self.text = text
        self.url = url


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.requested = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        self.requested = url
        return self._response


class ResolveShortLinkTest(unittest.IsolatedAsyncioTestCase):
    async def _resolve(self, response):
        with patch(
            "app.douyin_direct.httpx.AsyncClient", lambda **kw: _FakeClient(response)
        ):
            return await _resolve_short_link("ttwid", "boEdIkgzBaQ")

    async def test_extracts_web_rid_from_escaped_landing_page(self):
        # 真实落地页里字段是 JSON 双重转义：\"webRid\":\"310852424386\"
        html = r'{\"remarkName\":\"\",\"webRid\":\"310852424386\",\"mysteryMan\":1}'
        self.assertEqual(await self._resolve(_FakeResponse(html)), "310852424386")

    async def test_falls_back_to_redirected_live_url(self):
        response = _FakeResponse("", url="https://live.douyin.com/856477039359?from=share")
        self.assertEqual(await self._resolve(response), "856477039359")

    async def test_unresolvable_raises(self):
        with self.assertRaises(RoomUnresolvableError):
            await self._resolve(_FakeResponse('{"webRid":""}'))


if __name__ == "__main__":
    unittest.main()
