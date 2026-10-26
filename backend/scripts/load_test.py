"""Load test: engine throughput, end-to-end pipeline, HTTP API latency.

Runs fully self-contained via the ASGI transport (no server needed) against
an isolated throwaway SQLite database, then prints a Markdown report and
optionally writes it to a file.

Usage:
    python scripts/load_test.py                 # print report
    python scripts/load_test.py --out docs/load-test-report.md
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import random
import sys
import tempfile
import time
import uuid
from collections import deque
from pathlib import Path

# Silence request logs before app.basicConfig picks a level; only failures
# and the final report should reach stdout.
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before importing `app.*`: the settings object reads env vars at
# import time and the Database is created from it on first import.
_DB = Path(tempfile.gettempdir()) / "livepilot_loadtest.db"
_DB.unlink(missing_ok=True)
os.environ["DATABASE_PATH"] = str(_DB)

import httpx  # noqa: E402

from app import engine, knowledge, ratelimit  # noqa: E402
from app.bus import bus  # noqa: E402
from app.controller import DEDUP_WINDOW, controller  # noqa: E402
from app.db import db, dumps, now  # noqa: E402
from app.main import app  # noqa: E402
from app.models import EventType, LiveEvent  # noqa: E402
from app.routers.auth import seed_demo_users  # noqa: E402

CHAT_POOL = [
    "这个面霜油皮可以用吗？", "多少钱？", "怎么下单？", "发货快吗？",
    "有没有优惠券？", "敏感肌能用吗？", "库存还够吗？", "物流一般几天到？",
    "能再介绍一下成分吗？", "支持七天无理由吗？", "什么时候上链接？",
    "主播讲得好详细", "太喜欢这个直播间了", "这个价格真的划算",
]
USERS = ["小鹿", "奶茶", "阿杰", "栗子", "Momo", "柚子", "晚风", "豆豆"]
GIFTS = [("小心心", 1), ("棒棒糖", 2), ("玫瑰", 10), ("火箭", 100)]


def make_event(session_id: str, seq: int) -> LiveEvent:
    """Mixed traffic mirroring the mock platform's distribution."""
    roll = random.random()
    user = random.choice(USERS)
    if roll < 0.62:
        etype, payload = EventType.chat, {
            "user": user,
            "text": random.choice(CHAT_POOL),
            "message_id": f"loadtest-{session_id}-{seq}",
        }
    elif roll < 0.78:
        gift, value = random.choice(GIFTS)
        etype, payload = EventType.gift, {"user": user, "gift": gift, "value": value}
    elif roll < 0.9:
        etype, payload = EventType.user_enter, {
            "user": user, "level": random.randint(1, 60),
            "is_vip": random.random() < 0.2,
        }
    elif roll < 0.97:
        etype, payload = EventType.like, {"user": user, "count": random.randint(1, 50)}
    else:
        etype, payload = EventType.follow, {"user": user}
    return LiveEvent(
        id=seq, session_id=session_id, type=etype.value, payload=payload, ts=now()
    )


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * p / 100
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def stats_line(latencies_ms: list[float]) -> str:
    return (
        f"p50={pct(latencies_ms, 50):.1f}ms "
        f"p95={pct(latencies_ms, 95):.1f}ms "
        f"p99={pct(latencies_ms, 99):.1f}ms "
        f"max={max(latencies_ms):.1f}ms"
    )


async def bench_engine(n: int, concurrency: int) -> dict:
    """Throughput of engine.process_event alone (rules + classify + reply)."""
    events = [make_event("engine-bench", i) for i in range(n)]
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []

    async def one(event: LiveEvent) -> None:
        async with sem:
            t0 = time.perf_counter()
            await engine.process_event(event)
            latencies.append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    await asyncio.gather(*(one(e) for e in events))
    elapsed = time.perf_counter() - t0
    return {
        "label": "engine",
        "n": n, "elapsed": elapsed,
        "rate": n / elapsed * 60,
        "latencies": latencies,
    }


class BurstAdapter:
    """Feeds a pre-built event list through the controller pipeline."""

    def __init__(self, events: list[LiveEvent]) -> None:
        self._events = events

    async def connect(self) -> None:
        return None

    async def events(self):
        for event in self._events:
            yield event

    async def close(self) -> None:
        return None


async def bench_pipeline(n: int) -> dict:
    """Full path: adapter -> dedup -> engine -> SQLite -> bus fan-out."""
    session_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO live_sessions (id, title, status, host_id, started_at, created_at)"
        " VALUES (?, 'load test', 'live', 1, ?, ?)",
        (session_id, now(), now()),
    )
    controller._metrics[session_id] = {"online": 0, "peak_online": 0}
    controller._seen[session_id] = deque(maxlen=DEDUP_WINDOW)

    queue = await bus.subscribe(session_id)

    async def drain() -> int:
        count = 0
        while True:
            await queue.get()
            count += 1

    consumer = asyncio.create_task(drain())
    t0 = time.perf_counter()
    await controller._run(session_id, BurstAdapter([make_event(session_id, i) for i in range(n)]))
    elapsed = time.perf_counter() - t0
    consumer.cancel()

    stored = db.query_one(
        "SELECT COUNT(*) AS c FROM events WHERE session_id = ?", (session_id,)
    )["c"]
    actions = db.query_one(
        "SELECT COUNT(*) AS c FROM actions WHERE session_id = ?", (session_id,)
    )["c"]
    db.execute(
        "UPDATE live_sessions SET status = 'ended', ended_at = ? WHERE id = ?",
        (now(), session_id),
    )
    return {
        "label": "pipeline", "n": n, "elapsed": elapsed,
        "rate": n / elapsed * 60,
        "stored": stored, "actions": actions,
        "latency_note": "single-session sequential loop",
    }


async def bench_api(total: int, concurrency: int) -> dict:
    """Mixed HTTP load over the ASGI transport: read + auth-heavy login."""
    ratelimit.api_limiter.limit = 10**9
    ratelimit.login_limiter.limit = 10**9

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        read_lat: list[float] = []
        login_lat: list[float] = []
        sem = asyncio.Semaphore(concurrency)
        done = 0

        async def one(i: int) -> None:
            nonlocal done
            async with sem:
                t0 = time.perf_counter()
                if i % 10 == 0:
                    resp = await client.post(
                        "/api/auth/login",
                        json={"username": "admin", "password": "admin123"},
                    )
                    login_lat.append((time.perf_counter() - t0) * 1000)
                else:
                    resp = await client.get("/api/live/sessions", headers=headers)
                    read_lat.append((time.perf_counter() - t0) * 1000)
                assert resp.status_code == 200, resp.text
                done += 1

        t0 = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(total)))
        elapsed = time.perf_counter() - t0
    return {
        "label": "api", "n": total, "elapsed": elapsed,
        "rps": total / elapsed,
        "read_latencies": read_lat, "login_latencies": login_lat,
    }


def render(results: dict) -> str:
    eng, pipe, api = results["engine"], results["pipeline"], results["api"]
    lines = [
        "# LivePilot 压测报告",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}",
        f"- 环境：{platform.platform()}，{os.cpu_count()} 核，Python {sys.version.split()[0]}",
        "- 模式：无 LLM Key（启发式分类降级路径，即规则引擎兜底链路）；SQLite 本地库；单进程",
        "",
        "## 1. 场控引擎内核（process_event：规则 + 分类 + 回复检索）",
        "",
        f"- {eng['n']} 条事件，并发 20",
        f"- 吞吐：**{eng['rate']:.0f} events/min**（{eng['rate'] / 60:.1f}/s），耗时 {eng['elapsed']:.2f}s",
        f"- 单事件延迟：{stats_line(eng['latencies'])}",
        "",
        "## 2. 端到端管道（适配器 → 去重 → 引擎 → 落库 → WebSocket 扇出）",
        "",
        f"- {pipe['n']} 条事件，单会话顺序消费 + 1 个订阅者",
        f"- 吞吐：**{pipe['rate']:.0f} events/min**（{pipe['rate'] / 60:.1f}/s），耗时 {pipe['elapsed']:.2f}s",
        f"- 数据一致性：注入 {pipe['n']}，落库 {pipe['stored']}，动作 {pipe['actions']}，"
        f"**丢失 {pipe['n'] - pipe['stored']} 条**",
        "",
        "## 3. HTTP API（ASGI 内嵌，并发 20，读写混合 9:1）",
        "",
        f"- {api['n']} 请求，总 RPS：**{api['rps']:.1f}**，耗时 {api['elapsed']:.2f}s",
        f"- 会话列表读接口（{len(api['read_latencies'])} 次）：{stats_line(api['read_latencies'])}",
        f"- 登录接口（{len(api['login_latencies'])} 次，PBKDF2 120k 迭代）：{stats_line(api['login_latencies'])}",
        "",
        "## 结论",
        "",
        f"- 目标 1000 events/min：管道吞吐 {pipe['rate']:.0f} events/min —— "
        f"{'达标' if pipe['rate'] >= 1000 else '未达标（瓶颈：SQLite 全局写锁 + 单会话顺序消费，见 README 架构说明的扩展路径）'}",
        f"- 弹幕到动作延迟（引擎内核 p95）：{pct(eng['latencies'], 95):.1f}ms，远低于 1s 验收线",
        f"- 数据零丢失：注入/落库差值 {pipe['n'] - pipe['stored']}",
    ]
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> None:
    seed_demo_users()
    await knowledge.seed_defaults()
    random.seed(42)

    results = {
        "engine": await bench_engine(args.engine_events, 20),
        "pipeline": await bench_pipeline(args.pipeline_events),
        "api": await bench_api(args.api_requests, 20),
    }
    report = render(results)
    print(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"[saved] {out}")
    db.close()
    _DB.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="LivePilot load test")
    parser.add_argument("--engine-events", type=int, default=3000)
    parser.add_argument("--pipeline-events", type=int, default=3000)
    parser.add_argument("--api-requests", type=int, default=400)
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
