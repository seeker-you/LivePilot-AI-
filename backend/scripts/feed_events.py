"""Simulate external danmaku collectors to exercise the real adapter paths.

DouyinBarrageGrab / wxlivespy do the protocol capture in a real deployment;
this script impersonates their output so the full ingestion path (WS client /
webhook -> normalize -> dedup -> engine -> DB -> WebSocket fan-out) can be
verified end-to-end without installing them.

Usage (start the backend and open the console first):

  # Douyin: serve DouyinBarrageGrab-style broadcasts on ws://127.0.0.1:8888
  python scripts/feed_events.py douyin --room 123456 --n 100 --interval 0.3

  # Wxlive: POST wxlivespy-style event batches to the ingest webhook
  python scripts/feed_events.py wxlive --n 100 --interval 0.3 [--token SECRET]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time

sys.path.insert(0, ".")

NICKNAMES = ["奶茶不加糖", "晚风十里", "阿杰", "Momo", "栗子", "豆豆", "柚子", "老白"]
COMMENTS = [
    "主播这个多少钱呀",
    "有优惠券吗",
    "发货快吗，急用",
    "色号适合黄皮吗",
    "用过来说一句，很好用",
    "差评，上次发的货不对",
    "支持主播，已下单",
    "哈哈哈太好笑了",
]
GIFTS = ["玫瑰", "小心心", "棒棒糖", "嘉年华"]


def _douyin_message(room: str) -> dict:
    """One DouyinBarrageGrab-style broadcast (new camelCase shape)."""
    msg_type = random.choices([1, 2, 3, 4, 5], weights=[60, 15, 15, 5, 5])[0]
    data: dict = {
        "user": {"nickname": random.choice(NICKNAMES), "id": random.randint(1000, 9999)},
        "groupId": room,
    }
    if msg_type == 1:
        data["content"] = random.choice(COMMENTS)
    elif msg_type == 2:
        data["count"] = random.randint(1, 10)
    elif msg_type == 5:
        data["giftName"] = random.choice(GIFTS)
        data["giftCount"] = random.randint(1, 3)
        data["repeatCount"] = 1
    return {"type": msg_type, "data": data}


def _wxlive_event(seq: int) -> dict:
    """One wxlivespy-style event."""
    decoded_type = random.choices(
        ["comment", "enter", "gift"], weights=[70, 20, 10]
    )[0]
    evt: dict = {
        "seq": str(seq),
        "decoded_type": decoded_type,
        "nickname": random.choice(NICKNAMES),
    }
    if decoded_type == "comment":
        evt["content"] = random.choice(COMMENTS)
    elif decoded_type == "gift":
        evt["gift_name"] = random.choice(GIFTS)
    return evt


async def run_douyin(host: str, port: int, room: str, n: int, interval: float) -> None:
    import websockets

    sent = 0

    async def handler(ws) -> None:
        nonlocal sent
        peer = getattr(ws, "remote_address", "?")
        print(f"[douyin] LivePilot connected from {peer}, streaming room {room}")
        while sent < n:
            await ws.send(json.dumps(_douyin_message(room), ensure_ascii=False))
            sent += 1
            await asyncio.sleep(interval)
        print(f"[douyin] done: {sent} events sent")

    async with websockets.serve(handler, host, port):
        print(f"[douyin] broadcasting on ws://{host}:{port} (room={room}, n={n})")
        print("[douyin] now start a Douyin session in the console with the same room id")
        await asyncio.Future()  # runs until Ctrl+C


async def run_wxlive(url: str, n: int, interval: float, token: str) -> None:
    import httpx

    headers = {"X-Ingest-Token": token} if token else {}
    sent = 0
    print(f"[wxlive] POSTing events to {url}")
    print("[wxlive] start a Wxlive session in the console first")
    async with httpx.AsyncClient(timeout=10) as client:
        while sent < n:
            batch = [_wxlive_event(sent + i + 1) for i in range(5)]
            resp = await client.post(url, json={"events": batch}, headers=headers)
            body = resp.json()
            if resp.status_code != 200:
                print(f"[wxlive] rejected: {resp.status_code} {body}")
                return
            print(f"[wxlive] batch ok: received={body['received']} delivered={body['delivered']}")
            sent += len(batch)
            await asyncio.sleep(interval)
    print(f"[wxlive] done: {sent} events sent")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.__doc__ if __doc__ else "")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_douyin = sub.add_parser("douyin", help="serve DouyinBarrageGrab-style WS broadcasts")
    p_douyin.add_argument("--host", default="127.0.0.1")
    p_douyin.add_argument("--port", type=int, default=8888)
    p_douyin.add_argument("--room", default="123456", help="GroupId to stamp on events")
    p_douyin.add_argument("--n", type=int, default=50)
    p_douyin.add_argument("--interval", type=float, default=0.5)

    p_wxlive = sub.add_parser("wxlive", help="POST wxlivespy-style batches to the webhook")
    p_wxlive.add_argument("--url", default="http://127.0.0.1:8000/api/ingest/wxlive")
    p_wxlive.add_argument("--n", type=int, default=50)
    p_wxlive.add_argument("--interval", type=float, default=0.5)
    p_wxlive.add_argument("--token", default="")

    args = parser.parse_args()
    started = time.time()
    if args.mode == "douyin":
        asyncio.run(
            run_douyin(args.host, args.port, args.room, args.n, args.interval)
        )
    else:
        asyncio.run(run_wxlive(args.url, args.n, args.interval, args.token))
    print(f"[{args.mode}] finished in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
