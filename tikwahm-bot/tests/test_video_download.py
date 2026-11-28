import asyncio
import json
import os
import sys
import time
import unittest
import zlib
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
        self.saved_widget_sessions = bot._VIDEO_WIDGET_SESSIONS
        self.saved_widget_active = bot._VIDEO_WIDGET_DOWNLOAD_ACTIVE
        bot._TIKTOK_DOWNLOAD_PENDING = {}
        bot._TIKTOK_DOWNLOAD_ACTIVE = set()
        bot._USER_LANGUAGE_OVERRIDES = {}
        bot._VIDEO_WIDGET_SESSIONS = bot.OrderedDict()
        bot._VIDEO_WIDGET_DOWNLOAD_ACTIVE = set()

    def tearDown(self):
        bot._TIKTOK_DOWNLOAD_PENDING = self.saved_pending
        bot._TIKTOK_DOWNLOAD_ACTIVE = self.saved_active
        bot._USER_LANGUAGE_OVERRIDES = self.saved_languages
        bot._VIDEO_WIDGET_SESSIONS = self.saved_widget_sessions
        bot._VIDEO_WIDGET_DOWNLOAD_ACTIVE = self.saved_widget_active

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

    def test_native_widget_is_enabled_only_on_explicit_tikwahm_services(self):
        base = {"TIKWAHM_NATIVE_WIDGET_ENABLED": "1", "RENDER_EXTERNAL_URL": "https://tikwahm-bot-new.onrender.com"}
        with patch.dict(os.environ, base, clear=False):
            self.assertTrue(bot._native_video_widget_enabled())
        with patch.dict(os.environ, {**base, "RENDER_EXTERNAL_URL": "https://tikwahm-bot.onrender.com"}, clear=False):
            self.assertTrue(bot._native_video_widget_enabled())
        with patch.dict(os.environ, {**base, "RENDER_EXTERNAL_URL": "https://other-service.onrender.com"}, clear=False):
            self.assertFalse(bot._native_video_widget_enabled())
        with patch.dict(os.environ, {**base, "TIKWAHM_NATIVE_WIDGET_ENABLED": "0"}, clear=False):
            self.assertFalse(bot._native_video_widget_enabled())

    def test_native_widget_is_sent_with_official_method_and_within_html_limits(self):
        with patch.dict(os.environ, {
            "TIKWAHM_NATIVE_WIDGET_ENABLED": "1",
            "RENDER_EXTERNAL_URL": "https://tikwahm-bot-new.onrender.com",
        }, clear=False), patch.object(
            bot.apihelper, "_make_request", return_value={"message_id": 808}
        ) as request:
            session = self.arun(bot._send_native_video_widget(505, 505, 202, "ar"))
        self.assertEqual(request.call_args.args[1], "sendWidget")
        self.assertEqual(request.call_args.kwargs["method"], "post")
        params = request.call_args.kwargs["params"]
        self.assertTrue(params["allow_queries"])
        self.assertEqual(json.loads(params["reply_parameters"]), {"message_id": 202})
        widget_bytes = params["html"].encode("utf-8")
        self.assertLessEqual(len(widget_bytes), 32768)
        self.assertLessEqual(len(zlib.compress(widget_bytes, 9)), 4096)
        self.assertNotIn("<input", params["html"].lower())
        self.assertNotIn("<video", params["html"].lower())
        self.assertEqual(session["message_id"], 808)
        self.assertEqual(session["status"], "waiting_link")
        self.assertIn((505, 505), bot._TIKTOK_DOWNLOAD_PENDING)

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

    def test_enabled_video_button_sends_native_widget_instead_of_force_reply(self):
        call = SimpleNamespace(
            id="callback-widget",
            data="download:video",
            from_user=SimpleNamespace(id=505, language_code="ar"),
            message=SimpleNamespace(
                message_id=202,
                chat=SimpleNamespace(id=505, type="private"),
            ),
        )
        with patch.dict(os.environ, {
            "TIKWAHM_NATIVE_WIDGET_ENABLED": "1",
            "RENDER_EXTERNAL_URL": "https://tikwahm-bot.onrender.com",
        }, clear=False), patch.object(
            bot.bot, "answer_callback_query", new_callable=AsyncMock
        ), patch.object(
            bot, "_send_native_video_widget", new_callable=AsyncMock
        ) as send_widget, patch.object(
            bot.bot, "send_message", new_callable=AsyncMock
        ) as send_message:
            self.arun(bot.request_tiktok_video_link(call))

        send_widget.assert_awaited_once_with(505, 505, 202, "ar")
        send_message.assert_not_awaited()

    def test_widget_api_rejection_is_logged_with_detail_and_keeps_download_fallback(self):
        call = SimpleNamespace(
            id="callback-widget-failed",
            data="download:video",
            from_user=SimpleNamespace(id=505, language_code="ar"),
            message=SimpleNamespace(
                message_id=202,
                chat=SimpleNamespace(id=505, type="private"),
            ),
        )
        error = Exception("generic exception text is not used")
        error.error_code = 400
        error.description = "Bad Request: test rejection"
        prompt = SimpleNamespace(message_id=303)
        with patch.dict(os.environ, {
            "TIKWAHM_NATIVE_WIDGET_ENABLED": "1",
            "RENDER_EXTERNAL_URL": "https://tikwahm-bot.onrender.com",
        }, clear=False), patch.object(
            bot.bot, "answer_callback_query", new_callable=AsyncMock
        ), patch.object(
            bot, "_send_native_video_widget", new_callable=AsyncMock, side_effect=error
        ), patch.object(
            bot.bot, "send_message", new_callable=AsyncMock, return_value=prompt
        ) as send_message, patch("builtins.print") as log:
            self.arun(bot.request_tiktok_video_link(call))

        self.assertIsInstance(send_message.await_args.kwargs["reply_markup"], bot.types.ForceReply)
        logged_text = " ".join(str(call.args[0]) for call in log.call_args_list if call.args)
        self.assertIn("code=400", logged_text)
        self.assertIn("Bad Request: test rejection", logged_text)

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

    def test_pending_video_link_is_attached_to_the_native_widget_session(self):
        message = self.message("https://vm.tiktok.com/ZN8HD2jGK/")
        key = (505, 505)
        session = {
            "message_id": 808,
            "url": None,
            "lang": "ar",
            "status": "waiting_link",
            "expires_at": time.monotonic() + 60,
        }
        bot._VIDEO_WIDGET_SESSIONS[key] = session
        bot._TIKTOK_DOWNLOAD_PENDING[key] = time.monotonic() + 60
        with patch.object(bot, "_edit_native_video_widget", new_callable=AsyncMock) as edit_widget, patch.object(
            bot.bot, "reply_to", new_callable=AsyncMock
        ) as reply, patch.object(bot, "_process_tiktok_video_message", new_callable=AsyncMock) as download:
            self.arun(bot.handle_username_message(message))
        self.assertEqual(session["url"], message.text)
        self.assertIs(session["link_message"], message)
        self.assertEqual(session["status"], "ready")
        edit_widget.assert_awaited_once()
        reply.assert_awaited_once()
        download.assert_not_awaited()

    def test_native_widget_query_is_answered_before_download_is_scheduled(self):
        key = (505, 505)
        link_message = self.message("https://vm.tiktok.com/ZN8HD2jGK/")
        session = {
            "message_id": 808,
            "url": link_message.text,
            "link_message": link_message,
            "lang": "ar",
            "status": "ready",
            "expires_at": time.monotonic() + 60,
        }
        bot._VIDEO_WIDGET_SESSIONS[key] = session
        query = {
            "id": "widget-query-1",
            "from": {"id": 505},
            "chat": {"id": 505, "type": "private"},
            "message_id": 808,
            "data": "download",
        }

        def close_scheduled_coroutine(coro):
            coro.close()
            return None

        with patch.dict(os.environ, {
            "TIKWAHM_NATIVE_WIDGET_ENABLED": "1",
            "RENDER_EXTERNAL_URL": "https://tikwahm-bot-new.onrender.com",
        }, clear=False), patch.object(
            bot, "_answer_native_video_widget_query", new_callable=AsyncMock
        ) as answer, patch.object(
            bot, "_edit_native_video_widget", new_callable=AsyncMock
        ) as edit_widget, patch.object(
            bot.asyncio, "create_task", side_effect=close_scheduled_coroutine
        ) as create_task:
            self.arun(bot._process_native_video_widget_query(query))

        answer.assert_awaited_once_with("widget-query-1", session, "queued")
        edit_widget.assert_awaited_once_with(505, 808, session, "queued", "جارٍ تجهيز طلب التنزيل…")
        create_task.assert_called_once()
        self.assertIn(key, bot._VIDEO_WIDGET_DOWNLOAD_ACTIVE)

    def test_raw_webhook_widget_query_is_not_parsed_as_unsupported_update_type(self):
        update = {"update_id": 99, "widget_query": {"id": "q-1"}}
        with patch.object(
            bot, "_process_native_video_widget_query", new_callable=AsyncMock
        ) as process_widget, patch.object(
            bot.bot, "process_new_updates", new_callable=AsyncMock
        ) as process_updates:
            self.arun(bot._process_webhook_update(update))
        process_widget.assert_awaited_once_with(update["widget_query"])
        process_updates.assert_not_awaited()

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
        widget_session = {"message_id": 808, "lang": "ar"}
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
        ) as send_video, patch.object(
            bot, "_edit_native_video_widget", new_callable=AsyncMock
        ) as edit_widget:
            self.arun(bot._process_tiktok_video_message(message, message.text, widget_session=widget_session))
        self.assertIn("[", send_message.await_args.kwargs["text"])
        self.assertIn("█", send_message.await_args.kwargs["text"])
        send_video.assert_awaited_once()
        uploaded = send_video.await_args.kwargs["video"]
        self.assertIsInstance(uploaded, bot.types.InputFile)
        self.assertEqual(uploaded.file_name, "tiktok_video.mp4")
        self.assertEqual(send_video.await_args.kwargs["caption"], "TikTok title")
        self.assertTrue(any("اكتمل التنزيل" in call.kwargs.get("text", "") for call in edit_message.await_args_list))
        self.assertEqual([call.args[3] for call in edit_widget.await_args_list], ["extracting", "downloading"])
        self.assertNotIn((505, 505), bot._TIKTOK_DOWNLOAD_ACTIVE)


if __name__ == "__main__":
    unittest.main()
