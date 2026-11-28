import asyncio
import json
import os
import sys
import time
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

os.environ.setdefault("BOT_TOKEN", "123456:unit-test-token-not-for-network-use")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


class FakeResponse:
    def __init__(self, payload, content_type="application/json", status=200):
        self.stream = BytesIO(payload)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(payload))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        return self.stream.read(size)


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response


class TikTokVideoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.saved_pending = bot._TIKTOK_DOWNLOAD_PENDING
        self.saved_active = bot._TIKTOK_DOWNLOAD_ACTIVE
        self.saved_languages = bot._USER_LANGUAGE_OVERRIDES
        bot._TIKTOK_DOWNLOAD_PENDING = {}
        bot._TIKTOK_DOWNLOAD_ACTIVE = set()
        bot._USER_LANGUAGE_OVERRIDES = {}

    def tearDown(self):
        bot._TIKTOK_DOWNLOAD_PENDING = self.saved_pending
        bot._TIKTOK_DOWNLOAD_ACTIVE = self.saved_active
        bot._USER_LANGUAGE_OVERRIDES = self.saved_languages

    @staticmethod
    def arun(awaitable):
        return asyncio.run(awaitable)

    @staticmethod
    def message(text="", chat_id=505, user_id=505):
        return SimpleNamespace(
            text=text,
            message_id=101,
            chat=SimpleNamespace(id=chat_id, type="private"),
            from_user=SimpleNamespace(id=user_id, first_name="Test User", language_code="ar"),
        )

    def test_extracts_tiktok_short_and_full_video_links_only(self):
        short = "https://vm.tiktok.com/ZN8HD2jGK/"
        full = "https://www.tiktok.com/@demo/video/123456?lang=en"
        self.assertEqual(bot._extract_tiktok_video_url(short + "."), short)
        self.assertEqual(bot._extract_tiktok_video_url(full), full)
        self.assertIsNone(bot._extract_tiktok_video_url("https://www.tiktok.com/@demo"))
        self.assertIsNone(bot._extract_tiktok_video_url("https://vm.tiktok.com.evil.invalid/abc"))
        self.assertIsNone(bot._extract_tiktok_video_url("https://example.com/video/123"))

    def test_fintok_request_uses_public_download_endpoint_headers_and_form(self):
        media_url = "https://v16m.tiktokcdn-us.com/video.mp4"
        response = FakeResponse(json.dumps({
            "type": "video",
            "video_url": media_url,
            "title": "Sample TikTok video",
        }).encode("utf-8"))
        with patch.object(bot.urllib.request, "urlopen", return_value=response) as urlopen:
            result = bot._request_tiktok_video_details("https://vm.tiktok.com/ZN8HD2jGK/")
        self.assertEqual(result, (media_url, "Sample TikTok video"))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, bot.TIKTOK_DOWNLOAD_ENDPOINT)
        self.assertEqual(request.get_header("Origin"), "https://fintok.neocities.org")
        self.assertEqual(request.get_header("Referer"), "https://fintok.neocities.org/download/")
        self.assertEqual(
            parse_qs(request.data.decode("utf-8")),
            {"urlInput": ["https://vm.tiktok.com/ZN8HD2jGK/"]},
        )

    def test_media_downloader_rejects_untrusted_hosts_before_network(self):
        with patch.object(bot.urllib.request, "build_opener") as build_opener:
            with self.assertRaises(bot._TikTokVideoDownloadError):
                bot._download_tiktok_media("https://example.com/file.mp4")
        build_opener.assert_not_called()

    def test_media_downloader_accepts_valid_mp4_within_size_limit(self):
        payload = b"\x00\x00\x00\x18ftypisom" + (b"x" * 32)
        response = FakeResponse(payload, content_type="video/mp4")
        opener = FakeOpener(response)
        with patch.object(bot.urllib.request, "build_opener", return_value=opener):
            result = bot._download_tiktok_media("https://v16m.tiktokcdn-us.com/video.mp4")
        self.assertEqual(result, payload)
        self.assertEqual(opener.requests[0][1], bot.TIKTOK_MEDIA_TIMEOUT_SECONDS)

    def test_media_downloader_rejects_oversized_response_before_reading_body(self):
        response = FakeResponse(b"", content_type="video/mp4")
        response.headers["Content-Length"] = str(bot.MAX_TIKTOK_VIDEO_BYTES + 1)
        opener = FakeOpener(response)
        with patch.object(bot.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(bot._TikTokVideoDownloadError):
                bot._download_tiktok_media("https://v16m.tiktokcdn-us.com/video.mp4")
        self.assertEqual(response.stream.tell(), 0)

    def test_welcome_has_personalized_arabic_copy_and_requested_button_rows(self):
        message = self.message()
        with patch.object(bot.apihelper, "_make_request", return_value={"message_id": 202}) as request:
            self.arun(bot.send_welcome(message))
        self.assertEqual(request.call_args.args[1], "sendRichMessage")
        self.assertEqual(request.call_args.kwargs["method"], "post")
        params = request.call_args.kwargs["params"]
        self.assertEqual(json.loads(params["reply_parameters"]), {"message_id": 101})
        rich_message = json.loads(params["rich_message"])
        self.assertTrue(rich_message["is_rtl"])
        paragraphs = [block["text"] for block in rich_message["blocks"] if block["type"] == "paragraph"]
        self.assertEqual(paragraphs[0], "• مرحباً °~ Test User° في بوت معلومات تيك توك 🤖")
        self.assertEqual(paragraphs[1], bot._WELCOME_COPY["ar"]["description"])
        self.assertEqual(paragraphs[2:], ["أرسل يوزر أو رابط مقطع.", "يمكنك استخدام الأقسام من الأزرار بالأسفل."])
        rows = [block["buttons"] for block in rich_message["blocks"] if block["type"] == "buttons"]
        self.assertEqual(
            [[button["text"] for button in row] for row in rows],
            [["القسم الخاص"], ["قسم التحميل", "قسم البحث"], ["العربية", "English"]],
        )
        self.assertEqual(
            [[button["callback_data"] for button in row] for row in rows],
            [["menu:private"], ["menu:download", "menu:search"], ["lang:ar", "lang:en"]],
        )

    def test_download_section_has_photos_and_stories_then_video_then_back(self):
        call = SimpleNamespace(
            id="callback-download",
            data="menu:download",
            from_user=SimpleNamespace(id=505, language_code="ar"),
            message=SimpleNamespace(message_id=202, chat=SimpleNamespace(id=505, type="private")),
        )
        with patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), patch.object(
            bot.apihelper, "_make_request", return_value={"message_id": 303}
        ) as request:
            self.arun(bot.menu_section_callback(call))
        rich_message = json.loads(request.call_args.kwargs["params"]["rich_message"])
        paragraphs = [block["text"] for block in rich_message["blocks"] if block["type"] == "paragraph"]
        self.assertEqual(paragraphs, [bot._DOWNLOAD_COPY["ar"]["text"]])
        rows = [block["buttons"] for block in rich_message["blocks"] if block["type"] == "buttons"]
        self.assertEqual(
            [[button["text"] for button in row] for row in rows],
            [["تحميل صور", "تحميل الستوري"], ["تحميل الفيديو"], ["رجوع"]],
        )
        self.assertEqual(
            [[button["callback_data"] for button in row] for row in rows],
            [["download:photos", "download:stories"], ["download:video"], ["menu:home"]],
        )

    def test_english_language_button_updates_menu_copy(self):
        call = SimpleNamespace(
            id="callback-language",
            data="lang:en",
            from_user=SimpleNamespace(id=505, first_name="Taylor", language_code="ar"),
            message=SimpleNamespace(message_id=202, chat=SimpleNamespace(id=505, type="private")),
        )
        with patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), patch.object(
            bot.apihelper, "_make_request", return_value={"message_id": 303}
        ) as request:
            self.arun(bot.language_callback(call))
        self.assertEqual(bot._USER_LANGUAGE_OVERRIDES[505], "en")
        rich_message = json.loads(request.call_args.kwargs["params"]["rich_message"])
        self.assertFalse(rich_message["is_rtl"])
        self.assertEqual(rich_message["blocks"][0]["text"], "• Welcome °~ Taylor° to the TikTok Info Bot 🤖")
        self.assertEqual(rich_message["blocks"][4]["buttons"][0]["text"], "Private Section")

    def test_button_callback_asks_for_link_and_tracks_clicking_user(self):
        call = SimpleNamespace(
            id="callback-1",
            data="download:video",
            from_user=SimpleNamespace(id=505),
            message=SimpleNamespace(
                message_id=202,
                chat=SimpleNamespace(id=505, type="private"),
            ),
        )
        prompt = SimpleNamespace(message_id=303)
        with patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), patch.object(
            bot.bot, "send_message", new_callable=AsyncMock, return_value=prompt
        ) as send_message:
            self.arun(bot.request_tiktok_video_link(call))
        self.assertIn((505, 505), bot._TIKTOK_DOWNLOAD_PENDING)
        self.assertGreater(bot._TIKTOK_DOWNLOAD_PENDING[(505, 505)], time.monotonic())
        self.assertIsInstance(send_message.await_args.kwargs["reply_markup"], bot.types.ForceReply)
        self.assertIn("رابط فيديو TikTok", send_message.await_args.kwargs["text"])

    def test_pending_video_link_routes_to_downloader_not_account_search(self):
        message = self.message("https://vm.tiktok.com/ZN8HD2jGK/")
        key = (505, 505)
        bot._TIKTOK_DOWNLOAD_PENDING[key] = time.monotonic() + 60
        with patch.object(
            bot, "_process_tiktok_video_message", new_callable=AsyncMock
        ) as process_video, patch.object(
            bot, "_start_username_search", new_callable=AsyncMock
        ) as search, patch.object(bot.bot, "reply_to", new_callable=AsyncMock):
            self.arun(bot.handle_username_message(message))
        process_video.assert_awaited_once_with(message, "https://vm.tiktok.com/ZN8HD2jGK/")
        search.assert_not_awaited()
        self.assertNotIn(key, bot._TIKTOK_DOWNLOAD_PENDING)

    def test_video_link_without_download_button_does_not_start_download(self):
        message = self.message("https://vm.tiktok.com/ZN8HD2jGK/")
        with patch.object(
            bot, "_process_tiktok_video_message", new_callable=AsyncMock
        ) as process_video, patch.object(
            bot, "_start_username_search", new_callable=AsyncMock
        ) as search, patch.object(bot.bot, "reply_to", new_callable=AsyncMock) as reply:
            self.arun(bot.handle_username_message(message))
        process_video.assert_not_awaited()
        search.assert_not_awaited()
        self.assertIn("قسم التحميل", reply.await_args.args[1])

    def test_processing_uploads_actual_mp4_as_video_and_finishes_progress(self):
        message = self.message("https://vm.tiktok.com/ZN8HD2jGK/")
        status = SimpleNamespace(message_id=404)
        media_url = "https://v16m.tiktokcdn-us.com/video.mp4"
        payload = b"\x00\x00\x00\x18ftypisom" + (b"x" * 32)
        with patch.object(bot, "_request_tiktok_video_details", return_value=(media_url, "TikTok title")), patch.object(
            bot, "_download_tiktok_media", return_value=payload
        ), patch.object(
            bot.bot, "send_message", new_callable=AsyncMock, return_value=status
        ) as send_message, patch.object(
            bot.bot, "edit_message_text", new_callable=AsyncMock
        ) as edit_message, patch.object(
            bot.bot, "send_video", new_callable=AsyncMock
        ) as send_video:
            self.arun(bot._process_tiktok_video_message(message, message.text))
        self.assertIn("[", send_message.await_args.kwargs["text"])
        self.assertIn("█", send_message.await_args.kwargs["text"])
        send_video.assert_awaited_once()
        uploaded = send_video.await_args.kwargs["video"]
        self.assertIsInstance(uploaded, bot.types.InputFile)
        self.assertEqual(uploaded.file_name, "tiktok_video.mp4")
        self.assertEqual(send_video.await_args.kwargs["caption"], "TikTok title")
        self.assertTrue(any("اكتمل التنزيل" in call.kwargs.get("text", "") for call in edit_message.await_args_list))
        self.assertNotIn((505, 505), bot._TIKTOK_DOWNLOAD_ACTIVE)


if __name__ == "__main__":
    unittest.main()
