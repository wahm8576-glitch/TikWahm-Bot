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


class BridglyListTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            "api_key": bot.BRIDGLY_API_KEY,
            "cache": bot._BRIDGLY_LIST_CACHE,
            "tasks": bot._BRIDGLY_LIST_TASKS,
            "lock": bot._BRIDGLY_LIST_LOCK,
        }
        bot.BRIDGLY_API_KEY = "unit-test-key-not-for-network"
        bot._BRIDGLY_LIST_CACHE = bot.OrderedDict()
        bot._BRIDGLY_LIST_TASKS = {}
        bot._BRIDGLY_LIST_LOCK = asyncio.Lock()

    def tearDown(self):
        bot.BRIDGLY_API_KEY = self.saved["api_key"]
        bot._BRIDGLY_LIST_CACHE = self.saved["cache"]
        bot._BRIDGLY_LIST_TASKS = self.saved["tasks"]
        bot._BRIDGLY_LIST_LOCK = self.saved["lock"]

    @staticmethod
    def arun(awaitable):
        return asyncio.run(awaitable)

    @staticmethod
    def callback(list_kind="followers", callback_id="list-1", user_id=505):
        return SimpleNamespace(
            id=callback_id,
            data=f"{list_kind}:lookup_12345678",
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(
                message_id=444,
                chat=SimpleNamespace(id=user_id, type="private"),
            ),
        )

    @staticmethod
    def sample_users(prefix="user"):
        return [
            {
                "handle": f"{prefix}_{index}",
                "nickname": f"Name {index}",
                "avatar_url": f"https://p16-sign-va.tiktokcdn.com/{prefix}_{index}.webp",
            }
            for index in range(5)
        ]

    def test_both_api_directions_request_five_and_render_name_handle_and_avatar(self):
        cases = (
            ("followers", bot.BRIDGLY_FOLLOWERS_URL, bot._format_bridgly_list_text),
            ("following", bot.BRIDGLY_FOLLOWING_URL, bot._format_bridgly_list_text),
        )
        for list_kind, endpoint, formatter in cases:
            with self.subTest(list_kind=list_kind):
                response = FakeResponse({
                    "data": {
                        "users": [
                            {
                                "uniqueId": f"person_{index}",
                                "nickname": f"Display {index}",
                                "avatarUrl": f"https://p16-sign-va.tiktokcdn.com/person_{index}.webp",
                            }
                            for index in range(7)
                        ]
                    }
                })
                with patch.object(bot.urllib.request, "urlopen", return_value=response) as urlopen:
                    users = bot._request_bridgly_social_list("@menharef", list_kind)

                request = urlopen.call_args.args[0]
                request_body = json.loads(request.data.decode("utf-8"))
                self.assertEqual(request.full_url, endpoint)
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request_body, {
                    "uniqueId": "menharef",
                    "count": 5,
                    "cacheMaxAgeHours": 24,
                })
                self.assertEqual(
                    request.get_header("Authorization"),
                    "Bearer unit-test-key-not-for-network",
                )
                self.assertEqual(len(users), 5)
                self.assertEqual(users[0], {
                    "handle": "person_0",
                    "nickname": "Display 0",
                    "avatar_url": "https://p16-sign-va.tiktokcdn.com/person_0.webp",
                })

                text = formatter("menharef", users, list_kind)
                rich = bot._format_bridgly_list_rich_message("menharef", users, list_kind)
                expected_heading = "قائمة المتابعين" if list_kind == "followers" else "قائمة الحسابات التي يتابعها"
                self.assertIn(expected_heading, text)
                self.assertIn("الحساب @menharef", text)
                self.assertNotIn("غير موثّق", text)
                self.assertNotIn("الأحدث", text)
                self.assertNotIn("آخر خمسة", text)
                self.assertIsInstance(rich, bot.types.InputRichMessage)
                self.assertEqual(rich.blocks[0].text, expected_heading)
                self.assertEqual(rich.blocks[1].text, "الحساب @menharef")
                photo_blocks = [block for block in rich.blocks if isinstance(block, bot.types.InputRichBlockPhoto)]
                self.assertEqual(len(photo_blocks), 5)
                self.assertEqual(photo_blocks[0].photo.media, users[0]["avatar_url"])
                self.assertIn("Display 0", photo_blocks[0].caption.text)
                self.assertIn("@person_0", photo_blocks[0].caption.text)

    def test_plain_fallback_has_neutral_heading_and_no_recency_or_source_disclaimer(self):
        text = bot._format_bridgly_list_text(
            "menharef",
            [{"handle": "lostl09", "nickname": "𝓐"}],
            "followers",
        )
        self.assertIn("قائمة المتابعين", text)
        self.assertIn("𝓐", text)
        self.assertIn("@lostl09", text)
        self.assertNotIn("غير موثّق", text)
        self.assertNotIn("الأحدث", text)
        self.assertNotIn("آخر خمسة", text)

    def test_empty_list_does_not_claim_account_follows_nobody(self):
        text = bot._format_bridgly_list_text("menharef", [], "following")
        self.assertIn("لم يُعد المصدر أسماءً لهذه القائمة", text)
        self.assertNotIn("لا يتابع أحدًا", text)
        rich = bot._format_bridgly_list_rich_message("menharef", [], "following")
        self.assertEqual(rich.blocks[-1].text, "لم يُعد المصدر أسماءً لهذه القائمة.")

    def test_successful_results_are_cached_and_concurrent_requests_coalesced_per_direction(self):
        calls = []
        followers = self.sample_users("follower")
        following = self.sample_users("following")

        def fake_followers(username):
            calls.append(("followers", username))
            time.sleep(0.03)
            return followers

        def fake_following(username):
            calls.append(("following", username))
            return following

        async def scenario():
            with patch.object(bot, "_request_bridgly_followers", side_effect=fake_followers), \
                 patch.object(bot, "_request_bridgly_following", side_effect=fake_following):
                first, second = await asyncio.gather(
                    bot._get_bridgly_followers("Sample"),
                    bot._get_bridgly_followers("sample"),
                )
                third = await bot._get_bridgly_followers("@SAMPLE")
                followed = await bot._get_bridgly_following("sample")
            return first, second, third, followed

        first, second, third, followed = self.arun(scenario())
        self.assertEqual(calls, [("followers", "Sample"), ("following", "sample")])
        self.assertEqual(first, followers)
        self.assertEqual(second, followers)
        self.assertEqual(third, followers)
        self.assertEqual(followed, following)

    def test_missing_api_key_fails_without_network_request(self):
        bot.BRIDGLY_API_KEY = ""
        with patch.object(bot.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(bot._BridglyConfigurationError):
                bot._request_bridgly_social_list("sample", "followers")
        urlopen.assert_not_called()

    def test_invalid_response_is_rejected_instead_of_fabricating_names(self):
        with patch.object(
            bot.urllib.request,
            "urlopen",
            return_value=FakeResponse({"data": {"users": "nope"}}),
        ):
            with self.assertRaises(bot._BridglyRequestError):
                bot._request_bridgly_social_list("sample", "followers")

    def test_both_buttons_call_the_correct_direction_and_send_rich_list(self):
        for list_kind, handler in (
            ("followers", bot.followers_button_callback),
            ("following", bot.following_button_callback),
        ):
            with self.subTest(list_kind=list_kind):
                users = self.sample_users(list_kind)
                call = self.callback(list_kind=list_kind, callback_id=f"{list_kind}-callback")
                with patch.object(
                    bot, "_get_country_lookup", new_callable=AsyncMock,
                    return_value={"username": "menharef"},
                ), patch.object(
                    bot, "_get_bridgly_social_list", new_callable=AsyncMock,
                    return_value=users,
                ) as fetch, patch.object(
                    bot.bot, "answer_callback_query", new_callable=AsyncMock,
                ), patch.object(
                    bot.bot, "send_rich_message", new_callable=AsyncMock,
                ) as send_rich, patch.object(
                    bot.bot, "send_message", new_callable=AsyncMock,
                ) as send_text:
                    self.arun(handler(call))

                fetch.assert_awaited_once_with("menharef", list_kind)
                rich = send_rich.await_args.kwargs["rich_message"]
                self.assertEqual(rich.blocks[0].text, "قائمة المتابعين" if list_kind == "followers" else "قائمة الحسابات التي يتابعها")
                self.assertEqual(len([b for b in rich.blocks if isinstance(b, bot.types.InputRichBlockPhoto)]), 5)
                send_text.assert_not_awaited()

    def test_following_callback_handles_private_or_hidden_list_without_claiming_empty(self):
        call = self.callback(list_kind="following", callback_id="following-private")
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"username": "menharef"},
        ), patch.object(
            bot, "_get_bridgly_social_list", new_callable=AsyncMock,
            side_effect=bot._BridglyPrivateAccountError("private", status=403),
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
             patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send_message:
            self.arun(bot.following_button_callback(call))
        self.assertIn("حساب TikTok خاص", send_message.await_args.kwargs["text"])

    def test_callback_handles_out_of_credits_without_attempting_a_purchase(self):
        call = self.callback(list_kind="following", callback_id="following-no-credits")
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"username": "menharef"},
        ), patch.object(
            bot, "_get_bridgly_social_list", new_callable=AsyncMock,
            side_effect=bot._BridglyRequestError("credits exhausted", status=402),
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
             patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send_message:
            self.arun(bot.following_button_callback(call))
        self.assertIn("لا يتوفر رصيد Bridgly مجاني", send_message.await_args.kwargs["text"])

    def test_missing_key_callback_explains_feature_is_unavailable_without_fetching(self):
        bot.BRIDGLY_API_KEY = ""
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"username": "menharef"},
        ), patch.object(
            bot, "_get_bridgly_social_list", new_callable=AsyncMock,
        ) as fetch, patch.object(
            bot.bot, "answer_callback_query", new_callable=AsyncMock,
        ) as answer:
            self.arun(bot.following_button_callback(self.callback(list_kind="following")))

        fetch.assert_not_awaited()
        self.assertTrue(answer.await_args.kwargs["show_alert"])
        self.assertIn("غير مهيأة", answer.await_args.kwargs["text"])


if __name__ == "__main__":
    unittest.main()
