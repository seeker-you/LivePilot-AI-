"""End-to-end smoke test against a running LivePilot API.

Usage: python scripts/smoke_test.py [base_url]
"""

from __future__ import annotations

import sys
import time

import httpx


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def main() -> None:
    # Generous timeout: report generation may hit a slow local model
    # (e.g. Ollama with deepseek-r1) configured through the admin UI.
    client = httpx.Client(base_url=BASE, timeout=90)
    health = client.get("/api/health")
    assert health.status_code == 200, health.text
    print("[ok] health", health.json())

    token = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert token.status_code == 200, token.text
    access = token.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    print("[ok] login admin")

    session = client.post(
        "/api/live/start",
        json={"title": "冒烟测试直播", "event_rate": 6},
        headers=headers,
    )
    assert session.status_code == 200, session.text
    sid = session.json()["id"]
    print("[ok] start session", sid)

    time.sleep(4)
    snapshot = client.get(f"/api/live/{sid}/snapshot", headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    snap = snapshot.json()
    print(
        "[ok] snapshot events=%d actions=%d online=%s"
        % (
            len(snap["recent_events"]),
            len(snap["recent_actions"]),
            snap["metrics"].get("online"),
        )
    )

    metrics = client.get(f"/api/reports/{sid}/metrics", headers=headers)
    assert metrics.status_code == 200, metrics.text
    m = metrics.json()
    print(
        "[ok] metrics chats=%d gifts=%d actions=%d"
        % (m["chat_count"], m["gift_count"], m["action_count"])
    )

    report = client.post(f"/api/reports/{sid}/generate", headers=headers)
    assert report.status_code == 200, report.text
    body = report.json()
    print("[ok] report generated, head:", body["report"][:60].replace("\n", " "))

    stopped = client.post(f"/api/live/{sid}/stop", headers=headers)
    assert stopped.status_code == 200, stopped.text
    print("[ok] stop session ->", stopped.json()["status"])

    client.close()
    print("[done] smoke test passed")


if __name__ == "__main__":
    main()

