import asyncio
import hashlib
import json
import os
import sys
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

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
            {"host": f"proxy-{index}", "port": 10000 + index,
             "username": "test-user", "password": "test-password"}
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
    def arun(awaitable):
        return asyncio.run(awaitable)

    def test_connection_timeout_and_fintok_rejection_rotate_in_order(self):
        seen = []

        async def fake_fetch(username, proxy, progress=None):
            seen.append(proxy["host"])
            if len(seen) == 1:
                raise bot._ProxyConnectionBroken("connection refused")
            if len(seen) == 2:
                raise bot._FinTokAttemptTimedOut("source timeout")
            if len(seen) == 3:
                raise bot._FinTokRequestRejected("HTTP 403")
            return {"ok": username}

        with patch.object(bot, "_fetch_once_through_proxy", fake_fetch):
            result = self.arun(bot.fetch_data_from_fintok("public_test_account"))
        self.assertEqual(seen, [f"proxy-{i}" for i in range(4)])
        self.assertEqual(result, {"ok": "public_test_account"})
        self.assertEqual(bot._ACTIVE_PROXY_INDEX, 4)

    def test_all_proxies_are_tried_then_index_wraps_to_first(self):
        seen = []

        async def fail_every_proxy(username, proxy, progress=None):
            seen.append(proxy["host"])
            raise bot._ProxyConnectionBroken("connection failed")

        with patch.object(bot, "_fetch_once_through_proxy", fail_every_proxy):
            result = self.arun(bot.fetch_data_from_fintok("public_test_account"))
        self.assertEqual(seen, [f"proxy-{i}" for i in range(10)])
        self.assertIn("جميع البروكسيات", result)
        self.assertEqual(bot._ACTIVE_PROXY_INDEX, 0)

        next_search = []

        async def succeed(username, proxy, progress=None):
            next_search.append(proxy["host"])
            return "ok"

        with patch.object(bot, "_fetch_once_through_proxy", succeed):
            self.assertEqual(self.arun(bot.fetch_data_from_fintok("another")), "ok")
        self.assertEqual(next_search, ["proxy-0"])

    def test_success_advances_the_starting_proxy_for_next_search(self):
        seen = []

        async def succeed(username, proxy, progress=None):
            seen.append(proxy["host"])
            return "ok"

        with patch.object(bot, "_fetch_once_through_proxy", succeed):
            self.assertEqual(self.arun(bot.fetch_data_from_fintok("first")), "ok")
            self.assertEqual(self.arun(bot.fetch_data_from_fintok("second")), "ok")
        self.assertEqual(seen, ["proxy-0", "proxy-1"])


class FinTokRequestTests(unittest.TestCase):
    proxy = {"host": "proxy.example", "port": 8080,
             "username": "test-user", "password": "test-pass"}

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
            return FinTokRequestTests.FakeResponse(self.payload)

    def test_request_uses_signed_fintok_endpoint_and_site_headers(self):
        opener = self.FakeOpener({"status": "ok", "data": {"username": "iwahm"}})
        with patch.object(bot.urllib.request, "build_opener", return_value=opener), \
             patch.object(bot.time, "time", return_value=1712345678):
            result = bot._request_fintok_payload("/", {"username": "iwahm"}, ("iwahm",), self.proxy)

        parsed = urlparse(opener.request.full_url)
        query = parse_qs(parsed.query)
        expected = hashlib.sha256(
            ("iwahm1712345678" + bot.FINTOK_SIGNATURE_SALT).encode()
        ).hexdigest()
        self.assertEqual(parsed.netloc, "tiktokinfov10.itfreeforever.workers.dev")
        self.assertEqual(parsed.path, "/")
        self.assertEqual(query["username"], ["iwahm"])
        self.assertEqual(query["ts"], ["1712345678"])
        self.assertEqual(opener.request.get_header("X-token"), expected)
        self.assertEqual(opener.request.get_header("Origin"), "https://fintok.neocities.org")
        self.assertEqual(opener.request.get_header("Referer"), "https://fintok.neocities.org/")
        self.assertEqual(opener.timeout, bot.SOURCE_TIMEOUT_SECONDS)
        self.assertEqual(result["status"], "ok")

    def test_http_throttle_is_retryable_for_proxy_rotation(self):
        class ThrottledOpener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

        with patch.object(bot.urllib.request, "build_opener", return_value=ThrottledOpener()):
            with self.assertRaises(bot._FinTokRequestRejected):
                bot._request_fintok_payload("/", {"username": "iwahm"}, ("iwahm",), self.proxy)

    def test_timeout_and_connection_failure_are_classified_for_rotation(self):
        class FailingOpener:
            def __init__(self, reason):
                self.reason = reason

            def open(self, _request, timeout):
                raise urllib.error.URLError(self.reason)

        for reason, expected in [
            (TimeoutError("timed out"), bot._FinTokAttemptTimedOut),
            (ConnectionRefusedError("connection refused"), bot._ProxyConnectionBroken),
        ]:
            with self.subTest(expected=expected.__name__), \
                 patch.object(bot.urllib.request, "build_opener", return_value=FailingOpener(reason)):
                with self.assertRaises(expected):
                    bot._request_fintok_payload("/", {"username": "iwahm"}, ("iwahm",), self.proxy)

    def test_four_endpoint_results_are_assembled_in_site_order(self):
        profile = {
            "fullname": "Exact Name", "username": "iwahm", "profile": "",
            "following": 5, "followers": 8830, "likes": 1950,
            "userid": "7090596962206893062", "private": False,
            "secUid": "SECID", "language": "Arabic",
            "created_time": "2023-04-25 | 03:36:21",
            "last_change_name": "2026-10-17 | 21:05:25",
        }
        security = {
            "is_email": True, "is_mobile": True, "is_password": True, "is_passkey": True,
            "oauth": {"is_enabled": True, "platforms": ["google"]},
            "account_status": {"is_logged_in": False, "is_ftc_user": False,
                              "is_most_device": False, "is_org_account": False},
        }
        payloads = {
            "/": {"status": "ok", "data": profile},
            "/security": {"msg": {"account_info": security}},
            "/active": {"status": "ok", "data": {"active_region": "Italy"}},
            "/registered": {"status": "ok", "data": {"registered_region": "Saudi Arabia"}},
        }
        seen = []

        def fake_request(path, params, signature_parts, proxy):
            seen.append((path, params, signature_parts, proxy["host"]))
            return payloads[path]

        with patch.object(bot, "_request_fintok_payload", side_effect=fake_request):
            result = asyncio.run(bot._fetch_once_through_proxy("iwahm", self.proxy))

        self.assertEqual({row[0] for row in seen}, {"/", "/security", "/active", "/registered"})
        self.assertTrue(all(row[3] == self.proxy["host"] for row in seen))
        self.assertIn("المتابعون | 8.83K", result["text"])
        self.assertIn("الإعجابات | 1.95K", result["text"])
        self.assertIn("نوع الحساب | عام (Public Account)", result["text"])
        self.assertIn("المنطقة النشطة | إيطاليا — Italy 🇮🇹 — المصدر: FinTok", result["text"])
        self.assertEqual(
            result["original_country"],
            "المملكة العربية السعودية — Saudi Arabia 🇸🇦 — المصدر: FinTok",
        )
        self.assertNotIn("منطقة التسجيل", result["text"])
        self.assertNotIn("Saudi Arabia", result["text"])
        self.assertIn("اللغة | العربية (Arabic)", result["text"])
        self.assertIn("البريد الإلكتروني مرتبط | نعم", result["text"])
        self.assertIn("حالة تسجيل الدخول | لا", result["text"])
        ordered_labels = ["يتابع", "المتابعون", "الإعجابات", "معرّف المستخدم", "نوع الحساب",
                          "المنطقة النشطة", "اللغة", "وقت إنشاء الحساب",
                          "آخر تغيير للاسم", "البريد الإلكتروني مرتبط", "رقم الجوال مرتبط",
                          "كلمة المرور مُعيّنة", "مفتاح المرور (Passkey)", "OAuth مفعّل",
                          "المنصات المتصلة", "حالة تسجيل الدخول", "مستخدم FTC",
                          "حالة تعدد الأجهزة", "حساب مؤسسة"]
        positions = [result["text"].index(label) for label in ordered_labels]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
