import asyncio
import os
import sys
import time
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("BOT_TOKEN", "123456:unit-test-token-not-for-network-use")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


class InlineModeTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            "lookup_cache": bot._LOOKUP_CACHE,
            "lookup_tasks": bot._LOOKUP_TASKS,
            "latest": bot._INLINE_QUERY_LATEST,
            "last_search_at": bot._INLINE_QUERY_LAST_SEARCH_AT,
            "inline_lock": bot._INLINE_QUERY_LOCK,
        }
        bot._LOOKUP_CACHE = {}
        bot._LOOKUP_TASKS = {}
        bot._INLINE_QUERY_LATEST = OrderedDict()
        bot._INLINE_QUERY_LAST_SEARCH_AT = OrderedDict()
        bot._INLINE_QUERY_LOCK = asyncio.Lock()

    def tearDown(self):
        bot._LOOKUP_CACHE = self.saved["lookup_cache"]
        bot._LOOKUP_TASKS = self.saved["lookup_tasks"]
        bot._INLINE_QUERY_LATEST = self.saved["latest"]
        bot._INLINE_QUERY_LAST_SEARCH_AT = self.saved["last_search_at"]
        bot._INLINE_QUERY_LOCK = self.saved["inline_lock"]

    @staticmethod
    def arun(awaitable):
        return asyncio.run(awaitable)

    @staticmethod
    def query(query_id="inline-1", text="sample", user_id=101, language="ar"):
        return SimpleNamespace(
            id=query_id,
            query=text,
            from_user=SimpleNamespace(id=user_id, language_code=language),
        )

    @staticmethod
    def answer_result(answer):
        return answer.await_args.args[1][0]

    def test_empty_query_returns_usage_hint_without_searching(self):
        query = self.query(text="")
        with patch.object(bot.bot, "answer_inline_query", new_callable=AsyncMock) as answer, \
             patch.object(bot, "_get_or_start_lookup") as lookup:
            self.arun(bot.inline_query_handler(query))

        answer.assert_awaited_once()
        lookup.assert_not_called()
        result = self.answer_result(answer)
        self.assertIn("اكتب اسم مستخدم", result.input_message_content.message_text)
        self.assertEqual(answer.await_args.kwargs["is_personal"], False)

    def test_inline_result_shares_only_public_text_not_locked_details(self):
        public_text = "معلومات TikTok العامة\nاسم المستخدم: @sample\nالمتابعون | 123"
        result_data = {
            "text": "public display with private security details and Canada",
            "inline_public": public_text,
            "markdown": "public display with private controls",
            "original_country": "كندا — Canada — مصدر خاص",
            "followers": ["locked follower"],
        }
        bot._LOOKUP_CACHE["sample"] = (time.monotonic() + 60, result_data)
        query = self.query()
        with patch.object(bot.bot, "answer_inline_query", new_callable=AsyncMock) as answer:
            self.arun(bot.inline_query_handler(query))

        result = self.answer_result(answer)
        sent_text = result.input_message_content.message_text
        self.assertEqual(sent_text, public_text)
        self.assertNotIn("Canada", sent_text)
        self.assertNotIn("locked follower", sent_text)
        self.assertNotIn("private controls", sent_text)
        self.assertEqual(answer.await_args.kwargs["cache_time"], bot.INLINE_QUERY_CACHE_SECONDS)
        self.assertEqual(answer.await_args.kwargs["is_personal"], False)
        self.assertIn("@sample", result.title)

    def test_allowlisted_profile_never_includes_sensitive_fields(self):
        profile = {
            "username": "sample", "fullname": "Public Name", "followers": 1234,
            "following": 120, "likes": 500, "private": False, "userid": "987654321",
            "language": "en-US", "created_time": "2021-03-07 16:05:06",
            "last_change_name": "2024-11-29 09:08:07",
            "original_country": "SECRET_COUNTRY", "security": "SECRET_SECURITY",
            "followers_list": ["SECRET_FOLLOWER"], "active_region": "SECRET_REGION",
            "registered_region": "SECRET_REGISTERED_REGION", "secUid": "SECRET_SECUID",
            "security_status": "SECRET_SECURITY_STATUS",
        }
        text = bot._inline_public_profile("sample", profile)
        for token in (
            "SECRET_COUNTRY", "SECRET_SECURITY", "SECRET_FOLLOWER", "SECRET_REGION",
            "SECRET_REGISTERED_REGION", "SECRET_SECUID", "SECRET_SECURITY_STATUS",
        ):
            self.assertNotIn(token, text)
        self.assertIn("Public Name", text)
        self.assertIn("@sample", text)
        self.assertIn("المتابعون", text)
        for expected in (
            "يتابع | 120", "المتابعون | 1.23K", "الإعجابات | 500",
            "معرّف المستخدم | 987654321", "نوع الحساب | عام (Public Account)",
            "اللغة | الإنجليزية (en-US)", "وقت إنشاء الحساب | 2021-03-07 16:05:06",
            "آخر تغيير للاسم | 2024-11-29 09:08:07",
        ):
            self.assertIn(expected, text)
        ordered_labels = [
            "يتابع", "المتابعون", "الإعجابات", "معرّف المستخدم", "نوع الحساب",
            "اللغة", "وقت إنشاء الحساب", "آخر تغيير للاسم",
        ]
        positions = [text.index(label) for label in ordered_labels]
        self.assertEqual(positions, sorted(positions))

    def test_invalid_query_does_not_start_a_lookup(self):
        query = self.query(text="sample name")
        with patch.object(bot.bot, "answer_inline_query", new_callable=AsyncMock) as answer, \
             patch.object(bot, "_get_or_start_lookup") as lookup:
            self.arun(bot.inline_query_handler(query))

        answer.assert_awaited_once()
        lookup.assert_not_called()
        self.assertIn("غير صالح", self.answer_result(answer).title)

    def test_rapidly_changed_queries_only_search_latest_query(self):
        bot.INLINE_QUERY_DEBOUNCE_SECONDS = 0.01
        bot._LOOKUP_CACHE["sample"] = (
            time.monotonic() + 60,
            {"inline_public": "Public profile for @sample", "original_country": "locked"},
        )
        first = self.query(query_id="old-query", text="sam")
        latest = self.query(query_id="latest-query", text="sample")
        async def scenario():
            with patch.object(bot.bot, "answer_inline_query", new_callable=AsyncMock) as answer, \
             patch.object(bot, "_get_or_start_lookup", return_value=(
                     {"inline_public": "Public profile for @sample"}, None, True
                 )) as lookup:
                await asyncio.gather(
                    bot.inline_query_handler(first),
                    bot.inline_query_handler(latest),
                )
            return answer, lookup

        answer, lookup = self.arun(scenario())
        self.assertEqual(answer.await_count, 1)
        self.assertEqual(answer.await_args.args[0], "latest-query")
        self.assertEqual(lookup.call_count, 1)

    def test_inline_search_cooldown_is_short_and_does_not_extend_when_blocked(self):
        async def scenario():
            first = await bot._reserve_inline_query_search(101, now=100)
            blocked = await bot._reserve_inline_query_search(101, now=102.5)
            still_blocked = await bot._reserve_inline_query_search(101, now=102.8)
            allowed = await bot._reserve_inline_query_search(101, now=103)
            return first, blocked, still_blocked, allowed

        self.assertEqual(self.arun(scenario()), (0, 1, 1, 0))
        self.assertEqual(bot.INLINE_QUERY_COOLDOWN_SECONDS, 3)


if __name__ == "__main__":
    unittest.main()
