"""Platform adapter boundary plus mock / Douyin / Wxlive event sources.

Real platform integrations implement the same ``PlatformAdapter`` contract and
normalize vendor fields into ``LiveEvent``. The mock source makes the whole
pipeline demonstrable without credentials.

Real-platform ingestion modes:

- ``douyin``: 直连抖音（无需外部工具）。``DouyinDirectAdapter`` 自己完成
  ttwid/webid 获取、房间号解析、X-Bogus 签名和 wss 弹幕流解码，
  填直播间号（web_rid）即可拉取真实弹幕。
- ``douyin_grab``: 旧模式。DouyinBarrageGrab
  (github.com/ape-byte/DouyinBarrageGrab) hooks the browser, decodes the live
  protobuf stream and broadcasts JSON events on a local WebSocket;
  ``DouyinAdapter`` connects as a WS client and retries with exponential
  backoff.
- ``wxlive`` (微信视频号): wxlivespy (github.com/fire4nt/wxlivespy) watches
  the Channels backend via browser automation and POSTs events to our
  ``POST /api/ingest/wxlive`` webhook; ``WxliveAdapter`` consumes them from a
  queue. Vendor ``seq`` is normalized into ``payload["message_id"]`` so the
  controller's dedup window handles replays.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import uuid
from typing import Protocol

from .config import settings
from .db import now
from .douyin_direct import DouyinDirectAdapter
from .models import EventType, LiveEvent

logger = logging.getLogger("livepilot.platform")

try:  # websockets >= 13 ships the new asyncio client under this path
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - older bundled versions
    from websockets import connect as ws_connect  # type: ignore


class PlatformAdapter(Protocol):
    async def connect(self) -> None: ...
    async def events(self) -> "AsyncIterator[LiveEvent]": ...
    async def close(self) -> None: ...


USERNAMES = [
    "小鹿爱睡觉", "奶茶三分糖", "数码小白", "种草选手", "路人甲",
    "今天也要开心", "买买买选手", "阿杰", "栗子同学", "熬夜冠军",
    "Momo", "一颗柚子", "晚风", "豆豆不吃辣", "Sakura",
]

CHAT_POOL = [
    "这个面霜油皮可以用吗？",
    "多少钱？",
    "主播皮肤好好，被种草了",
    "怎么下单？",
    "发货快吗？",
    "有没有优惠券？",
    "敏感肌能用吗？",
    "已拍，坐等收货",
    "这个价格真的划算",
    "能再介绍一下成分吗？",
    "库存还够吗？",
    "主播讲得好详细",
    "物流一般几天到？",
    "能不能送个小样？",
    "支持七天无理由吗？",
    "第一次来，有什么福利？",
    "这个和别家比怎么样？",
    "榜一有专属福利吗？",
    "太喜欢这个直播间了",
    "什么时候上链接？",
    "加微信聊聊？",
]

GIFTS = [
    {"name": "小心心", "value": 1},
    {"name": "棒棒糖", "value": 2},
    {"name": "荧光棒", "value": 5},
    {"name": "玫瑰", "value": 10},
    {"name": "火箭", "value": 100},
    {"name": "嘉年华", "value": 1000},
]


class MockPlatform:
    """Deterministic-ish generator for realistic danmaku/gift traffic."""

    def __init__(self, session_id: str, event_rate: float) -> None:
        self.session_id = session_id
        self.event_rate = event_rate
        self._seq = 0

    async def events(self):
        while True:
            await asyncio.sleep(random.uniform(0.4, 1.4) / self.event_rate)
            yield self._next_event()

    def _next_event(self) -> LiveEvent:
        self._seq += 1
        roll = random.random()
        username = random.choice(USERNAMES)
        if roll < 0.62:
            event_type = EventType.chat
            payload = {
                "user": username,
                "text": random.choice(CHAT_POOL),
                "platform_user_id": str(random.randint(1000, 99999)),
            }
        elif roll < 0.78:
            gift = random.choices(GIFTS, weights=[30, 22, 18, 15, 10, 5])[0]
            event_type = EventType.gift
            payload = {
                "user": username,
                "gift": gift["name"],
                "value": gift["value"],
            }
        elif roll < 0.9:
            event_type = EventType.user_enter
            payload = {
                "user": username,
                "level": random.randint(1, 60),
                "is_vip": random.random() < 0.2,
            }
        elif roll < 0.97:
            event_type = EventType.like
            payload = {"user": username, "count": random.randint(1, 50)}
        else:
            event_type = EventType.follow
            payload = {"user": username}
        return LiveEvent(
            id=self._seq,
            session_id=self.session_id,
            type=event_type.value,
            payload=payload,
            ts=now(),
        )


class MockAdapter:
    def __init__(self, session_id: str, event_rate: float) -> None:
        self._platform = MockPlatform(session_id, event_rate)

    async def connect(self) -> None:
        return None

    def events(self):
        return self._platform.events()

    async def close(self) -> None:
        return None


# ── 抖音：DouyinBarrageGrab 本地 WS 广播 → 归一化事件 ─────────────────────


class DouyinAdapter:
    """Douyin danmaku adapter over DouyinBarrageGrab's local WebSocket.

    The grab tool broadcasts one JSON object per message, in either PascalCase
    (older) or camelCase (newer) shape; both are accepted. WS drops are
    retried with exponential backoff (1s → 30s cap).
    """

    # DouyinBarrageGrab message type codes -> our EventType
    _TYPE_MAP = {
        1: EventType.chat,       # 弹幕
        2: EventType.like,       # 点赞
        3: EventType.user_enter, # 进入直播间
        4: EventType.follow,     # 关注
        5: EventType.gift,       # 礼物
    }

    def __init__(self, session_id: str, ws_url: str | None = None, room_id: str = "") -> None:
        self._session_id = session_id
        self._ws_url = ws_url or settings.douyin_ws_url
        self._room_id = room_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._task: asyncio.Task | None = None
        self._running = False

    async def connect(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._ws_loop())
        logger.info("douyin adapter connecting to %s", self._ws_url)

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

    async def _ws_loop(self) -> None:
        retry_delay = 1.0
        while self._running:
            try:
                async with ws_connect(self._ws_url) as ws:
                    logger.info("douyin adapter connected")
                    retry_delay = 1.0
                    async for raw in ws:
                        if not self._running:
                            break
                        event = self.parse_message(raw, self._session_id, self._room_id)
                        if event is not None:
                            self._queue.put_nowait(event)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("douyin adapter error: %s", exc)
            if self._running:
                logger.info("douyin adapter reconnecting in %.0fs", retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    @staticmethod
    def _room_matches(data: dict, room_id: str) -> bool:
        """True when the broadcast belongs to the configured room.

        DouyinBarrageGrab tags each message with the room's ``GroupId`` (aka
        ``groupId``/``roomId`` in some builds). Broadcasts without any room
        field pass through so older tool builds keep working.
        """
        for key in ("GroupId", "groupId", "RoomId", "roomId"):
            value = data.get(key)
            if value and str(value) != room_id:
                return False
        return True

    @classmethod
    def parse_message(
        cls, raw: str | dict, session_id: str, room_id: str = ""
    ) -> LiveEvent | None:
        """Normalize one DouyinBarrageGrab broadcast into a LiveEvent.

        When ``room_id`` is set, broadcasts from a different room (matched on
        the vendor ``GroupId``/``groupId``/``roomId`` field) are dropped.
        """
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return None
        # PascalCase (old) / camelCase (new) field compat
        msg_type = raw.get("Type") or raw.get("type")
        data = raw.get("Data") or raw.get("data") or {}
        if isinstance(data, str):  # old versions embed a JSON string
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None
        if room_id and not cls._room_matches(data, room_id):
            return None
        event_type = cls._TYPE_MAP.get(msg_type)
        if event_type is None:
            return None
        user = data.get("User") or data.get("user") or {}
        nickname = user.get("Nickname") or user.get("nickname") or "未知"

        if event_type is EventType.chat:
            payload = {
                "user": nickname,
                "text": data.get("Content") or data.get("content") or "",
                "platform_user_id": str(user.get("Id") or user.get("id") or ""),
            }
        elif event_type is EventType.like:
            payload = {
                "user": nickname,
                "count": int(data.get("Count") or data.get("count") or 1),
            }
        elif event_type is EventType.gift:
            count = int(data.get("GiftCount") or data.get("giftCount") or 1)
            repeat = int(data.get("RepeatCount") or data.get("repeatCount") or 1)
            payload = {
                "user": nickname,
                "gift": data.get("GiftName") or data.get("giftName") or "礼物",
                "count": count,
                "repeat": repeat,
                # 抖币精确值未透出时按份数计，语义与 mock 的 value 一致
                "value": count * repeat,
            }
        else:  # user_enter / follow
            payload = {"user": nickname}

        return LiveEvent(
            id=int(now() * 1000) % 10**9,
            session_id=session_id,
            type=event_type.value,
            payload=payload,
            ts=now(),
        )


# ── 视频号：wxlivespy HTTP 回调 → 队列桥接 ────────────────────────────────

# 活跃 wxlive 会话的事件队列注册表：webhook 收到事件后分发到这里。
_wxlive_queues: dict[str, asyncio.Queue] = {}
_wxlive_lock = threading.Lock()

# wxlivespy decoded_type -> our EventType
_WXLIVE_TYPE_MAP = {
    "comment": EventType.chat,
    "enter": EventType.user_enter,
    "gift": EventType.gift,
    "like": EventType.like,
    "follow": EventType.follow,
}


def wxlive_register(session_id: str, queue: asyncio.Queue) -> None:
    with _wxlive_lock:
        _wxlive_queues[session_id] = queue


def wxlive_unregister(session_id: str) -> None:
    with _wxlive_lock:
        _wxlive_queues.pop(session_id, None)


def wxlive_dispatch(events: list[dict]) -> int:
    """Normalize wxlivespy events and fan out to every active session.

    Returns the number of events delivered (0 when no session is live or
    nothing maps to a known type). Vendor ``seq`` becomes
    ``payload["message_id"]`` so controller-level dedup absorbs replays.
    """
    with _wxlive_lock:
        sessions = list(_wxlive_queues.items())
    if not sessions:
        return 0
    delivered = 0
    for evt in events:
        for session_id, queue in sessions:
            event = WxliveAdapter.normalize_event(evt, session_id)
            if event is None:
                continue
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning("wxlive queue full, event dropped")
    return delivered


class WxliveAdapter:
    """微信视频号适配器：消费 webhook 注入队列的归一化事件。"""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=2000)

    async def connect(self) -> None:
        wxlive_register(self._session_id, self._queue)
        logger.info("wxlive adapter waiting for webhook events")

    async def events(self):
        while True:
            yield await self._queue.get()

    async def close(self) -> None:
        wxlive_unregister(self._session_id)

    @staticmethod
    def normalize_event(evt: dict, session_id: str) -> LiveEvent | None:
        """Normalize one wxlivespy event into a LiveEvent."""
        decoded_type = evt.get("decoded_type", "")
        event_type = _WXLIVE_TYPE_MAP.get(decoded_type)
        if event_type is None:
            return None
        nickname = evt.get("nickname") or "未知"

        if event_type is EventType.chat:
            payload = {"user": nickname, "text": evt.get("content", "")}
        elif event_type is EventType.gift:
            payload = {
                "user": nickname,
                "gift": evt.get("gift_name", "礼物"),
                "value": 1,
            }
        else:  # user_enter / like / follow
            payload = {"user": nickname}

        seq = str(evt.get("seq", "") or "")
        if seq:
            payload["message_id"] = f"wxlive:{seq}"

        return LiveEvent(
            id=abs(hash(f"wxlive:{seq}:{decoded_type}:{nickname}")) % 10**9,
            session_id=session_id,
            type=event_type.value,
            payload=payload,
            ts=now(),
        )


def build_adapter(
    platform: str, session_id: str, event_rate: float, room_id: str = ""
) -> PlatformAdapter:
    """Event-source factory. ``event_rate`` only applies to the mock source."""
    if platform == "mock":
        return MockAdapter(session_id, event_rate)
    if platform == "douyin":
        # 直连抖音：room_id 为直播间号（web_rid），必填。
        return DouyinDirectAdapter(session_id, room_id=room_id)
    if platform == "douyin_grab":
        # 旧模式：DouyinBarrageGrab 本地 WS 广播。
        # GroupId-based room filtering keeps other live rooms out when set.
        return DouyinAdapter(session_id, room_id=room_id)
    if platform == "wxlive":
        return WxliveAdapter(session_id)
    raise ValueError(f"Unsupported platform: {platform}")

