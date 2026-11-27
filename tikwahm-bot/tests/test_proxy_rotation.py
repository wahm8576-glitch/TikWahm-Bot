import asyncio
import json
import os
import sys
import urllib.error
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
        self.original_semaphore = bot._SOURCE_SEMAPHORE
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
        bot._SOURCE_SEMAPHORE = asyncio.Semaphore(3)

    def tearDown(self):
        bot._PROXY_POOL = self.original_pool
        bot._ACTIVE_PROXY_INDEX = self.original_index
        bot._PROXY_LOCK = self.original_lock
        bot._SOURCE_SEMAPHORE = self.original_semaphore
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


class DirectTikipRequestTests(unittest.TestCase):
    proxy = {
        "host": "proxy.example",
        "port": 8080,
        "username": "test-user",
        "password": "test-pass",
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return 200

        def read(self, _limit):
            return json.dumps(self.payload).encode("utf-8")

    class FakeOpener:
        def __init__(self, payload):
            self.payload = payload
            self.request = None
            self.timeout = None

        def open(self, request, timeout):
            self.request = request
            self.timeout = timeout
            return DirectTikipRequestTests.FakeResponse(self.payload)

    def test_fetches_json_endpoint_directly_through_proxy(self):
        payload = {
            "status": "success",
            "data": {
                "user": {"uniqueId": "tiktok", "nickname": "TikTok", "id": "123"},
                "stats": {"followerCount": 1, "heartCount": 2, "videoCount": 3},
            },
        }
        opener = self.FakeOpener(payload)
        with patch.object(bot.urllib.request, "build_opener", return_value=opener):
            result = ProxyRotationTests._run(
                bot._fetch_once_through_proxy("tiktok", self.proxy)
            )

        self.assertIn("/api/v1/profile?username=tiktok", opener.request.full_url)
        self.assertEqual(opener.timeout, bot.SOURCE_TIMEOUT_SECONDS)
        self.assertIn("TikTok", result["text"])

    def test_http_throttle_is_marked_retryable(self):
        class ThrottledOpener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(
                    request.full_url, 429, "Too Many Requests", {}, None
                )

        with patch.object(
            bot.urllib.request, "build_opener", return_value=ThrottledOpener()
        ):
            with self.assertRaises(bot._TikipRequestRejected):
                bot._request_tikip_payload("tiktok", self.proxy)

    def test_proxy_timeout_and_connection_error_are_classified_for_rotation(self):
        class FailingOpener:
            def __init__(self, reason):
                self.reason = reason

            def open(self, _request, timeout):
                raise urllib.error.URLError(self.reason)

        cases = [
            (TimeoutError("timed out"), bot._TikipAttemptTimedOut),
            (ConnectionRefusedError("connection refused"), bot._ProxyConnectionBroken),
        ]
        for reason, expected_error in cases:
            with self.subTest(expected_error=expected_error.__name__):
                with patch.object(
                    bot.urllib.request,
                    "build_opener",
                    return_value=FailingOpener(reason),
                ):
                    with self.assertRaises(expected_error):
                        bot._request_tikip_payload("tiktok", self.proxy)


if __name__ == "__main__":
    unittest.main()
