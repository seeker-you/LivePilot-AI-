"""Tests for LLM-assisted knowledge organization."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app import knowledge_ai


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class OrganizeTextTest(unittest.TestCase):
    def test_llm_json_array(self):
        fake = """[{"title": "发货时效", "product": "熬夜修护面霜", "category": "policy",
        "content": "现货 48 小时内发货，默认中通/圆通，偏远地区顺丰补差。",
        "tags": ["发货", "物流", "多久到"]}]"""
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("素材……", "熬夜修护面霜", "policy"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "发货时效")
        self.assertEqual(items[0]["category"], "policy")
        self.assertIn("发货", items[0]["tags"])

    def test_fenced_json(self):
        fake = '```json\n[{"title": "卖点", "product": "A", "category": "product", "content": "成分扎实", "tags": ["成分"]}]\n```'
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("x"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "成分扎实")

    def test_invalid_json_falls_back_to_split(self):
        with patch.object(knowledge_ai.llm, "generate_text", return_value="抱歉，我无法理解"):
            items = run(
                knowledge_ai.organize_text(
                    "主推商品卖点\n玻色因+烟酰胺，28 天改善暗沉，敏感肌可用。\n\n"
                    "物流政策\n现货 48 小时发货，支持 7 天无理由退换。",
                    "熬夜修护面霜",
                    "policy",
                )
            )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "主推商品卖点")
        self.assertEqual(items[0]["category"], "policy")
        self.assertEqual(items[0]["product"], "熬夜修护面霜")

    def test_no_llm_key_fallback(self):
        with patch.object(knowledge_ai.llm, "generate_text", return_value=None):
            items = run(knowledge_ai.organize_text("第一行标题\n内容一。\n\n第二行标题\n内容二。"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "第一行标题")
        self.assertEqual(items[1]["title"], "第二行标题")
        self.assertEqual(items[0]["tags"], [])

    def test_empty_input(self):
        self.assertEqual(run(knowledge_ai.organize_text("   ")), [])

    def test_normalizes_bad_category_and_truncation(self):
        fake = '[{"title": "T", "product": "P", "category": "weird", "content": "C", "tags": [" a ", "a"]}]'
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("x"))
        self.assertEqual(items[0]["category"], "general")
        self.assertEqual(items[0]["tags"], ["a"])

    def test_drops_items_missing_content(self):
        fake = '[{"title": "ok", "product": "P", "category": "general", "content": "有内容", "tags": []}, {"title": "缺内容", "product": "P", "category": "general", "tags": []}]'
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("x"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "ok")


class TagCleaningTest(unittest.TestCase):
    def test_sentence_tags_dropped(self):
        fake = (
            '[{"title": "T", "product": "P", "category": "policy", "content": "C", '
            '"tags": ["敏感肌能不能用", "什么时候发货", "发货", "物流"]}]'
        )
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("x"))
        self.assertEqual(items[0]["tags"], ["发货", "物流"])

    def test_punctuation_stripped(self):
        fake = '[{"title": "T", "product": "P", "category": "policy", "content": "C", "tags": ["敏感肌，", " 退换 ", "优惠。"]}]'
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("x"))
        self.assertEqual(items[0]["tags"], ["敏感肌", "退换", "优惠"])

    def test_overlong_tag_dropped(self):
        fake = '[{"title": "T", "product": "P", "category": "policy", "content": "C", "tags": ["这个问题非常常见", "发货"]}]'
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("x"))
        self.assertEqual(items[0]["tags"], ["发货"])

    def test_tags_capped_at_six(self):
        fake = (
            '[{"title": "T", "product": "P", "category": "policy", "content": "C", '
            '"tags": ["一","二","三","四","五","六","七","八"]}]'
        )
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("x"))
        self.assertEqual(len(items[0]["tags"]), 6)

    def test_bare_number_tags_dropped(self):
        fake = '[{"title": "T", "product": "P", "category": "product", "content": "C", "tags": ["35", "36", "尺码", "偏大"]}]'
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            items = run(knowledge_ai.organize_text("x"))
        self.assertEqual(items[0]["tags"], ["尺码", "偏大"])


class EnrichItemTest(unittest.TestCase):
    def test_llm_enriches(self):
        fake = '{"title": "敏感肌能用吗", "product": "熬夜修护面霜", "category": "product", "content": "含玻色因+烟酰胺，敏感肌可用，质地清爽。", "tags": ["敏感肌", "过敏", "红血丝"]}'
        with patch.object(knowledge_ai.llm, "generate_text", return_value=fake):
            item = run(knowledge_ai.enrich_item("", "敏感肌能用吗？可以，成分温和。"))
        self.assertEqual(item["title"], "敏感肌能用吗")
        self.assertEqual(item["category"], "product")
        self.assertIn("敏感肌", item["tags"])

    def test_fallback_passthrough(self):
        with patch.object(knowledge_ai.llm, "generate_text", return_value=None):
            item = run(
                knowledge_ai.enrich_item("发货", "现货 48 小时发货。", "通用", "policy")
            )
        self.assertEqual(item["title"], "发货")
        self.assertEqual(item["category"], "policy")
        self.assertEqual(item["content"], "现货 48 小时发货。")
        self.assertEqual(item["tags"], [])

    def test_invalid_json_fallback(self):
        with patch.object(knowledge_ai.llm, "generate_text", return_value="不行的样子"):
            item = run(knowledge_ai.enrich_item("", "内容甲。", "", "product"))
        self.assertEqual(item["category"], "product")
        self.assertEqual(item["content"], "内容甲。")

    def test_bad_category_fallback_to_general(self):
        with patch.object(knowledge_ai.llm, "generate_text", return_value=None):
            item = run(knowledge_ai.enrich_item("", "内容乙。", "", "whatever"))
        self.assertEqual(item["category"], "general")


if __name__ == "__main__":
    unittest.main()
