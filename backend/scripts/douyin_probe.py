"""Probe the Douyin live danmaku protocol end to end.

Pipeline (all anonymous, no login):
  1. ttwid cookie from live.douyin.com
  2. fresh webid (user_unique_id) from mcs.zijieapi.com
  3. web_rid -> real room_id scraped from the SSR room page
  4. X-Bogus signature for the push URL via node sign_runner.js (sign.js)
  5. wss connect + generic protobuf decode + message tree dump

Usage: python scripts/douyin_probe.py [web_rid] [--seconds 45]

Diagnostic tool: prints the observed protobuf field layout so the real
adapter can be written against reality. HTTP steps shell out to curl.exe
because its TLS fingerprint passes Douyin's edge checks from this network.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import random
import re
import string
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from websockets.asyncio.client import connect

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SCRIPTS = Path(__file__).resolve().parent
TMP = SCRIPTS / "_probe_tmp"
TMP.mkdir(exist_ok=True)


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


def as_tree(buf: bytes, depth: int = 0):
    """Best-effort: decode bytes as a nested message tree for dumping."""
    if depth > 7 or not buf:
        return None
    try:
        decoded = pb_decode(buf)
    except ValueError:
        return None
    if not decoded:
        return None
    tree: dict = {}
    useful = False
    for field, values in decoded.items():
        shown = []
        for v in values:
            if isinstance(v, bytes):
                sub = as_tree(v, depth + 1)
                if sub is not None and sub != {}:
                    shown.append(sub)
                    useful = True
                    continue
                try:
                    s = v.decode("utf-8")
                    shown.append(s)
                    useful = True
                except UnicodeDecodeError:
                    shown.append(f"<bytes:{len(v)}>")
            else:
                shown.append(v)
                useful = True
        tree[field] = shown[0] if len(shown) == 1 else shown
    return tree if useful else None


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


# ── HTTP helpers over curl.exe ────────────────────────────────────────────


def curl(
    url: str, cookie: str = "", data: str | None = None, method: str = "GET"
) -> tuple[dict[str, str], bytes]:
    """HTTP via curl.exe; returns (lowercased headers, body bytes)."""
    hdr_file = TMP / "h.txt"
    body_file = TMP / "b.bin"
    cmd = [
        "curl.exe", "-s", "--compressed", "-m", "20",
        "-A", UA,
        "-H", "Accept-Language: zh-CN,zh;q=0.9",
        "-H", "Referer: https://live.douyin.com/",
        "-D", str(hdr_file), "-o", str(body_file),
    ]
    if cookie:
        cmd += ["-H", f"Cookie: {cookie}"]
    if data is not None:
        cmd += ["-X", method, "-H", "Content-Type: application/json", "--data", data]
    cmd.append(url)
    subprocess.run(cmd, check=True)
    headers: dict[str, str] = {}
    for line in hdr_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    return headers, body_file.read_bytes()


def get_ttwid() -> str:
    curl("https://live.douyin.com/")
    hdr_text = (TMP / "h.txt").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"(?im)^set-cookie:\s*ttwid=([^;]+)", hdr_text)
    if not m:
        raise RuntimeError(f"no ttwid in response headers:\n{hdr_text[:500]}")
    return m.group(1)


def get_webid(ttwid: str) -> str:
    """Register a fresh anonymous device id (user_unique_id)."""
    _, body = curl(
        "https://mcs.zijieapi.com/webid",
        cookie=f"ttwid={ttwid}",
        method="POST",
        data=json.dumps(
            {
                "app_id": 6383,
                "url": "https://live.douyin.com/",
                "user_agent": UA,
                "referer": "https://live.douyin.com/",
                "user_unique_id": "",
            }
        ),
    )
    resp = json.loads(body)
    webid = (resp.get("data") or {}).get("web_id") or resp.get("web_id")
    if not webid:
        raise RuntimeError(f"webid registration failed: {body[:200]!r}")
    return str(webid)


def gen_mstoken(length: int = 107) -> str:
    base = string.ascii_letters + string.digits + "=_"
    return "".join(random.choice(base) for _ in range(length))


# ── room discovery ────────────────────────────────────────────────────────


FALLBACK_RIDS = ["856477039359", "KXyy12345678", "306512506808", "737576399880"]


def discover_rids(ttwid: str, limit: int = 5) -> list[str]:
    """Extract live-room web_rid candidates from the homepage HTML."""
    _, body = curl("https://live.douyin.com/", cookie=f"ttwid={ttwid}")
    html = body.decode("utf-8", errors="replace")
    found: list[str] = []
    for m in re.finditer(r"live\.douyin\.com/(\d{6,20})", html):
        rid = m.group(1)
        if rid not in found:
            found.append(rid)
    return found[:limit]


def resolve_room(ttwid: str, rid: str) -> dict:
    """Scrape the SSR room page to resolve web_rid -> real room info."""
    _, body = curl(f"https://live.douyin.com/{rid}", cookie=f"ttwid={ttwid}")
    html = body.decode("utf-8", errors="replace")

    def find(*patterns: str) -> str:
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                return m.group(1)
        return ""

    room_id = find(r'roomId\\":\\"(\d+)\\"', r'"roomId"\s*:\s*"?(\d{6,})"?')
    return {
        "web_rid": rid,
        "room_id": room_id,
        "title": find(r'\\"title\\":\\"([^"\\]{1,60})'),
        "status": find(r'roomStatus\\?":\\?"(\d)'),
        "owner": find(r'nickname\\":\\"([^"\\]{1,24})'),
    }


# ── signing + wss ─────────────────────────────────────────────────────────


def ws_url(room_id: str, user_unique_id: str) -> str:
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


def xbogus(wss_url: str) -> str:
    """Compute the X-Bogus signature for a push URL via node sign_runner.js."""
    params = (
        "live_id,aid,version_code,webcast_sdk_version,"
        "room_id,sub_room_id,sub_channel_id,did_rule,"
        "user_unique_id,device_platform,device_type,ac,"
        "identity"
    ).split(",")
    maps = {
        p.split("=")[0]: p.split("=")[-1]
        for p in urlparse(wss_url).query.split("&")
    }
    tpl = [f"{k}={maps.get(k, '')}" for k in params]
    md5 = hashlib.md5(",".join(tpl).encode()).hexdigest()
    r = subprocess.run(
        ["node", "sign_runner.js", json.dumps({"X-MS-STUB": md5})],
        capture_output=True, text=True, cwd=SCRIPTS, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"sign failed: {r.stderr[:200]}")
    return json.loads(r.stdout).get("X-Bogus", "")


async def dump_stream(signed_url: str, ttwid: str, seconds: int) -> None:
    counts: dict[str, int] = {}
    dumped: set[str] = set()
    deadline = time.time() + seconds

    async with connect(
        signed_url,
        additional_headers={
            "User-Agent": UA,
            "Cookie": f"ttwid={ttwid}; msToken={gen_mstoken()}",
            "Origin": "https://live.douyin.com",
        },
        max_size=2**22,
    ) as ws:
        print(f"[ws] connected, dumping for {seconds}s ...")

        async def heartbeat():
            while True:
                await asyncio.sleep(10)
                # PushFrame: field 4 = method ("ping")
                await ws.send(pb_bytes(4, b"ping"))
                print("[ws] ping sent")

        hb = asyncio.create_task(heartbeat())
        try:
            while time.time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                frame_fields = pb_decode(raw)
                payload = _first_bytes(frame_fields, 8)
                log_id = _first_int(frame_fields, 2)
                if payload is None:
                    print("[frame] no payload field 8; fields:", list(frame_fields))
                    continue
                try:
                    inner = gzip.decompress(payload)
                except OSError:
                    inner = payload
                resp = pb_decode(inner)
                need_ack = bool(_first_int(resp, 10))
                for msg_fields in resp.get(1, []):
                    if not isinstance(msg_fields, bytes):
                        continue
                    m = pb_decode(msg_fields)
                    m_method = _first_str(m, 1)
                    m_payload = _first_bytes(m, 2)
                    counts[m_method] = counts.get(m_method, 0) + 1
                    if m_method not in dumped and m_payload:
                        dumped.add(m_method)
                        print(f"\n===== {m_method} (first seen) =====")
                        tree = as_tree(m_payload)
                        print(
                            json.dumps(tree, ensure_ascii=False, indent=1, default=str)[:3000]
                        )
                if need_ack:
                    cursor = _first_str(resp, 2) or ""
                    internal = _first_str(resp, 6) or ""
                    ack_body = json.dumps(
                        {"internal_ext": internal, "cursor": cursor}
                    ).encode()
                    ack = b""
                    if log_id:
                        ack += pb_varint((2 << 3) | 0) + pb_varint(log_id)
                    ack += pb_bytes(4, b"ack") + pb_bytes(8, ack_body)
                    await ws.send(ack)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        finally:
            hb.cancel()
    print("\n[counts]", json.dumps(counts, ensure_ascii=False, indent=1))


def _first(fields: dict[int, list], n: int, cls):
    for v in fields.get(n, []):
        if isinstance(v, cls):
            return v
    return None


def _first_str(fields: dict[int, list], n: int) -> str:
    v = _first(fields, n, bytes)
    return v.decode("utf-8", errors="replace") if v is not None else ""


def _first_int(fields: dict[int, list], n: int) -> int:
    return _first(fields, n, int) or 0


def _first_bytes(fields: dict[int, list], n: int) -> bytes | None:
    return _first(fields, n, bytes)


async def main() -> None:
    seconds = 45
    args: list[str] = []
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--seconds":
            seconds = int(argv[i + 1])
            i += 2
        elif argv[i].startswith("-"):
            i += 1
        else:
            args.append(argv[i])
            i += 1
    rid = args[0] if args else ""

    ttwid = get_ttwid()
    print(f"[ok] ttwid: {ttwid[:24]}...")
    webid = get_webid(ttwid)
    print(f"[ok] webid: {webid}")

    if not rid:
        rids = discover_rids(ttwid) or FALLBACK_RIDS
        print(f"[ok] rid candidates: {rids}")
        room = None
        for cand in rids:
            info = resolve_room(ttwid, cand)
            print(f"    {cand}: room={info['room_id'][:12]} owner={info['owner']!r}")
            if info["room_id"]:
                room = info
                break
        if not room:
            raise SystemExit("no live room found among candidates")
    else:
        room = resolve_room(ttwid, rid)
        if not room["room_id"]:
            raise SystemExit(f"cannot resolve room for rid {rid!r}")
    print(f"[ok] room_id={room['room_id']} web_rid={room['web_rid']}")

    url = ws_url(room["room_id"], webid)
    sig = xbogus(url)
    print(f"[ok] signature: {sig}")
    await dump_stream(f"{url}&signature={sig}", ttwid, seconds)


if __name__ == "__main__":
    asyncio.run(main())
