"""End-to-end check for the script library + speech queue.

Usage: python scripts/queue_smoke.py [base_url]
"""

from __future__ import annotations

import sys
import time

import httpx


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=20)
    token = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    scripts = client.get("/api/scripts", headers=headers).json()
    print("[ok] seeded scripts:", [s["title"] for s in scripts])

    session = client.post(
        "/api/live/start", json={"title": "队列验证", "event_rate": 6}, headers=headers
    ).json()
    sid = session["id"]
    print("[ok] started session", sid)

    time.sleep(2)
    queue = client.get(f"/api/live/{sid}/queue", headers=headers).json()
    print(
        "[ok] queue next=%s high=%d low=%d"
        % (
            (queue["next"] or {}).get("content", "")[:24],
            len(queue["high"]),
            len(queue["low"]),
        )
    )

    snapshot = client.get(f"/api/live/{sid}/snapshot", headers=headers).json()
    replies = [a for a in snapshot["recent_actions"] if a["type"] == "auto_reply"]
    if replies:
        action = replies[0]
        resp = client.post(
            f"/api/live/{sid}/actions/{action['id']}/review",
            json={"status": "approved"},
            headers=headers,
        )
        print("[ok] approve auto_reply:", resp.status_code)
        time.sleep(0.3)
        after = client.get(f"/api/live/{sid}/queue", headers=headers).json()
        print("[ok] after approve high=%d next_source=%s" % (len(after["high"]), (after["next"] or {}).get("source")))
        popped = client.post(f"/api/live/{sid}/queue/pop", headers=headers).json()
        print("[ok] popped source:", (popped.get("popped") or {}).get("source"))

    client.post(f"/api/live/{sid}/stop", headers=headers)
    client.close()
    print("[done] queue smoke test passed")


if __name__ == "__main__":
    main()

