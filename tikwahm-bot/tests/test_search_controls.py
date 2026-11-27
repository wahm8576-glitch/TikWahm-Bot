import asyncio
import os
import sys
import unittest
from collections import OrderedDict
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:unit-test-token-not-for-network-use")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


class SearchControlsTests(unittest.TestCase):
    def setUp(self):
        self.original_last_at = bot._USER_SEARCH_LAST_AT
        self.original_lock = bot._USER_SEARCH_COOLDOWN_LOCK
        bot._USER_SEARCH_LAST_AT = OrderedDict()
        bot._USER_SEARCH_COOLDOWN_LOCK = asyncio.Lock()

    def tearDown(self):
        bot._USER_SEARCH_LAST_AT = self.original_last_at
        bot._USER_SEARCH_COOLDOWN_LOCK = self.original_lock

    @staticmethod
    def arun(awaitable):
        return asyncio.run(awaitable)

    def test_progress_reaches_100_in_ten_seconds_and_stays_complete(self):
        before_completion = bot._wait_progress_for_elapsed("sample", 9.9)
        completed = bot._wait_progress_for_elapsed("sample", 10)
        grace_period = bot._wait_progress_for_elapsed("sample", 14.9)

        self.assertIn("٩٩٪", before_completion)
        self.assertIn("١٠٠٪", completed)
        self.assertIn("١٠٠٪", grace_period)
        self.assertIn("جارٍ إكمال البحث", grace_period)
        self.assertNotIn("ثانية", grace_period)
        self.assertIn("٠٪", bot._wait_progress_for_elapsed("sample", -1))

    def test_cooldown_is_per_user_and_expires_after_fifteen_seconds(self):
        async def scenario():
            first_user = await bot._reserve_user_search(101, now=100)
            same_user_early = await bot._reserve_user_search(101, now=104)
            other_user = await bot._reserve_user_search(202, now=104)
            almost_ready = await bot._reserve_user_search(101, now=114.999)
            ready = await bot._reserve_user_search(101, now=115)
            return first_user, same_user_early, other_user, almost_ready, ready

        result = self.arun(scenario())
        self.assertEqual(result, (0, 11, 0, 1, 0))

    def test_blocked_attempt_does_not_extend_cooldown(self):
        async def scenario():
            await bot._reserve_user_search("user-a", now=50)
            blocked = await bot._reserve_user_search("user-a", now=60)
            still_blocked = await bot._reserve_user_search("user-a", now=64)
            allowed = await bot._reserve_user_search("user-a", now=65)
            return blocked, still_blocked, allowed

        self.assertEqual(self.arun(scenario()), (5, 1, 0))

    def test_concurrent_requests_from_one_user_reserve_only_one_slot(self):
        async def scenario():
            return await asyncio.gather(
                bot._reserve_user_search("same-user", now=100),
                bot._reserve_user_search("same-user", now=100),
            )

        self.assertEqual(sorted(self.arun(scenario())), [0, 15])


if __name__ == "__main__":
    unittest.main()
