import json
import unittest
from unittest.mock import patch

from app import replay
from app.db import db


def _bucket(minute, chat=0, gift_count=0, gift_value=0, likes=0, enters=0):
    return {
        "minute": minute,
        "ts": minute * 60,
        "chat": chat,
        "gift_count": gift_count,
        "gift_value": gift_value,
        "likes": likes,
        "enters": enters,
        "total": chat + gift_count + likes + enters,
    }


class BuildTimelineTest(unittest.TestCase):
    def test_buckets_fill_zero_minutes_and_sum_totals(self):
        events = [
            {"ts": 0, "type": "chat", "payload": "{}"},
            {"ts": 10, "type": "chat", "payload": "{}"},
            {"ts": 130, "type": "like", "payload": '{"count": 5}'},
        ]
        timeline = replay.build_timeline(events, 0, 130)
        self.assertEqual(len(timeline), 3)
        self.assertEqual(timeline[0]["chat"], 2)
        self.assertEqual(timeline[1]["total"], 0)  # 中间空分钟被填满
        self.assertEqual(timeline[2]["likes"], 5)
        self.assertEqual(timeline[2]["total"], 5)
        self.assertEqual([b["minute"] for b in timeline], [0, 1, 2])

    def test_gift_value_and_like_count_from_payload(self):
        events = [
            {"ts": 0, "type": "gift", "payload": '{"value": 99}'},
            {"ts": 5, "type": "like", "payload": '{"count": 3}'},
            {"ts": 6, "type": "like", "payload": "{}"},  # 缺 count 按缺省 0
        ]
        timeline = replay.build_timeline(events, 0, 60)
        self.assertEqual(timeline[0]["gift_count"], 1)
        self.assertEqual(timeline[0]["gift_value"], 99)
        self.assertEqual(timeline[0]["likes"], 3)

    def test_enter_and_follow_count_as_enters(self):
        events = [
            {"ts": 0, "type": "user_enter", "payload": "{}"},
            {"ts": 1, "type": "follow", "payload": "{}"},
        ]
        timeline = replay.build_timeline(events, 0, 10)
        self.assertEqual(timeline[0]["enters"], 2)

    def test_out_of_range_ts_clamped(self):
        events = [
            {"ts": -30, "type": "chat", "payload": "{}"},  # 开播前 → 钳到 0
            {"ts": 400, "type": "chat", "payload": "{}"},  # 结束后 → 钳到末桶
        ]
        timeline = replay.build_timeline(events, 0, 120)
        self.assertEqual(len(timeline), 3)
        self.assertEqual(timeline[0]["chat"], 1)
        self.assertEqual(timeline[2]["chat"], 1)

    def test_empty_or_inverted_range(self):
        self.assertEqual(replay.build_timeline([], 0, 100), [])
        self.assertEqual(replay.build_timeline([{"ts": 0, "type": "chat", "payload": "{}"}], 100, 0), [])


class DetectInsightsTest(unittest.TestCase):
    def test_empty_timeline(self):
        self.assertEqual(
            replay.detect_insights([]),
            {"peak": None, "cold_spots": [], "highlights": []},
        )

    def test_peak_is_max_total_minute(self):
        timeline = [_bucket(0, chat=2), _bucket(1, chat=8), _bucket(2, chat=1)]
        ins = replay.detect_insights(timeline)
        self.assertEqual(ins["peak"]["minute"], 1)
        self.assertEqual(ins["peak"]["total"], 8)
        self.assertIn("弹幕 8", ins["peak"]["detail"])

    def test_cold_spot_requires_two_consecutive_low_minutes(self):
        # 均值 = (2+1+1+8)/4 = 3 → 阈值 max(1, 0.9) = 1
        timeline = [
            _bucket(0, chat=2),
            _bucket(1, chat=1),
            _bucket(2, chat=1),
            _bucket(3, chat=8),
        ]
        ins = replay.detect_insights(timeline)
        self.assertEqual(ins["cold_spots"], [{"from": 1, "to": 2}])

    def test_single_low_minute_is_not_cold(self):
        timeline = [_bucket(0, chat=5), _bucket(1, chat=0), _bucket(2, chat=5)]
        ins = replay.detect_insights(timeline)
        self.assertEqual(ins["cold_spots"], [])

    def test_trailing_cold_stretch_detected(self):
        # 均值 = 12/6 = 2 → 阈值 1？max(1, 0.6)=1，total 0/1 均低于
        timeline = [
            _bucket(0, chat=5),
            _bucket(1, chat=5),
            _bucket(2, chat=1),
            _bucket(3, chat=0),
            _bucket(4, chat=1),
            _bucket(5, chat=0),
        ]
        ins = replay.detect_insights(timeline)
        self.assertEqual(ins["cold_spots"], [{"from": 2, "to": 5}])

    def test_highlights_ranked_by_weighted_score(self):
        # 第 3 分钟礼物价值高 → 加权分 200 远超纯弹幕分钟
        timeline = [
            _bucket(0, chat=30),                # 分 30
            _bucket(1, chat=10, likes=50),      # 分 10+10=20
            _bucket(2, chat=5, enters=4),       # 分 5+2=7
            _bucket(3, gift_count=1, gift_value=100),  # 分 200
            _bucket(4, chat=1),
        ]
        ins = replay.detect_insights(timeline)
        minutes = [h["minute"] for h in ins["highlights"]]
        self.assertEqual(minutes[0], 3)
        self.assertEqual(minutes[:3], [3, 0, 1])
        self.assertIn("礼物 1 笔（价值 100）", ins["highlights"][0]["detail"])

    def test_zero_activity_minutes_excluded_from_highlights(self):
        timeline = [_bucket(0, chat=3), _bucket(1), _bucket(2)]
        ins = replay.detect_insights(timeline)
        self.assertEqual([h["minute"] for h in ins["highlights"]], [0])


class AggregateSessionTest(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM actions")
        db.execute("DELETE FROM live_sessions")

    def _seed(self):
        db.execute(
            """
            INSERT INTO live_sessions (id, title, status, host_id, started_at, ended_at, metrics, created_at)
            VALUES ('s1', '测试直播', 'ended', 1, 0, 180, ?, 0)
            """,
            (json.dumps({"peak_online": 7}),),
        )
        rows = [
            ("chat", {"user": "小鹿", "text": "多少钱", "sentiment": "positive", "intent": "price_inquiry"}),
            ("chat", {"user": "奶茶", "text": "多少钱", "sentiment": "neutral", "intent": "question"}),
            ("chat", {"user": "阿杰", "text": "好用", "sentiment": "positive", "intent": "praise"}),
            ("gift", {"user": "大伟", "value": 50}),
            ("like", {"user": "小鹿", "count": 2}),
            ("user_enter", {"user": "新粉"}),
            ("follow", {"user": "铁粉"}),
        ]
        # 全部事件落在第 1 分钟，第 2-4 分钟静默 → 峰值=第 1 分钟，冷场=2-4
        ts_map = [0, 5, 10, 20, 25, 30, 35]
        for (etype, payload), ts in zip(rows, ts_map):
            db.execute(
                "INSERT INTO events (session_id, type, payload, ts) VALUES (?, ?, ?, ?)",
                ("s1", etype, json.dumps(payload), ts),
            )
        db.execute(
            "INSERT INTO actions (session_id, type, content, status, ts) VALUES ('s1', 'auto_reply', 'ok', 'executed', 1)"
        )

    def test_aggregate_returns_totals_plus_timeline_and_insights(self):
        self._seed()
        m = replay.aggregate_session("s1")
        # 原 13 键保持不变
        self.assertEqual(m["total_events"], 7)
        self.assertEqual(m["chat_count"], 3)
        self.assertEqual(m["gift_count"], 1)
        self.assertEqual(m["gift_total"], 50)
        self.assertEqual(m["like_total"], 2)
        self.assertEqual(m["follow_count"], 1)
        self.assertEqual(m["unique_users"], 6)
        self.assertEqual(m["peak_online"], 7)
        self.assertEqual(m["sentiment"], {"positive": 2, "neutral": 1, "negative": 0})
        self.assertEqual(m["top_questions"][0]["text"], "多少钱")
        self.assertEqual(m["action_count"], 1)
        self.assertEqual(m["action_breakdown"], {"auto_reply": 1})
        # 新增时间维度
        self.assertEqual(len(m["timeline"]), 4)  # 0..180s → 4 个分钟桶
        self.assertEqual(m["timeline"][0]["chat"], 3)
        self.assertEqual(m["timeline"][0]["gift_value"], 50)
        self.assertEqual(m["insights"]["peak"]["minute"], 0)
        self.assertEqual(m["insights"]["cold_spots"], [{"from": 1, "to": 3}])

    def test_empty_session(self):
        db.execute(
            """
            INSERT INTO live_sessions (id, title, status, host_id, created_at)
            VALUES ('s0', '空直播', 'ended', 1, 0)
            """
        )
        m = replay.aggregate_session("s0")
        self.assertEqual(m["total_events"], 0)
        self.assertEqual(m["timeline"], [])
        self.assertEqual(m["insights"], {"peak": None, "cold_spots": [], "highlights": []})


class ReportGenerationTest(unittest.TestCase):
    def _metrics(self, **overrides):
        base = {
            "session": {"title": "测试直播"},
            "total_events": 10,
            "chat_count": 8,
            "gift_count": 1,
            "gift_total": 50,
            "like_total": 10,
            "follow_count": 1,
            "unique_users": 5,
            "peak_online": 6,
            "sentiment": {"positive": 5, "neutral": 3, "negative": 0},
            "top_questions": [{"text": "多少钱", "count": 3}],
            "action_count": 2,
            "action_breakdown": {"auto_reply": 2},
            "timeline": [_bucket(0, chat=5, gift_count=1, gift_value=50), _bucket(1, chat=1)],
            "insights": {
                "peak": {"minute": 0, "total": 6, "detail": "弹幕 5、礼物 1 笔（价值 50）"},
                "cold_spots": [{"from": 1, "to": 1}],
                "highlights": [{"minute": 0, "detail": "弹幕 5、礼物 1 笔（价值 50）", "score": 105.0}],
            },
        }
        base.update(overrides)
        return base

    def test_template_report_contains_rhythm_section(self):
        report = replay._template_report(self._metrics())
        self.assertIn("### 节奏与时间维度", report)
        self.assertIn("时长：2 分钟", report)
        self.assertIn("互动峰值：第 1 分钟", report)
        self.assertIn("冷场时段：第 2-2 分钟", report)
        self.assertIn("高光时刻：第 1 分钟", report)

    def test_generate_report_falls_back_to_template_when_llm_unavailable(self):
        import asyncio

        async def fake_generate(system, user):
            assert "时间维度" in user  # 节奏数据已注入 prompt
            assert "峰值/冷场/高光" in system
            return None

        with patch.object(replay.llm, "generate_text", side_effect=fake_generate):
            report = asyncio.run(replay.generate_report(self._metrics()))
        self.assertIn("### 节奏与时间维度", report)

    def test_generate_report_returns_llm_text_when_available(self):
        import asyncio

        async def fake_generate(system, user):
            return "## LLM 报告"

        with patch.object(replay.llm, "generate_text", side_effect=fake_generate):
            report = asyncio.run(replay.generate_report(self._metrics()))
        self.assertEqual(report, "## LLM 报告")


if __name__ == "__main__":
    unittest.main()
