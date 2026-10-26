"""Live session aggregation and LLM-assisted replay report generation.

Besides whole-session totals, ``aggregate_session`` derives a minute-level
timeline from the raw ``events`` table (no platform API needed) and detects
rhythm insights: the peak minute, cold stretches and highlight moments.
These feed both the frontend trend chart and the LLM / template report so
the generated text references concrete points in time.
"""

from __future__ import annotations

from typing import Any

from . import llm
from .db import db, loads
from .runtime_config import build_persona_prompt

# 提问类意图计入“高频提问”
QUESTION_INTENTS = frozenset({"price_inquiry", "question", "order"})


# ── minute-level timeline ─────────────────────────────────────────────────


def build_timeline(events: list[dict], start_ts: float, end_ts: float) -> list[dict]:
    """把事件按分钟分桶，返回填满每一分钟的时间序列（含 0 事件分钟）。

    每个桶：minute（从 0 起的分钟序号）、ts（桶起点 epoch 秒）、chat、
    gift_count、gift_value、likes、enters（进场+关注）、total。
    """
    if not events or end_ts <= start_ts:
        return []
    minutes = int((end_ts - start_ts) / 60) + 1
    buckets = [
        {
            "minute": i,
            "ts": start_ts + i * 60,
            "chat": 0,
            "gift_count": 0,
            "gift_value": 0,
            "likes": 0,
            "enters": 0,
        }
        for i in range(minutes)
    ]
    last = minutes - 1
    for row in events:
        idx = int((row["ts"] - start_ts) / 60)
        if idx < 0 or idx > last:  # 开播前/结束后的时钟漂移，钳到边界
            idx = 0 if idx < 0 else last
        b = buckets[idx]
        etype = row["type"]
        if etype == "chat":
            b["chat"] += 1
        elif etype == "gift":
            payload = loads(row.get("payload"), {})
            b["gift_count"] += 1
            b["gift_value"] += int(payload.get("value", 0) or 0)
        elif etype == "like":
            payload = loads(row.get("payload"), {})
            b["likes"] += int(payload.get("count", 0) or 0)
        else:  # user_enter / follow 都视作进场增量
            b["enters"] += 1
    for b in buckets:
        b["total"] = b["chat"] + b["gift_count"] + b["likes"] + b["enters"]
    return buckets


def _describe_bucket(b: dict) -> str:
    parts = []
    if b["chat"]:
        parts.append(f"弹幕 {b['chat']}")
    if b["gift_count"]:
        parts.append(f"礼物 {b['gift_count']} 笔（价值 {b['gift_value']}）")
    if b["likes"]:
        parts.append(f"点赞 {b['likes']}")
    if b["enters"]:
        parts.append(f"进场 {b['enters']}")
    return "、".join(parts) or "无互动"


def _highlight_score(b: dict) -> float:
    """高光加权分：礼物价值权重最高（真金白银），进场最低。"""
    return b["gift_value"] * 2 + b["chat"] + b["likes"] * 0.2 + b["enters"] * 0.5


def detect_insights(timeline: list[dict]) -> dict:
    """从分钟序列检测峰值分钟、冷场段（≥2 分钟低活跃）与高光分钟。"""
    empty = {"peak": None, "cold_spots": [], "highlights": []}
    if not timeline:
        return empty
    peak = max(timeline, key=lambda b: b["total"])
    avg = sum(b["total"] for b in timeline) / len(timeline)

    # 冷场：互动量 ≤ 均值 30%（至少 1）且持续 ≥ 2 分钟
    threshold = max(1.0, avg * 0.3)
    cold_spots: list[dict] = []
    run_start: int | None = None
    for b in timeline:
        if b["total"] <= threshold:
            if run_start is None:
                run_start = b["minute"]
        elif run_start is not None:
            if b["minute"] - run_start >= 2:
                cold_spots.append({"from": run_start, "to": b["minute"] - 1})
            run_start = None
    if run_start is not None and len(timeline) - run_start >= 2:
        cold_spots.append({"from": run_start, "to": len(timeline) - 1})

    highlights = sorted(
        (b for b in timeline if b["total"] > 0), key=_highlight_score, reverse=True
    )[:3]
    return {
        "peak": {
            "minute": peak["minute"],
            "total": peak["total"],
            "detail": _describe_bucket(peak),
        },
        "cold_spots": cold_spots,
        "highlights": [
            {
                "minute": b["minute"],
                "detail": _describe_bucket(b),
                "score": round(_highlight_score(b), 1),
            }
            for b in highlights
        ],
    }


# ── session aggregation ───────────────────────────────────────────────────


def aggregate_session(session_id: str) -> dict[str, Any]:
    events = db.query(
        "SELECT * FROM events WHERE session_id = ? ORDER BY id ASC", (session_id,)
    )
    actions = db.query(
        "SELECT * FROM actions WHERE session_id = ? ORDER BY id ASC", (session_id,)
    )
    session = db.query_one("SELECT * FROM live_sessions WHERE id = ?", (session_id,))

    chat_count = gift_total = gift_count = like_total = follow_count = 0
    enter_count = question_total = answered_total = order_count = 0
    unique_users: set = set()
    sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
    top_questions: dict[str, int] = {}
    tag_hits: dict[str, int] = {}
    blind_spots: dict[str, int] = {}

    for row in events:
        payload = loads(row.get("payload"), {})
        etype = row["type"]
        user = payload.get("user")
        if user:
            unique_users.add(user)
        if etype == "chat":
            chat_count += 1
            sentiment_counts[payload.get("sentiment", "neutral")] += 1
            intent = payload.get("intent")
            if intent == "order":
                order_count += 1
            if intent in QUESTION_INTENTS:
                text = payload.get("text", "")
                top_questions[text] = top_questions.get(text, 0) + 1
                question_total += 1
                if payload.get("kb_hit"):
                    answered_total += 1
                    for tag in payload.get("kb_tags", []):
                        if isinstance(tag, str):
                            tag_hits[tag] = tag_hits.get(tag, 0) + 1
                else:
                    blind_spots[text] = blind_spots.get(text, 0) + 1
        elif etype == "gift":
            gift_count += 1
            gift_total += int(payload.get("value", 0))
        elif etype == "like":
            like_total += int(payload.get("count", 0))
        elif etype == "follow":
            follow_count += 1
            enter_count += 1
        elif etype == "user_enter":
            enter_count += 1

    peak_online = loads((session or {}).get("metrics") or "{}", {}).get(
        "peak_online", len(unique_users)
    )
    top = sorted(top_questions.items(), key=lambda kv: kv[1], reverse=True)[:5]
    coverage = round(answered_total / question_total * 100, 1) if question_total else None
    blind_top = sorted(blind_spots.items(), key=lambda kv: kv[1], reverse=True)[:5]
    tag_top = sorted(tag_hits.items(), key=lambda kv: kv[1], reverse=True)[:8]
    funnel = [
        {"label": "进场", "value": enter_count},
        {"label": "互动", "value": chat_count},
        {"label": "咨询商品", "value": question_total},
        {"label": "购买意图", "value": order_count + follow_count},
    ]

    # 分钟级时间序列：开播→结束（直播中则到最后一笔事件）
    start_ts = (session or {}).get("started_at") or (
        events[0]["ts"] if events else 0
    )
    end_ts = (session or {}).get("ended_at") or (events[-1]["ts"] if events else 0)
    timeline = build_timeline(events, start_ts, end_ts)
    insights = detect_insights(timeline)

    return {
        "session": session,
        "total_events": len(events),
        "chat_count": chat_count,
        "gift_count": gift_count,
        "gift_total": gift_total,
        "like_total": like_total,
        "follow_count": follow_count,
        "unique_users": len(unique_users),
        "peak_online": peak_online,
        "sentiment": sentiment_counts,
        "top_questions": [{"text": text, "count": count} for text, count in top],
        "question_total": question_total,
        "answered_total": answered_total,
        "coverage": coverage,
        "blind_spots": [{"text": text, "count": count} for text, count in blind_top],
        "tag_hits": [{"tag": tag, "count": count} for tag, count in tag_top],
        "funnel": funnel,
        "action_count": len(actions),
        "action_breakdown": _count_actions(actions),
        "timeline": timeline,
        "insights": insights,
    }


def _count_actions(actions) -> dict[str, int]:
    result: dict[str, int] = {}
    for action in actions:
        result[action["type"]] = result.get(action["type"], 0) + 1
    return result


# ── report generation ─────────────────────────────────────────────────────


def _rhythm_lines(metrics: dict[str, Any]) -> list[str]:
    """时间维度数据行：供 LLM prompt 与离线模板共用。"""
    timeline = metrics.get("timeline") or []
    insights = metrics.get("insights") or {}
    lines = [f"时长：{len(timeline)} 分钟"]
    peak = insights.get("peak")
    if peak:
        lines.append(
            f"互动峰值：第 {peak['minute'] + 1} 分钟（{peak['detail']}）"
        )
    cold = insights.get("cold_spots") or []
    if cold:
        shown = "、".join(
            f"第 {c['from'] + 1}-{c['to'] + 1} 分钟" for c in cold[:5]
        )
        lines.append(f"冷场时段：{shown}")
    else:
        lines.append("冷场时段：无明显冷场")
    highlights = insights.get("highlights") or []
    if highlights:
        shown = "；".join(
            f"第 {h['minute'] + 1} 分钟（{h['detail']}）" for h in highlights
        )
        lines.append(f"高光时刻：{shown}")
    return lines


async def generate_report(metrics: dict[str, Any]) -> str:
    session = metrics.get("session") or {}

    prompt = (
        f"直播标题：{session.get('title', '未知')}"
        f"\n总弹幕：{metrics['chat_count']}，礼物总价值：{metrics['gift_total']}"
        f"，礼物数：{metrics['gift_count']}，点赞：{metrics['like_total']}"
        f"，关注新增：{metrics['follow_count']}，独立观众：{metrics['unique_users']}"
        f"，峰值在线：{metrics['peak_online']}"
        f"\n情绪分布：{metrics['sentiment']}"
        f"\n高频提问：{metrics['top_questions']}"
        f"\nAI 动作：{metrics['action_count']} 条，分类：{metrics['action_breakdown']}"
    )
    rhythm = _rhythm_lines(metrics)
    if rhythm:
        prompt += "\n时间维度：" + "；".join(rhythm)

    generated = await llm.generate_text(
        build_persona_prompt()
        + "\n你是直播运营复盘专家。基于数据输出 Markdown 复盘：1. 本场表现总结；"
        "2. 数据亮点；3. 暴露的问题；4. 下一场可执行建议；5. 节奏分析（结合"
        "峰值/冷场/高光出现的具体时间点，说明哪段时间该加热、哪段该稳住）。"
        "语气客观，建议具体可落地。",
        prompt,
    )
    if generated:
        return generated
    return _template_report(metrics)


def _template_report(metrics: dict[str, Any]) -> str:
    session = metrics.get("session") or {}
    sentiment = metrics["sentiment"]
    total = max(1, sum(sentiment.values()))
    neg_ratio = sentiment["negative"] / total * 100
    top = (
        ", ".join(
            f"{q['text']}（{q['count']}次）" for q in metrics["top_questions"][:3]
        )
        or "暂无"
    )
    rhythm = _rhythm_lines(metrics)

    return (
        f"## 直播复盘：{session.get('title', '本场直播')}"
        f"\n\n### 本场表现总结\n本场累计弹幕 {metrics['chat_count']} 条，"
        f"礼物总价值 {metrics['gift_total']}，峰值在线 {metrics['peak_online']} 人，"
        f"独立观众 {metrics['unique_users']} 人。"
        f"\n\n### 节奏与时间维度\n"
        + "\n".join(f"- {line}。" for line in rhythm)
        + f"\n\n### 数据亮点\n- 新增关注 {metrics['follow_count']}，"
        f"点赞 {metrics['like_total']}，互动基础良好。\n- AI 场控共产出 "
        f"{metrics['action_count']} 条动作：{metrics['action_breakdown']}。"
        f"\n\n### 暴露的问题\n- 负面情绪占比约 {neg_ratio:.0f}%，"
        f"需重点核实敏感与投诉内容。\n- 高频提问集中在：{top}，"
        f"话术与知识库覆盖需要跟进。"
        f"\n\n### 下一场建议\n1. 将高频提问补入商品知识库，让 AI 自动回复更精准。\n"
        "2. 对负面弹幕设置升级提醒，安排场控及时私聊处理。\n"
        "3. 冷场时段提前安排抽奖/福袋等暖场动作，高光时刻承接促单话术。"
    )
