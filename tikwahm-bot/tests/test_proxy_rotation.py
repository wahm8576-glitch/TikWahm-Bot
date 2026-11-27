import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("BOT_TOKEN", "123456:unit-test-token-not-for-network-use")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


class ProxyRotationTests(unittest.TestCase):
    def setUp(self):
        self.original_pool = bot._PROXY_POOL
        self.original_index = bot._ACTIVE_PROXY_INDEX
        self.original_lock = bot._PROXY_LOCK
        self.original_semaphore = bot._BROWSER_SEMAPHORE
        self.original_fetch_once = bot._fetch_once_through_proxy
        bot._PROXY_POOL = [
            {
                "host": f"proxy-{index}",
                "port": 10000 + index,
                "username": "test-user",
                "password": "test-password",
            }
            for index in range(10)
        ]
        bot._ACTIVE_PROXY_INDEX = 0
        bot._PROXY_LOCK = asyncio.Lock()
        bot._BROWSER_SEMAPHORE = asyncio.Semaphore(1)

    def tearDown(self):
        bot._PROXY_POOL = self.original_pool
        bot._ACTIVE_PROXY_INDEX = self.original_index
        bot._PROXY_LOCK = self.original_lock
        bot._BROWSER_SEMAPHORE = self.original_semaphore
        bot._fetch_once_through_proxy = self.original_fetch_once

    @staticmethod
    def _run(awaitable):
        return asyncio.run(awaitable)

    def test_connection_timeout_and_tikip_rejection_rotate_in_order(self):
        seen = []

        async def fake_fetch(username, proxy, progress=None):
            seen.append(proxy["host"])
            if len(seen) == 1:
                raise bot._ProxyConnectionBroken("connection refused")
            if len(seen) == 2:
                raise bot._TikipAttemptTimedOut("source timeout")
            if len(seen) == 3:
                raise bot._TikipRequestRejected("HTTP 403")
            return {"ok": username}

        with patch.object(bot, "_fetch_once_through_proxy", fake_fetch):
            result = self._run(bot.fetch_data_from_tikip("public_test_account"))

        self.assertEqual(seen, [f"proxy-{i}" for i in range(4)])
        self.assertEqual(result, {"ok": "public_test_account"})
        self.assertEqual(bot._ACTIVE_PROXY_INDEX, 4)

    def test_all_ten_proxies_are_tried_and_next_search_wraps_to_first(self):
        seen = []

        async def fail_every_proxy(username, proxy, progress=None):
            seen.append(proxy["host"])
            raise bot._ProxyConnectionBroken("connection timed out")

        with patch.object(bot, "_fetch_once_through_proxy", fail_every_proxy):
            result = self._run(bot.fetch_data_from_tikip("public_test_account"))

        self.assertEqual(seen, [f"proxy-{i}" for i in range(10)])
        self.assertIn("جميع البروكسيات", result)
        self.assertEqual(bot._ACTIVE_PROXY_INDEX, 0)

        next_search = []

        async def succeed_on_first(username, proxy, progress=None):
            next_search.append(proxy["host"])
            return "ok"

        with patch.object(bot, "_fetch_once_through_proxy", succeed_on_first):
            result = self._run(bot.fetch_data_from_tikip("another_public_test_account"))

        self.assertEqual(next_search, ["proxy-0"])
        self.assertEqual(result, "ok")

    def test_successful_search_advances_starting_proxy_for_next_search(self):
        seen = []

        async def succeed_immediately(username, proxy, progress=None):
            seen.append(proxy["host"])
            return "ok"

        with patch.object(bot, "_fetch_once_through_proxy", succeed_immediately):
            self.assertEqual(self._run(bot.fetch_data_from_tikip("first")), "ok")
            self.assertEqual(self._run(bot.fetch_data_from_tikip("second")), "ok")

        self.assertEqual(seen, ["proxy-0", "proxy-1"])


if __name__ == "__main__":
    unittest.main()
