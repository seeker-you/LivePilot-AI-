"""LLM-assisted knowledge organization.

Turns raw, loosely-structured material (paste or file text) into atomic,
well-tagged knowledge entries, and enriches a single draft entry with tags
and a structured description. Both paths prefer a real model when configured
and degrade to a deterministic splitter so the feature works offline.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import llm

ALLOWED_CATEGORIES = {"product", "policy", "script", "general"}

_MAX_RAW = 12000
_MAX_TITLE = 120
_MAX_PRODUCT = 80
_MAX_CATEGORY = 40
_MAX_CONTENT = 4000
_MAX_TAGS = 6
_TAG_MAX_LEN = 5

# 标签必须是短关键词，与知识库既有风格一致（面霜/修护/敏感肌/发货/运费险）。
_TAG_RULES = (
    "tags 规则（必须严格遵守）：每个标签 2-4 个字的短关键词，给 3-6 个；"
    "参考既有标签风格：面霜、修护、敏感肌、发货、物流、退换、运费险、优惠、券、满减。"
    "正确示例 tags 为 [\"敏感肌\", \"过敏\", \"红血丝\"]；"
    "错误示例 tags 为 [\"敏感肌能不能用\", \"什么时候发货\", \"这个面霜多少钱\"]。"
    "禁止整句问法、禁止带标点或疑问词（吗/呢/怎么/如何/多少/多久）。"
)

_ORGANIZE_SYSTEM = (
    "你是直播运营知识库整理助手。把用户粘贴的零散素材拆解成多条结构化知识条目，"
    "每条只表达一个观众能独立提问、独立引用的信息点（如发货政策、某个卖点、优惠规则）。\n"
    "输出严格的 JSON 数组，不要任何解释文字、不要 markdown 代码块。字段与示例格式如下：\n"
    '[{"title": "发货时效", "product": "熬夜修护面霜", "category": "policy", '
    '"content": "现货 48 小时内发货；默认中通/圆通；偏远地区顺丰补差；支持 7 天无理由退换", '
    '"tags": ["发货", "物流", "退换", "运费险"]}]\n'
    "要求：\n"
    "- title 用观众视角命名，如「发货时效」\n"
    "- content 必须写素材里的真实信息，用分号或换行分点，禁止照抄上面的示例或字段说明\n"
    f"- {_TAG_RULES}\n"
    "- 只留事实，删除广告水话\n"
    "- 分类：product=商品卖点/成分/适用人群；policy=物流/售后/优惠规则；"
    "script=可直接照念的话术；general=其他"
)

_ENRICH_SYSTEM = (
    "你是直播运营知识库助手。基于用户提供的标题和内容，补全并优化成一条结构化知识条目。"
    "内容要保留原始数字、成分与政策事实，只做分点整理，不要压缩成概括性总结。\n"
    "输出严格的 JSON 对象，不要任何解释文字、不要 markdown 代码块。字段与示例格式如下：\n"
    '{"title": "尺码怎么选", "product": "运动鞋", "category": "product", '
    '"content": "偏大一码，平时 36 码建议选 35 码；脚背高按正常码；可对照详情页尺码表；'
    '拿不准可拍两双，运费险免费退", '
    '"tags": ["尺码", "偏大", "脚背高", "运费险"]}\n'
    "content 只能使用用户提供的事实，禁止照抄上面的示例或字段说明。\n"
    f"{_TAG_RULES}"
)


def _extract_json(text: str | None) -> Any:
    """Parse a JSON array/object from model output, tolerating fences and prose."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


_TAG_STRIP_RE = re.compile(r"[\s，,。.、；;：:！!？?\"'“”‘’（）()\[\]【】《》<>#/\\|~`*]+")
_TAG_BAD_WORDS = (
    "吗", "呢", "什么", "怎么", "如何", "多少", "能不能", "有没有",
    "是不是", "要不要", "多久", "哪个", "哪些", "请问",
)


def _clean_tag(raw: Any) -> str:
    """Keep only short keyword tags; drop sentences, question phrasing and bare numbers."""
    tag = _TAG_STRIP_RE.sub("", str(raw or ""))
    if not tag or len(tag) > _TAG_MAX_LEN or tag.isdigit():
        return ""
    if any(bad in tag for bad in _TAG_BAD_WORDS):
        return ""
    return tag


def _normalize_item(it: Any, default_product: str = "通用") -> dict[str, Any] | None:
    if not isinstance(it, dict):
        return None
    title = str(it.get("title") or "").strip()[:_MAX_TITLE]
    product = str(it.get("product") or "").strip()[:_MAX_PRODUCT] or default_product
    category = str(it.get("category") or "").strip().lower()[:_MAX_CATEGORY]
    if category not in ALLOWED_CATEGORIES:
        category = "general"
    content = str(it.get("content") or "").strip()[:_MAX_CONTENT]
    if not title or not content:
        return None
    tags: list[str] = []
    for raw_tag in it.get("tags") or []:
        tag = _clean_tag(raw_tag)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= _MAX_TAGS:
            break
    return {
        "title": title,
        "product": product,
        "category": category,
        "content": content,
        "tags": tags,
    }


def _fallback_split(raw: str, product_hint: str = "", category_hint: str = "") -> list[dict[str, Any]]:
    """Deterministic splitter: blank-line blocks become one entry per block."""
    category = category_hint.strip().lower() if category_hint.strip().lower() in ALLOWED_CATEGORIES else "general"
    default_product = product_hint.strip()[:_MAX_PRODUCT] or "通用"
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw.strip()) if b.strip()]
    if not blocks:
        blocks = [raw.strip()]

    items: list[dict[str, Any]] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if len(lines) == 1:
            title = "未命名条目"
            content = block[:_MAX_CONTENT]
        else:
            title = lines[0][:_MAX_TITLE]
            content = "\n".join(lines[1:])[:_MAX_CONTENT]
        item = _normalize_item(
            {"title": title, "product": default_product, "category": category, "content": content, "tags": []},
            default_product,
        )
        if item:
            items.append(item)

    if not items:
        items.append(
            {
                "title": "未命名条目",
                "product": default_product,
                "category": category,
                "content": raw.strip()[:_MAX_CONTENT],
                "tags": [],
            }
        )
    return items


async def organize_text(raw: str, product_hint: str = "", category_hint: str = "") -> list[dict[str, Any]]:
    """Split loose material into atomic knowledge entries."""
    raw = (raw or "").strip()
    if not raw:
        return []
    user = (
        f"商品/主题提示：{product_hint.strip() or '无'}\n"
        f"分类提示：{category_hint.strip() or '无'}\n"
        f"素材：\n{raw[:_MAX_RAW]}"
    )
    text = await llm.generate_text(_ORGANIZE_SYSTEM, user)
    parsed = _extract_json(text)
    if isinstance(parsed, list):
        items = [item for item in (_normalize_item(it) for it in parsed) if item]
        if items:
            return items
    return _fallback_split(raw, product_hint, category_hint)


async def enrich_item(
    title: str = "", content: str = "", product: str = "", category: str = ""
) -> dict[str, Any]:
    """Enrich a draft entry with tags and a structured description."""
    title = (title or "").strip()
    content = (content or "").strip()
    product = (product or "").strip()
    category = (category or "").strip().lower()
    default_product = product[:_MAX_PRODUCT] or "通用"

    user = (
        f"标题：{title or '（无）'}\n"
        f"商品/主题：{product or '（无）'}\n"
        f"分类提示：{category or '（无）'}\n"
        f"内容：\n{content[:_MAX_RAW]}"
    )
    text = await llm.generate_text(_ENRICH_SYSTEM, user)
    parsed = _extract_json(text)
    if isinstance(parsed, dict):
        item = _normalize_item(parsed, default_product=default_product)
        if item:
            return item

    fallback_category = category if category in ALLOWED_CATEGORIES else "general"
    return {
        "title": title or "未命名条目",
        "product": default_product,
        "category": fallback_category,
        "content": content[:_MAX_CONTENT],
        "tags": [],
    }
