"""Content generation service for titles and live-streaming scripts.

Like the rest of the AI surface, this prefers a real model when a key is
configured and falls back to deterministic templates so the feature works
offline. Product-related kinds pull knowledge-base context into the prompt.
"""

from __future__ import annotations

from typing import Any

from . import knowledge, llm
from .runtime_config import build_persona_prompt


KINDS: dict[str, dict[str, Any]] = {
    "title": {"label": "直播标题", "needs_kb": False},
    "opening": {"label": "开场话术", "needs_kb": False},
    "closing": {"label": "逼单话术", "needs_kb": False},
    "qa": {"label": "答疑话术", "needs_kb": True},
    "product_script": {"label": "商品口播", "needs_kb": True},
}


def list_kinds() -> list[dict[str, Any]]:
    return [
        {"kind": key, "label": meta["label"], "needs_kb": meta["needs_kb"]}
        for key, meta in KINDS.items()
    ]


async def generate(
    kind: str, topic: str, style: str, context: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    """Return (generated_text, referenced_knowledge_titles)."""
    if kind not in KINDS:
        kind = "opening"

    kb_text = ""
    sources: list[str] = []
    if KINDS[kind]["needs_kb"]:
        hits = await knowledge.search(topic or style, limit=3)
        sources = [hit["title"] for hit in hits]
        kb_text = "\n".join(f"- {hit['title']}: {hit['content']}" for hit in hits)

    system = _system_prompt(kind, style)
    user = _user_prompt(kind, topic, kb_text, context)
    text = await llm.generate_text(system, user)
    return text or _fallback(kind, topic, kb_text, style), sources


def _system_prompt(kind: str, style: str) -> str:
    style_guide = {
        "自然亲和": "语气亲切自然，像朋友聊天，多用「宝宝们」拉近距离",
        "专业严谨": "语气专业客观，突出成分、数据、卖点依据",
        "幽默活泼": "语气轻松有梗，适当用短句和节奏感带动情绪",
        "高转化逼单": "强调限时、限量、优惠与紧迫感，制造下单动力",
    }.get(style, "语气自然专业")

    directives = {
        "title": "输出 5 个可直接用于直播间的标题，每个一行，不要序号以外的解释",
        "opening": "输出一段 60-100 字的开场白，先打招呼、抛福利、预告今晚内容",
        "closing": "输出一段 80-120 字的逼单话术，强调优惠、库存与限时，给明确行动指令",
        "qa": "基于知识库输出 3 组高频问题及其口播回答，每组标注问题和回答",
        "product_script": "输出一段 120-200 字的商品口播脚本，按卖点展开并落到行动",
    }[kind]

    return (
        f"{build_persona_prompt()}\n"
        f"你是资深直播运营与口播文案专家。{style_guide}。"
        f"要求口语化、可直接照念、不编造知识库之外的事实。{directives}。"
    )


def _user_prompt(
    kind: str, topic: str, kb_text: str, context: list[dict[str, Any]]
) -> str:
    topic_line = f"主题/商品：{topic}" if topic else "主题/商品：未指定"
    live = "\n".join(
        f"- {c.get('user', '观众')}: {c.get('text', c.get('type', ''))}"
        for c in context[-8:]
    ) or "（暂无实时弹幕）"
    parts = [topic_line, f"知识库：\n{kb_text or '（无匹配知识）'}", f"近期弹幕：\n{live}"]
    return "\n".join(parts)


def _fallback(kind: str, topic: str, kb_text: str, style: str) -> str:
    subject = topic or "本场主推商品"
    if kind == "title":
        return (
            f"1. {subject}｜今晚直播间专属福利，手慢无\n"
            f"2. 熬夜党必入！{subject} 主播同款实测分享\n"
            f"3. {subject} 直播专场：买一送一，前 100 名加赠\n"
            f"4. 敏感肌也能用的 {subject}，主播在线答疑\n"
            f"5. {subject} 今晚直播价 199，限量秒杀通道已开启"
        )
    if kind == "opening":
        return (
            f"宝宝们晚上好，欢迎来到直播间～今天给大家带来的是 {subject} 的专场，"
            "主播会把它最核心的卖点、成分和用法一次讲透。还没点关注的宝宝先点个关注，"
            "今晚的福利和优惠都会在直播间限时放出，千万别错过！"
        )
    if kind == "closing":
        return (
            f"宝宝们，今晚的 {subject} 优惠名额已经不多了，直播间专属价 + 买一送一，"
            "前 100 名还加赠小样。库存是实打实地在往下掉，想要的下单入口就在下方，"
            "现在拍最划算，错过今晚就要恢复原价啦，抓紧手速！"
        )
    if kind == "qa":
        if kb_text:
            return (
                f"问：{subject} 适合什么肤质？\n答：可以看主播刚才讲解的知识点：{kb_text[:80]}…\n\n"
                f"问：现在下单有什么优惠？\n答：直播间专属价 + 买一送一，前 100 名加赠小样。\n\n"
                f"问：发货和售后怎么样？\n答：现货 48 小时内发货，支持 7 天无理由退换。"
            )
        return (
            f"问：{subject} 多少钱？\n答：宝宝看直播间价格牌，今晚是专属优惠价。\n\n"
            f"问：怎么下单？\n答：点击下方购物袋，选好规格直接拍，拍下就是福利价。\n\n"
            f"问：发货快吗？\n答：现货 48 小时内发货，请宝宝们放心。"
        )
    # product_script
    return (
        f"来看今天的主角 {subject}。它的核心就是「好用 + 划算」：成分扎实、肤感清爽，"
        "日常用完全没有负担，而且今晚是直播间专属价，还买一送一。主播把它放在购物袋里了，"
        "想要的宝宝直接拍，前 100 名还能额外拿到小样，等于到手更值。"
    )

