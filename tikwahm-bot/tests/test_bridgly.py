import asyncio
import json
import os
import sys
import threading
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
            "avatar_cache": bot._AVATAR_THUMBNAIL_CACHE,
            "avatar_semaphore": bot._AVATAR_THUMBNAIL_SEMAPHORE,
        }
        bot.BRIDGLY_API_KEY = "unit-test-key-not-for-network"
        bot._BRIDGLY_LIST_CACHE = bot.OrderedDict()
        bot._BRIDGLY_LIST_TASKS = {}
        bot._BRIDGLY_LIST_LOCK = asyncio.Lock()
        bot._AVATAR_THUMBNAIL_CACHE = bot.OrderedDict()
        bot._AVATAR_THUMBNAIL_SEMAPHORE = asyncio.Semaphore(3)

    def tearDown(self):
        bot.BRIDGLY_API_KEY = self.saved["api_key"]
        bot._BRIDGLY_LIST_CACHE = self.saved["cache"]
        bot._BRIDGLY_LIST_TASKS = self.saved["tasks"]
        bot._BRIDGLY_LIST_LOCK = self.saved["lock"]
        bot._AVATAR_THUMBNAIL_CACHE = self.saved["avatar_cache"]
        bot._AVATAR_THUMBNAIL_SEMAPHORE = self.saved["avatar_semaphore"]

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
            for index in range(bot.MAX_FOLLOWER_NAMES)
        ]

    def test_both_api_directions_request_thirteen_and_render_name_handle_and_avatar(self):
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
                            for index in range(16)
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
                    "count": 13,
                    "cacheMaxAgeHours": 24,
                })
                self.assertEqual(
                    request.get_header("Authorization"),
                    "Bearer unit-test-key-not-for-network",
                )
                self.assertEqual(len(users), 13)
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
                self.assertEqual(len(photo_blocks), 13)
                divider_blocks = [block for block in rich.blocks if isinstance(block, bot.types.InputRichBlockDivider)]
                self.assertEqual(len(divider_blocks), 12)
                self.assertEqual(photo_blocks[0].photo.media, users[0]["avatar_url"])
                self.assertEqual(photo_blocks[0].caption.text[0].text, "Display 0")
                self.assertEqual(photo_blocks[0].caption.text[2], "@person_0")

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
        self.assertIn("١. 👤 𝓐", text)
        self.assertIn("────────────", bot._format_bridgly_list_text(
            "menharef",
            [{"handle": "one", "nickname": "First"}, {"handle": "two", "nickname": "Second"}],
            "followers",
        ))

    def test_avatar_thumbnail_is_small_jpeg_and_rejects_untrusted_hosts(self):
        from io import BytesIO
        from PIL import Image

        image_buffer = BytesIO()
        Image.new("RGB", (500, 320), "red").save(image_buffer, format="PNG")
        image_payload = image_buffer.getvalue()

        class FakeAvatarResponse:
            headers = SimpleNamespace(get_content_type=lambda: "image/png")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://p16-sign-va.tiktokcdn.com/avatar.png"

            def read(self, _limit):
                return image_payload

        class FakeOpener:
            def open(self, _request, timeout):
                self.timeout = timeout
                return FakeAvatarResponse()

        opener = FakeOpener()
        with patch.object(bot.urllib.request, "build_opener", return_value=opener):
            thumbnail = bot._create_avatar_thumbnail(
                "https://p16-sign-va.tiktokcdn.com/avatar.png"
            )
        resized = Image.open(BytesIO(thumbnail))
        self.assertEqual(resized.size, (bot.AVATAR_THUMBNAIL_SIZE,) * 2)
        self.assertEqual(resized.format, "JPEG")
        self.assertLess(len(thumbnail), len(image_payload))
        with patch.object(bot.urllib.request, "build_opener") as build_opener:
            self.assertIsNone(bot._create_avatar_thumbnail("https://example.com/avatar.png"))
        build_opener.assert_not_called()

    def test_avatar_thumbnail_rejects_excessive_pixel_count_before_decoding(self):
        image = SimpleNamespace(width=2001, height=2000, seek=lambda _index: None)
        image.convert = unittest.mock.Mock(side_effect=AssertionError("should not decode"))
        image_context = unittest.mock.MagicMock()
        image_context.__enter__.return_value = image
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://p16.tiktokcdn.com/a.png"
        response.headers.get_content_type.return_value = "image/png"
        response.read.return_value = b"fake image"
        opener = SimpleNamespace(open=lambda *_args, **_kwargs: response)
        with patch.object(bot.urllib.request, "build_opener", return_value=opener), \
             patch.object(bot.Image, "open", return_value=image_context):
            self.assertIsNone(bot._create_avatar_thumbnail("https://p16.tiktokcdn.com/a.png"))
        image.convert.assert_not_called()

    def test_avatar_thumbnail_downloads_are_globally_bounded(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_thumbnail(_url):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return b"tiny-jpeg"

        async def scenario():
            with patch.object(bot, "_create_avatar_thumbnail", side_effect=fake_thumbnail):
                return await asyncio.gather(*[
                    bot._create_avatar_thumbnail_bounded(str(i)) for i in range(10)
                ])

        self.assertEqual(self.arun(scenario()), [b"tiny-jpeg"] * 10)
        self.assertLessEqual(peak, 3)

    def test_composited_list_image_is_a_single_jpeg_and_caps_rows_at_thirteen(self):
        from io import BytesIO
        from PIL import Image

        users = self.sample_users() + [{"handle": "extra", "nickname": "Extra"}]
        avatar_buffer = BytesIO()
        Image.new("RGB", (88, 88), "teal").save(avatar_buffer, format="JPEG")
        card = bot._render_bridgly_list_image(
            "menharef", users, "followers", [avatar_buffer.getvalue()] * 14
        )
        rendered = Image.open(BytesIO(card))
        self.assertEqual(rendered.format, "JPEG")
        self.assertEqual(rendered.size, (1080, 2402))

    def test_free_preview_has_three_real_rows_and_ten_labeled_placeholder_rows(self):
        from io import BytesIO
        from PIL import Image

        users = self.sample_users("preview")[:3]
        avatar_buffer = BytesIO()
        Image.new("RGB", (88, 88), "teal").save(avatar_buffer, format="JPEG")
        card = bot._render_bridgly_list_image(
            "menharef", users, "followers", [avatar_buffer.getvalue()] * 3,
            locked_count=10,
        )
        rendered = Image.open(BytesIO(card))
        self.assertEqual(rendered.size, (1080, 2494))
        self.assertEqual(rendered.format, "JPEG")

    def test_avatar_thumbnail_route_serves_only_short_lived_cached_images(self):
        async def scenario():
            with patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "https://tikwahm-bot-new.onrender.com"}), \
                 patch.object(bot, "_create_avatar_thumbnail", return_value=b"tiny-jpeg"):
                prepared = await bot._prepare_bridgly_list_avatars([
                    {"handle": "one", "nickname": "One", "avatar_url": "https://p16-sign-va.tiktokcdn.com/avatar.png"}
                ])
            return prepared

        prepared = self.arun(scenario())
        self.assertRegex(prepared[0]["avatar_url"], r"^https://tikwahm-bot-new\.onrender\.com/avatar-thumb/[A-Za-z0-9_-]+\.jpg$")
        token = prepared[0]["avatar_url"].rsplit("/", 1)[-1].removesuffix(".jpg")
        response = self.arun(bot.avatar_thumbnail(token))
        self.assertEqual(response.media_type, "image/jpeg")
        self.assertEqual(response.body, b"tiny-jpeg")

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

        def fake_followers(username, count):
            calls.append(("followers", username, count))
            time.sleep(0.03)
            return followers[:count]

        def fake_following(username, count):
            calls.append(("following", username, count))
            return following[:count]

        async def scenario():
            with patch.object(bot, "_request_bridgly_followers", side_effect=fake_followers), \
                 patch.object(bot, "_request_bridgly_following", side_effect=fake_following):
                first, second = await asyncio.gather(
                    bot._get_bridgly_followers("Sample"),
                    bot._get_bridgly_followers("sample"),
                )
                third = await bot._get_bridgly_followers("@SAMPLE")
                followed = await bot._get_bridgly_following("sample")
                limited = await bot._get_bridgly_followers("sample", 3)
            return first, second, third, followed, limited

        first, second, third, followed, limited = self.arun(scenario())
        self.assertEqual(calls, [
            ("followers", "Sample", 13),
            ("following", "sample", 13),
            ("followers", "sample", 3),
        ])
        self.assertEqual(first, followers)
        self.assertEqual(second, followers)
        self.assertEqual(third, followers)
        self.assertEqual(followed, following)
        self.assertEqual(limited, followers[:3])

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

    def test_both_buttons_call_the_correct_direction_and_send_composited_photo(self):
        for list_kind, handler in (
            ("followers", bot.followers_button_callback),
            ("following", bot.following_button_callback),
        ):
            with self.subTest(list_kind=list_kind):
                users = self.sample_users(list_kind)
                call = self.callback(list_kind=list_kind, callback_id=f"{list_kind}-callback")
                with patch.dict(os.environ, {"RENDER_EXTERNAL_URL": ""}), patch.object(
                    bot, "_get_country_lookup", new_callable=AsyncMock,
                    return_value={"username": "menharef"},
                ), patch.object(
                    bot, "_get_country_access_settings", new_callable=AsyncMock,
                    return_value={"mode": "paid", "stars_price": 17},
                ), patch.object(
                    bot, "_has_active_country_subscription", new_callable=AsyncMock,
                    return_value=True,
                ), patch.object(
                    bot, "_get_bridgly_social_list", new_callable=AsyncMock,
                    return_value=users,
                ) as fetch, patch.object(
                    bot, "_create_bridgly_list_image", new_callable=AsyncMock,
                    return_value=b"one-composited-jpeg",
                ) as render_image, patch.object(
                    bot.bot, "answer_callback_query", new_callable=AsyncMock,
                ), patch.object(
                    bot.bot, "send_photo", new_callable=AsyncMock,
                ) as send_photo, patch.object(
                    bot.bot, "send_message", new_callable=AsyncMock,
                ) as send_text:
                    self.arun(handler(call))

                fetch.assert_awaited_once_with("menharef", list_kind, 13)
                render_image.assert_awaited_once_with("menharef", users, list_kind, 0)
                self.assertIsInstance(send_photo.await_args.kwargs["photo"], bot.types.InputFile)
                self.assertIsNone(send_photo.await_args.kwargs["reply_markup"])
                send_text.assert_not_awaited()

    def test_free_view_fetches_only_three_and_routes_unlock_to_shared_country_subscription(self):
        call = self.callback(list_kind="followers", callback_id="followers-locked")
        users = self.sample_users("free")[:3]
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"username": "menharef"},
        ), patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "paid", "stars_price": 31},
        ), patch.object(
            bot, "_has_active_country_subscription", new_callable=AsyncMock,
            return_value=False,
        ), patch.object(
            bot, "_get_bridgly_social_list", new_callable=AsyncMock,
            return_value=users,
        ) as fetch, patch.object(
            bot, "_create_bridgly_list_image", new_callable=AsyncMock,
            return_value=b"locked-preview-jpeg",
        ) as render_image, patch.object(
            bot.bot, "answer_callback_query", new_callable=AsyncMock,
        ), patch.object(
            bot.bot, "send_photo", new_callable=AsyncMock,
        ) as send_photo:
            self.arun(bot.followers_button_callback(call))

        fetch.assert_awaited_once_with("menharef", "followers", 3)
        render_image.assert_awaited_once_with("menharef", users, "followers", 10)
        markup = send_photo.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.keyboard[0][0].callback_data, "country:lookup_12345678")
        self.assertIn("اشتراك واحد", markup.keyboard[0][0].text)

    def test_locked_text_fallback_never_exposes_locked_account_fields(self):
        users = [
            {"handle": "visible_1", "nickname": "Visible 1"},
            {"handle": "visible_2", "nickname": "Visible 2"},
            {"handle": "visible_3", "nickname": "Visible 3"},
        ]
        text = bot._format_bridgly_list_text(
            "menharef", users, "followers", locked_count=10
        )
        self.assertIn("١. 👤 Visible 1", text)
        self.assertIn("١٣. 🔒 خانة مقفلة", text)
        self.assertIn("ليست بيانات متابعين حقيقية", text)
        self.assertNotIn("secret_handle", text)

    def test_photo_render_failure_keeps_plain_text_fallback(self):
        call = self.callback(list_kind="followers", callback_id="followers-render-fail")
        users = self.sample_users("fallback")
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"username": "menharef"},
        ), patch.object(
            bot, "_get_bridgly_social_list", new_callable=AsyncMock,
            return_value=users,
        ), patch.object(
            bot, "_create_bridgly_list_image", new_callable=AsyncMock,
            side_effect=RuntimeError("render failed"),
        ), patch.object(
            bot.bot, "answer_callback_query", new_callable=AsyncMock,
        ), patch.object(
            bot.bot, "send_photo", new_callable=AsyncMock,
        ) as send_photo, patch.object(
            bot.bot, "send_message", new_callable=AsyncMock,
        ) as send_message:
            self.arun(bot.followers_button_callback(call))
        send_photo.assert_not_awaited()
        self.assertIn("قائمة المتابعين", send_message.await_args.kwargs["text"])

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
