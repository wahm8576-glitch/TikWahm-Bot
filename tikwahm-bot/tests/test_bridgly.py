import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:unit-test-token-not-for-network-use")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class BridglyFollowerTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            "api_key": bot.BRIDGLY_API_KEY,
            "cache": bot._FOLLOWERS_CACHE,
            "tasks": bot._FOLLOWERS_TASKS,
            "lock": bot._FOLLOWERS_LOCK,
        }
        bot.BRIDGLY_API_KEY = "unit-test-key-not-for-network"
        bot._FOLLOWERS_CACHE = bot.OrderedDict()
        bot._FOLLOWERS_TASKS = {}
        bot._FOLLOWERS_LOCK = asyncio.Lock()

    def tearDown(self):
        bot.BRIDGLY_API_KEY = self.saved["api_key"]
        bot._FOLLOWERS_CACHE = self.saved["cache"]
        bot._FOLLOWERS_TASKS = self.saved["tasks"]
        bot._FOLLOWERS_LOCK = self.saved["lock"]

    @staticmethod
    def arun(awaitable):
        return asyncio.run(awaitable)

    @staticmethod
    def callback(callback_id="followers-1", data="followers:lookup_12345678", user_id=505):
        return SimpleNamespace(
            id=callback_id,
            data=data,
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(
                message_id=444,
                chat=SimpleNamespace(id=user_id, type="private"),
            ),
        )

    def test_real_request_contract_caps_count_and_normalizes_at_most_five_names(self):
        payload = {
            "data": {
                "users": [
                    {"uniqueId": f"follower_{index}", "nickname": f"Name {index}"}
                    for index in range(7)
                ]
            }
        }
        response = FakeResponse(payload)
        with patch.object(bot.urllib.request, "urlopen", return_value=response) as urlopen:
            users = bot._request_bridgly_followers("@sample")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, bot.BRIDGLY_FOLLOWERS_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {
            "uniqueId": "sample", "count": 5,
        })
        self.assertEqual(request.get_header("Authorization"), "Bearer unit-test-key-not-for-network")
        self.assertEqual(len(users), 5)
        self.assertEqual(users[0], {"handle": "follower_0", "nickname": "Name 0"})
        self.assertEqual(users[-1]["handle"], "follower_4")

        message = bot._format_followers_message("sample", users)
        self.assertIn("قائمة المتابعين للحساب @sample", message)
        self.assertIn("@follower_0 — Name 0", message)
        self.assertIn("ترتيب القائمة بحسب المصدر غير موثّق", message)
        self.assertNotIn("الأحدث", message)
        self.assertNotIn("آخر خمسة", message)

    def test_successful_results_are_cached_and_concurrent_requests_are_coalesced(self):
        calls = []
        result = [{"handle": "one", "nickname": "One"}]

        def fake_request(username):
            calls.append(username)
            time.sleep(0.03)
            return result

        async def scenario():
            with patch.object(bot, "_request_bridgly_followers", side_effect=fake_request):
                first, second = await asyncio.gather(
                    bot._get_bridgly_followers("Sample"),
                    bot._get_bridgly_followers("sample"),
                )
                third = await bot._get_bridgly_followers("@SAMPLE")
            return first, second, third

        first, second, third = self.arun(scenario())
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, result)
        self.assertEqual(second, result)
        self.assertEqual(third, result)

    def test_missing_api_key_fails_without_network_request(self):
        bot.BRIDGLY_API_KEY = ""
        with patch.object(bot.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(bot._BridglyConfigurationError):
                bot._request_bridgly_followers("sample")
        urlopen.assert_not_called()

    def test_invalid_response_is_rejected_instead_of_fabricating_names(self):
        with patch.object(
            bot.urllib.request, "urlopen", return_value=FakeResponse({"data": {"users": "nope"}})
        ):
            with self.assertRaises(bot._BridglyRequestError):
                bot._request_bridgly_followers("sample")

    def test_callback_sends_nonchronological_list_and_handles_provider_failure(self):
        call = self.callback()
        users = [{"handle": "follower1", "nickname": "Follower One"}]
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"username": "sample"},
        ), patch.object(
            bot, "_get_bridgly_followers", new_callable=AsyncMock,
            return_value=users,
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock) as answer, \
             patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send_message:
            self.arun(bot.followers_button_callback(call))

        answer.assert_awaited_once()
        self.assertEqual(send_message.await_args.kwargs["chat_id"], 505)
        self.assertIn("@follower1 — Follower One", send_message.await_args.kwargs["text"])
        self.assertIn("غير موثّق", send_message.await_args.kwargs["text"])

        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"username": "sample"},
        ), patch.object(
            bot, "_get_bridgly_followers", new_callable=AsyncMock,
            side_effect=bot._BridglyRequestError("provider unavailable"),
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
             patch.object(bot.bot, "send_message", new_callable=AsyncMock) as failed_message:
            self.arun(bot.followers_button_callback(self.callback("followers-2")))

        self.assertEqual(
            failed_message.await_args.kwargs["text"],
            "تعذر جلب قائمة المتابعين حاليًا. حاول مرة أخرى لاحقًا.",
        )

    def test_missing_key_callback_explains_feature_is_unavailable_without_fetching(self):
        bot.BRIDGLY_API_KEY = ""
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"username": "sample"},
        ), patch.object(
            bot, "_get_bridgly_followers", new_callable=AsyncMock,
        ) as fetch, patch.object(
            bot.bot, "answer_callback_query", new_callable=AsyncMock,
        ) as answer:
            self.arun(bot.followers_button_callback(self.callback()))

        fetch.assert_not_awaited()
        self.assertTrue(answer.await_args.kwargs["show_alert"])
        self.assertIn("غير مهيأة", answer.await_args.kwargs["text"])


if __name__ == "__main__":
    unittest.main()
