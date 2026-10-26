"""抖音直播弹幕直连适配器：无需任何外部抓流工具。

Anonymous pipeline (verified against live traffic):

1. ``ttwid`` cookie      ← GET https://live.douyin.com/
2. ``webid`` (device id) ← POST https://mcs.zijieapi.com/webid
3. web_rid → real room_id scraped from the SSR room page
4. X-Bogus signature     ← ``node scripts/sign_runner.js`` (wraps sign.js)
5. wss push stream at ``wss://webcast5-ws-web-hl.douyin.com`` with a
   hand-rolled protobuf decoder (PushFrame → Response → Message),
   10s app-level ping and cursor acks.

Vendor msgId is normalized into ``payload["message_id"]`` (``dy:<id>``) so
the controller-level dedup window absorbs reconnect replays.

Field maps below were confirmed from a live capture of a top streamer room
(see scripts/douyin_probe.py, the diagnostic that produced them).
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import random
import re
import shutil
import string
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import BASE_DIR
from .db import now
from .models import EventType, LiveEvent

logger = logging.getLogger("livepilot.platform")

try:  # websockets >= 13 ships the new asyncio client under this path
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - older bundled versions
    from websockets import connect as ws_connect  # type: ignore

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SCRIPTS = BASE_DIR / "scripts"
_WS_ENDPOINT = "wss://webcast5-ws-web-hl.douyin.com/webcast/im/push/v2/"
# gzip only: the brotli decoder in this env is flaky with Douyin's responses.
_HTTP_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://live.douyin.com/",
    "Accept-Encoding": "gzip, deflate",
}
# X-Bogus signing only covers this fixed parameter subset, joined in order.
_SIGN_PARAMS = (
    "live_id,aid,version_code,webcast_sdk_version,"
    "room_id,sub_room_id,sub_channel_id,did_rule,"
    "user_unique_id,device_platform,device_type,ac,"
    "identity"
).split(",")


class RoomUnresolvableError(RuntimeError):
    """The configured web_rid does not point at a resolvable live room."""


# ── generic protobuf wire-format codec ────────────────────────────────────


def read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("varint overrun")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def pb_decode(buf: bytes) -> dict[int, list]:
    """Decode protobuf wire format into {field_number: [values]}."""
    out: dict[int, list] = {}
    i, n = 0, len(buf)
    while i < n:
        tag, i = read_varint(buf, i)
        field, wire = tag >> 3, tag & 7
        if field == 0:
            raise ValueError("field 0")
        if wire == 0:
            v, i = read_varint(buf, i)
        elif wire == 1:
            v, i = buf[i : i + 8], i + 8
        elif wire == 2:
            ln, i = read_varint(buf, i)
            if i + ln > n:
                raise ValueError("length overflow")
            v, i = buf[i : i + ln], i + ln
        elif wire == 5:
            v, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"wire type {wire}")
        out.setdefault(field, []).append(v)
    return out


def pb_varint(value: int) -> bytes:
    out = b""
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def pb_bytes(field: int, value: bytes) -> bytes:
    return pb_varint((field << 3) | 2) + pb_varint(len(value)) + value


def pb_int(field: int, value: int) -> bytes:
    return pb_varint((field << 3) | 0) + pb_varint(value)


def _first(fields: dict[int, list], n: int, cls):
    for v in fields.get(n, []):
        if isinstance(v, cls):
            return v
    return None


def _f_int(fields: dict[int, list] | None, n: int) -> int:
    if not fields:
        return 0
    return _first(fields, n, int) or 0


def _f_str(fields: dict[int, list] | None, n: int) -> str:
    if not fields:
        return ""
    v = _first(fields, n, bytes)
    return v.decode("utf-8", errors="replace") if v is not None else ""


def _f_msg(fields: dict[int, list], n: int) -> dict[int, list] | None:
    v = _first(fields, n, bytes)
    if v is None:
        return None
    try:
        return pb_decode(v)
    except ValueError:
        return None


# ── HTTP bootstrap (httpx passes Douyin's TLS edge checks) ────────────────


async def _get_ttwid() -> str:
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        r = await client.get("https://live.douyin.com/", headers=_HTTP_HEADERS)
    m = re.search(r"ttwid=([^;]+)", r.headers.get("set-cookie", ""))
    if not m:
        raise RuntimeError("failed to obtain ttwid cookie from live.douyin.com")
    return m.group(1)


async def _get_webid(ttwid: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://mcs.zijieapi.com/webid",
            json={
                "app_id": 6383,
                "url": "https://live.douyin.com/",
                "user_agent": UA,
                "referer": "https://live.douyin.com/",
                "user_unique_id": "",
            },
            headers={"User-Agent": UA, "Cookie": f"ttwid={ttwid}"},
        )
    try:
        body = r.json()
    except ValueError as exc:
        raise RuntimeError(f"webid registration failed: HTTP {r.status_code}") from exc
    webid = (body.get("data") or {}).get("web_id") or body.get("web_id")
    if not webid:
        raise RuntimeError(f"webid registration failed: {str(body)[:200]}")
    return str(webid)


async def _resolve_room(ttwid: str, web_rid: str) -> str:
    """Scrape the SSR room page to resolve web_rid → real room_id."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        r = await client.get(
            f"https://live.douyin.com/{web_rid}",
            headers={**_HTTP_HEADERS, "Cookie": f"ttwid={ttwid}"},
        )
    m = re.search(
        r'roomId\\":\\"(\d+)\\"', r.text
    ) or re.search(r'"roomId"\s*:\s*"?(\d{6,})"?', r.text)
    if not m:
        raise RoomUnresolvableError(web_rid)
    return m.group(1)


async def _resolve_short_link(ttwid: str, code: str) -> str:
    """v.douyin.com 分享短链 → web_rid：跟随跳转后从落地页提取 webRid。"""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(
            f"https://v.douyin.com/{code}/",
            headers={**_HTTP_HEADERS, "Cookie": f"ttwid={ttwid}"},
        )
    m = re.search(r"live\.douyin\.com/([A-Za-z0-9]{6,24})", str(r.url))
    if m:
        return m.group(1)
    # 落地页里的 webRid 可能被 JSON 双重转义：\"webRid\":\"310852424386\"
    text = r.text.replace('\\"', '"')
    m = re.search(r'"webRid"\s*:\s*"([A-Za-z0-9]{6,24})"', text)
    if m:
        return m.group(1)
    raise RoomUnresolvableError(code)


def _gen_mstoken(length: int = 107) -> str:
    base = string.ascii_letters + string.digits + "=_"
    return "".join(random.choice(base) for _ in range(length))


def _ws_url(room_id: str, user_unique_id: str) -> str:
    """Unsigned push URL; the X-Bogus signature is appended separately."""
    return (
        "wss://webcast5-ws-web-hl.douyin.com/webcast/im/push/v2/"
        "?app_name=douyin_web&version_code=180800&webcast_sdk_version=1.0.14-beta.0"
        "&update_version_code=1.0.14-beta.0&compress=gzip&device_platform=web"
        "&cookie_enabled=true&screen_width=1536&screen_height=864"
        "&browser_language=zh-CN&browser_platform=Win32&browser_name=Mozilla"
        "&browser_version=5.0%20(Windows%20NT%2010.0%3B%20Win64%3B%20x64)%20AppleWebKit%2F537.36%20(KHTML%2C%20like%20Gecko)%20Chrome%2F120.0.0.0%20Safari%2F537.36"
        f"&room_id={room_id}&sub_room_id=&sub_channel_id=&did_rule=3"
        f"&user_unique_id={user_unique_id}&device_platform=web&device_type=&ac=&identity=audience"
        "&need_persist_msg_count=15&insert_task_id=&live_reason="
        "&host=https://live.douyin.com&aid=6383&live_id=1&endpoint=live_pc"
        "&support_wrds=1&im_path=/webcast/im/fetch/"
    )


async def _sign(url: str) -> str:
    """Compute the X-Bogus signature via node scripts/sign_runner.js."""
    maps = {
        p.split("=")[0]: p.split("=")[-1]
        for p in urlparse(url).query.split("&")
    }
    tpl = [f"{k}={maps.get(k, '')}" for k in _SIGN_PARAMS]
    md5 = hashlib.md5(",".join(tpl).encode()).hexdigest()

    def run() -> str:
        r = subprocess.run(
            ["node", "sign_runner.js", json.dumps({"X-MS-STUB": md5})],
            capture_output=True, text=True, cwd=SCRIPTS, timeout=60,
        )
        if r.returncode != 0:
            raise RuntimeError(f"X-Bogus signing failed: {r.stderr[:200]}")
        sig = json.loads(r.stdout).get("X-Bogus", "")
        if not sig:
            raise RuntimeError("empty X-Bogus signature")
        return sig

    return await asyncio.to_thread(run)


_WEB_RID_RE = re.compile(r"[A-Za-z0-9]{6,24}")


def parse_douyin_room_input(raw: str) -> tuple[str, str]:
    """解析用户输入 → ("rid", web_rid) / ("short", 短链码) / ("", "")。

    支持直播间号、live.douyin.com 链接、v.douyin.com 分享短链，以及整段
    抖音分享文案（含话题标签等前后文字）。
    """
    text = (raw or "").strip()
    m = re.search(r"live\.douyin\.com/([A-Za-z0-9]+)", text)
    if m and _WEB_RID_RE.fullmatch(m.group(1)):
        return "rid", m.group(1)
    m = re.search(r"v\.douyin\.com/([A-Za-z0-9]+)", text)
    if m:
        return "short", m.group(1)
    if _WEB_RID_RE.fullmatch(text):
        return "rid", text
    return "", ""


def normalize_douyin_room(room_id: str) -> str:
    """Accept a bare web_rid or a full room URL; return the web_rid.

    分享短链需要联网跟随跳转，由 ``_resolve_short_link`` 异步解析，这里返回 ""。
    """
    kind, value = parse_douyin_room_input(room_id)
    return value if kind == "rid" else ""


# ── adapter ───────────────────────────────────────────────────────────────


class DouyinDirectAdapter:
    """抖音弹幕直连适配器：填直播间号即可拉取真实弹幕，无需外部工具。"""

    _CHAT_METHODS = {
        "WebcastChatMessage",
        "WebcastAudioChatMessage",  # 语音弹幕（已转文本）
        "WebcastEmojiChatMessage",
    }
    # 连续解析失败这么多次后放弃（房间号错误/房间不存在），避免无限重试
    _MAX_RESOLVE_FAILURES = 5

    def __init__(self, session_id: str, room_id: str = "") -> None:
        kind, value = parse_douyin_room_input(room_id)
        if not value:
            raise ValueError(
                f"抖音直播间号不正确：{room_id!r}"
                "（支持直播间号、直播间链接或抖音分享短链/分享文案）"
            )
        missing = [
            p.name for p in (SCRIPTS / "sign.js", SCRIPTS / "sign_runner.js")
            if not p.exists()
        ]
        if missing or not (SCRIPTS / "node_modules").exists():
            raise ValueError(
                f"抖音直连缺少签名组件 {missing or ['node_modules']}，"
                "请检查 backend/scripts 目录（含 npm install）"
            )
        if not shutil.which("node"):
            raise ValueError("抖音直连签名需要 Node.js，请先安装 node 并加入 PATH")
        self._session_id = session_id
        self._web_rid = value if kind == "rid" else ""
        self._short_code = value if kind == "short" else ""
        self._room_id = ""  # resolved real room id
        self._ttwid = ""
        self._webid = ""
        self._resolve_failures = 0
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._task: asyncio.Task | None = None
        self._running = False

    async def connect(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._ws_loop())
        logger.info(
            "douyin direct adapter starting for room %s",
            self._web_rid or f"short:{self._short_code}",
        )

    async def events(self):
        while True:
            yield await self._queue.get()

    async def close(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ── connection loop ───────────────────────────────────────────────────

    async def _ws_loop(self) -> None:
        retry = 1.0
        while self._running:
            try:
                self._ttwid = await _get_ttwid()
                if not self._web_rid:
                    try:
                        self._web_rid = await _resolve_short_link(
                            self._ttwid, self._short_code
                        )
                        self._resolve_failures = 0
                        logger.info(
                            "douyin direct: share link %s resolved to web_rid %s",
                            self._short_code,
                            self._web_rid,
                        )
                    except RoomUnresolvableError:
                        self._resolve_failures += 1
                        if self._resolve_failures >= self._MAX_RESOLVE_FAILURES:
                            logger.error(
                                "douyin direct: share link %r unresolvable "
                                "(expired, wrong code or room offline), giving up",
                                self._short_code,
                            )
                            return
                        raise
                if not self._room_id:
                    try:
                        self._room_id = await _resolve_room(self._ttwid, self._web_rid)
                        self._resolve_failures = 0
                        logger.info(
                            "douyin direct: room %s resolved to %s",
                            self._web_rid, self._room_id,
                        )
                    except RoomUnresolvableError:
                        self._resolve_failures += 1
                        if self._resolve_failures >= self._MAX_RESOLVE_FAILURES:
                            logger.error(
                                "douyin direct: room %r unresolvable (wrong id or "
                                "room offline), giving up",
                                self._web_rid,
                            )
                            return
                        raise
                self._webid = await _get_webid(self._ttwid)
                url = _ws_url(self._room_id, self._webid)
                sig = await _sign(url)
                if await self._stream(f"{url}&signature={sig}"):
                    retry = 1.0  # connected at least once; reset backoff
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("douyin direct adapter error: %s", exc)
            if not self._running:
                return
            logger.info("douyin direct adapter reconnecting in %.0fs", retry)
            await asyncio.sleep(retry)
            retry = min(retry * 2, 30.0)

    async def _stream(self, signed_url: str) -> bool:
        """Run one wss connection until it drops. True when it connected."""
        async with ws_connect(
            signed_url,
            additional_headers={
                "User-Agent": UA,
                "Cookie": f"ttwid={self._ttwid}; msToken={_gen_mstoken()}",
                "Origin": "https://live.douyin.com",
            },
            max_size=2**22,
        ) as ws:
            logger.info("douyin direct: connected to room %s", self._web_rid)
            ping = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    if not self._running:
                        break
                    if not isinstance(raw, bytes):
                        continue
                    events, ack = self.parse_frame(raw, self._session_id)
                    if ack is not None:
                        await ws.send(ack)
                    for event in events:
                        try:
                            self._queue.put_nowait(event)
                        except asyncio.QueueFull:
                            logger.warning("douyin direct queue full, dropping event")
            finally:
                ping.cancel()
        return True

    @staticmethod
    async def _ping_loop(ws) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                # PushFrame: field 4 = method ("ping")
                await ws.send(pb_bytes(4, b"ping"))
        except asyncio.CancelledError:
            pass

    # ── protobuf → LiveEvent (pure, unit-testable) ────────────────────────

    @classmethod
    def parse_frame(
        cls, raw: bytes, session_id: str
    ) -> tuple[list[LiveEvent], bytes | None]:
        """Decode one PushFrame into (events, ack_bytes | None)."""
        try:
            frame = pb_decode(raw)
        except ValueError:
            return [], None
        payload = _first(frame, 8, bytes)
        if payload is None:
            return [], None
        try:
            inner = gzip.decompress(payload)
        except OSError:
            inner = payload
        try:
            resp = pb_decode(inner)
        except ValueError:
            return [], None

        events: list[LiveEvent] = []
        for msg in resp.get(1, []):
            if not isinstance(msg, bytes):
                continue
            try:
                m = pb_decode(msg)
            except ValueError:
                continue
            method = _f_str(m, 1)
            msg_payload = _first(m, 2, bytes)
            if not method or msg_payload is None:
                continue
            event = cls.parse_message(method, msg_payload, session_id)
            if event is not None:
                events.append(event)

        ack = None
        if _f_int(resp, 10):
            cursor = _f_str(resp, 2)
            internal = _f_str(resp, 6)
            log_id = _f_int(frame, 2)
            body = json.dumps({"internal_ext": internal, "cursor": cursor}).encode()
            ack = b""
            if log_id:
                ack += pb_int(2, log_id)
            ack += pb_bytes(4, b"ack") + pb_bytes(8, body)
        return events, ack

    @classmethod
    def parse_message(
        cls, method: str, payload: bytes, session_id: str
    ) -> LiveEvent | None:
        """Normalize one business message (e.g. WebcastChatMessage)."""
        try:
            fields = pb_decode(payload)
        except ValueError:
            return None
        common = _f_msg(fields, 1) or {}
        msg_id = _f_int(common, 2)

        if method in cls._CHAT_METHODS:
            event_type = EventType.chat
            user_id, nickname = cls._user_at(fields, 2)
            text = _f_str(fields, 3) or "[表情]"
            extra = {"text": text, "platform_user_id": user_id}
        elif method == "WebcastLikeMessage":
            event_type = EventType.like
            user_id, nickname = cls._user_at(fields, 5)
            extra = {"count": _f_int(fields, 2) or 1}
        elif method == "WebcastMemberMessage":
            event_type = EventType.user_enter
            user_id, nickname = cls._user_at(fields, 3)
            extra = {}
        elif method == "WebcastSocialMessage":
            event_type = EventType.follow
            user_id, nickname = cls._user_at(fields, 2)
            extra = {}
        elif method == "WebcastLightGiftMessage":
            # Observed: {3: repeat, 5: userId, 7: {1: giftId}, 8: {1: value}},
            # no user profile. value unit is 1/1000 抖币 (小心心 → 1000 → 1).
            event_type = EventType.gift
            user_id = str(_f_int(fields, 5) or "")
            nickname = "未知"
            repeat = _f_int(fields, 3) or 1
            raw_value = _f_int(_f_msg(fields, 8), 1)
            extra = {
                "gift": "轻量礼物",
                "count": repeat,
                "repeat": repeat,
                "value": max(1, round(raw_value / 1000)) if raw_value else 1,
            }
        elif method == "WebcastGiftMessage":
            event_type = EventType.gift
            user_id, nickname = cls._user_at(fields, 7)
            _, gift_name, diamond = cls._find_gift(fields)
            count = cls._small_int(fields, (5, 6)) or 1
            extra = {
                "gift": gift_name or "礼物",
                "count": count,
                "repeat": count,
                "value": diamond or 1,
            }
        else:
            return None  # 系统/统计类消息不映射

        payload_dict = {"user": nickname, **extra}
        if msg_id:
            payload_dict["message_id"] = f"dy:{msg_id}"
        return LiveEvent(
            id=int(now() * 1000) % 10**9,
            session_id=session_id,
            type=event_type.value,
            payload=payload_dict,
            ts=now(),
        )

    # ── user / gift heuristic extraction ──────────────────────────────────

    @classmethod
    def _user_at(
        cls, fields: dict[int, list], *positions: int
    ) -> tuple[str, str]:
        """Nickname from known field positions, else a BFS over the tree."""
        for pos in positions:
            sub = _f_msg(fields, pos)
            if sub and (hit := cls._user_signature(sub)):
                return hit
        return cls._bfs_user(fields, 0) or ("", "未知")

    @staticmethod
    def _user_signature(sub: dict[int, list]) -> tuple[str, str] | None:
        """User = {1: userId (int, big), 3: nickname (str), 4: gender 0-2}."""
        uid = _f_int(sub, 1)
        if uid < 10_000:
            return None
        nick = _f_str(sub, 3)
        if not nick or len(nick) > 40 or any(ord(c) < 32 for c in nick):
            return None
        gender = _first(sub, 4, int)
        if gender is not None and gender > 2:
            return None
        return str(uid), nick

    @classmethod
    def _bfs_user(
        cls, fields: dict[int, list], depth: int
    ) -> tuple[str, str] | None:
        if depth > 4:
            return None
        subs = []
        for values in fields.values():
            for v in values:
                if isinstance(v, bytes) and v:
                    try:
                        sub = pb_decode(v)
                    except ValueError:
                        continue
                    subs.append(sub)
                    if (hit := cls._user_signature(sub)):
                        return hit
        for sub in subs:  # nothing at this level; go deeper
            if (hit := cls._bfs_user(sub, depth + 1)):
                return hit
        return None

    @classmethod
    def _find_gift(cls, fields: dict[int, list], depth: int = 0) -> tuple[int, str, int]:
        """Heuristic GiftStruct lookup: {1: giftId (small int), 2: name (str)}.

        Returns (gift_id, name, diamond_count).
        """
        if depth > 3:
            return 0, "", 0
        subs = []
        for values in fields.values():
            for v in values:
                if isinstance(v, bytes) and v:
                    try:
                        sub = pb_decode(v)
                    except ValueError:
                        continue
                    subs.append(sub)
                    gid = _f_int(sub, 1)
                    name = _f_str(sub, 2)
                    if gid and gid < 10**10 and cls._plausible_gift_name(name):
                        diamond = next(
                            (
                                x
                                for f in range(3, 17)
                                for x in sub.get(f, [])
                                if isinstance(x, int) and 1 <= x <= 10**6
                            ),
                            0,
                        )
                        return gid, name, diamond
        for sub in subs:
            gid, name, diamond = cls._find_gift(sub, depth + 1)
            if gid:
                return gid, name, diamond
        return 0, "", 0

    @staticmethod
    def _plausible_gift_name(name: str) -> bool:
        return (
            bool(name)
            and len(name) <= 40
            and not any(ord(c) < 32 for c in name)
            and not name.startswith(("#", "http", "webcast/"))
        )

    @staticmethod
    def _small_int(fields: dict[int, list], positions) -> int:
        for pos in positions:
            for v in fields.get(pos, []):
                if isinstance(v, int) and 1 <= v <= 10_000:
                    return v
        return 0
