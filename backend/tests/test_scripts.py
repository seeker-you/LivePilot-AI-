import asyncio
import unittest

from app import play_queue, script_manager
from app.db import db


class ScriptManagerTest(unittest.TestCase):
    def setUp(self):
        self._ids = []
        # Isolate weighted_pick from seeded/leftover scripts: disable them and
        # restore their exact enabled state afterwards.
        self._original = [
            (r["id"], int(bool(r["enabled"]))) for r in script_manager.list_scripts()
        ]
        db.execute("UPDATE scripts SET enabled = 0")
        script_manager._recent.clear()

    def tearDown(self):
        for script_id in self._ids:
            db.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
        for script_id, enabled in self._original:
            db.execute(
                "UPDATE scripts SET enabled = ? WHERE id = ?", (enabled, script_id)
            )
        script_manager._recent.clear()

    def _create(self, title: str, content: str = "测试内容") -> dict:
        item = script_manager.create_script(title, content, "text", ["测试"])
        self._ids.append(item["id"])
        return item

    def test_create_list_delete(self):
        item = self._create("测试话术A")
        titles = [s["title"] for s in script_manager.list_scripts()]
        self.assertIn("测试话术A", titles)
        self.assertTrue(script_manager.delete_script(item["id"]))
        self.assertIsNone(script_manager.get_script(item["id"]))

    def test_update_toggle(self):
        item = self._create("测试话术B")
        updated = script_manager.update_script(item["id"], {"enabled": False})
        self.assertFalse(updated["enabled"])
        updated = script_manager.update_script(item["id"], {"title": "改标题"})
        self.assertEqual(updated["title"], "改标题")

    def test_weighted_pick_increments_and_avoids_recent(self):
        a = self._create("测试A")
        b = self._create("测试B")
        first = script_manager.weighted_pick()
        second = script_manager.weighted_pick()
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(script_manager.get_script(a["id"])["play_count"], 1)
        self.assertEqual(script_manager.get_script(b["id"])["play_count"], 1)


class PlayQueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_high_priority_pops_before_low(self):
        queue = play_queue.PlayQueue()
        await queue.put_low(play_queue.QueueItem(content="话术补位"))
        await queue.put_high("感谢礼物", source="gift")
        first = await queue.pop_next()
        second = await queue.pop_next()
        self.assertEqual(first.content, "感谢礼物")
        self.assertEqual(first.level, "high")
        self.assertEqual(second.content, "话术补位")

    async def test_snapshot_peeks_next(self):
        queue = play_queue.PlayQueue()
        await queue.put_low(play_queue.QueueItem(content="低优"))
        snap = queue.snapshot_sync()
        self.assertEqual(snap["next"]["content"], "低优")

    async def test_auto_fill_uses_scripts(self):
        # Seed one script so the filler has something to pick.
        item = script_manager.create_script("自动填充", "填充内容", "text", ["测试"])
        try:
            queue = play_queue.PlayQueue()
            await queue.start_auto_fill(min_size=1, interval=0.05)
            try:
                await asyncio.sleep(0.12)
            finally:
                await queue.stop_auto_fill()
            snap = queue.snapshot_sync()
            self.assertGreaterEqual(len(snap["low"]), 1)
        finally:
            script_manager.delete_script(item["id"])
            script_manager._recent.clear()


if __name__ == "__main__":
    unittest.main()
