"""Moderation engine: rule-first automation with LLM-assisted classification.

Thresholds and action toggles come from the persisted rules module, so the
moderation behavior can be tuned at runtime without a deploy. Classification
still prefers the model and falls back to heuristics offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import knowledge, llm, rules
from .db import now
from .models import ActionType, EventType, LiveAction, LiveEvent


@dataclass
class EngineResult:
    actions: list[LiveAction] = field(default_factory=list)
    intent: str | None = None
    sentiment: str | None = None
    sensitive: bool = False
    kb_hit: bool = False
    kb_tags: list[str] = field(default_factory=list)


async def process_event(event: LiveEvent) -> EngineResult:
    result = EngineResult()
    cfg = rules.get_rules()
    enabled = set(cfg["enabled_actions"])
    etype = event.type
    payload = event.payload

    if etype == EventType.chat.value:
        text = payload.get("text", "")
        user = payload.get("user", "观众")
        intent_result = await llm.classify(text)
        result.intent = intent_result.intent.value
        result.sentiment = intent_result.sentiment.value
        result.sensitive = intent_result.is_sensitive or any(
            word in text for word in cfg["sensitive_words"]
        )

        if result.sensitive:
            _add(
                result,
                event,
                ActionType.alert,
                f"检测到敏感弹幕「{_truncate(text)}」，建议场控优先安抚并核实问题。",
                enabled,
            )
        if intent_result.intent.value == "spam":
            _add(
                result,
                event,
                ActionType.escalate,
                f"疑似引流弹幕「{_truncate(text)}」，建议禁言并记录账号。",
                enabled,
            )
        elif intent_result.intent.value == "complaint":
            _add(
                result,
                event,
                ActionType.alert,
                f"负面情绪弹幕「{_truncate(text)}」，建议主播正面回应并私聊跟进。",
                enabled,
            )
        elif intent_result.intent.value in {"question", "price_inquiry", "order"}:
            reply, kb_hit, kb_tags = await _build_reply(text)
            result.kb_hit = kb_hit
            result.kb_tags = kb_tags
            _add(result, event, ActionType.auto_reply, reply, enabled)

    elif etype == EventType.gift.value:
        gift = payload.get("gift", "礼物")
        value = int(payload.get("value", 0))
        user = payload.get("user", "观众")
        if value >= int(cfg["gift_alert_threshold"]):
            _add(
                result,
                event,
                ActionType.alert,
                f"大额礼物：{user} 送出「{gift}」（价值 {value}），建议置顶感谢。",
                enabled,
            )
            _add(result, event, ActionType.auto_reply, f"感谢 {user} 的「{gift}」！", enabled)
        elif value >= int(cfg["gift_thank_threshold"]):
            _add(result, event, ActionType.auto_reply, f"感谢 {user} 的「{gift}」！", enabled)

    elif etype == EventType.user_enter.value:
        user = payload.get("user", "观众")
        level = int(payload.get("level", 0))
        is_vip = bool(payload.get("is_vip", False))
        if is_vip or level >= int(cfg["vip_welcome_level"]):
            _add(
                result,
                event,
                ActionType.welcome,
                f"欢迎 {user} 进入直播间，有任何问题随时扣在弹幕上～",
                enabled,
            )

    return result


def _add(
    result: EngineResult,
    event: LiveEvent,
    type_: ActionType,
    content: str,
    enabled: set[str],
) -> None:
    if type_.value not in enabled:
        return
    result.actions.append(
        LiveAction(
            session_id=event.session_id,
            type=type_.value,
            content=content,
            trigger_event_id=event.id,
            status="pending",
            ts=now(),
        )
    )


def _truncate(text: str, limit: int = 36) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


async def _build_reply(query: str) -> tuple[str, bool, list[str]]:
    hits = await knowledge.search(query, limit=1)
    if hits:
        item = hits[0]
        snippet = _truncate(item["content"], 90)
        return (
            f"【自动回复】关于「{_truncate(query, 24)}」：{snippet}",
            True,
            [t for t in item.get("tags", []) if isinstance(t, str)],
        )
    return "【自动回复】主播正在讲解，稍后统一回答，先关注直播间不错过福利～", False, []

