import unittest

from app.ratelimit import SlidingWindowLimiter


class SlidingWindowLimiterTest(unittest.TestCase):
    def test_allows_up_to_limit(self):
        limiter = SlidingWindowLimiter(limit=3, window_seconds=60.0)
        for i in range(3):
            allowed, _ = limiter.hit("a", ts=100.0 + i)
            self.assertTrue(allowed)

    def test_blocks_over_limit(self):
        limiter = SlidingWindowLimiter(limit=2, window_seconds=60.0)
        limiter.hit("a", ts=100.0)
        limiter.hit("a", ts=100.5)
        allowed, retry_after = limiter.hit("a", ts=101.0)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

    def test_window_slides(self):
        limiter = SlidingWindowLimiter(limit=2, window_seconds=60.0)
        limiter.hit("a", ts=100.0)
        limiter.hit("a", ts=100.5)
        self.assertFalse(limiter.hit("a", ts=101.0)[0])
        # First hit has aged out of the window -> allowed again.
        allowed, _ = limiter.hit("a", ts=160.6)
        self.assertTrue(allowed)

    def test_keys_are_isolated(self):
        limiter = SlidingWindowLimiter(limit=1, window_seconds=60.0)
        self.assertTrue(limiter.hit("a", ts=100.0)[0])
        self.assertTrue(limiter.hit("b", ts=100.0)[0])
        self.assertFalse(limiter.hit("a", ts=100.1)[0])


if __name__ == "__main__":
    unittest.main()
