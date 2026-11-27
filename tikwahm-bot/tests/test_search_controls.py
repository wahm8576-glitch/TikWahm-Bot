import asyncio
import os
import sys
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:unit-test-token-not-for-network-use")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


class SearchControlsTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            "last_at": bot._USER_SEARCH_LAST_AT,
            "cooldown_lock": bot._USER_SEARCH_COOLDOWN_LOCK,
            "lookups": bot._COUNTRY_LOOKUP_CACHE,
            "orders": bot._COUNTRY_ORDER_CACHE,
            "subscriptions": bot._COUNTRY_SUBSCRIPTION_CACHE,
            "pending_orders": bot._COUNTRY_PENDING_ORDER_CACHE,
            "processed_payments": bot._COUNTRY_PROCESSED_PAYMENT_CACHE,
            "settings": bot._COUNTRY_ACCESS_SETTINGS_CACHE,
            "order_lock": bot._COUNTRY_ORDER_LOCK,
            "payment_lock": bot._COUNTRY_PAYMENT_LOCK,
            "support_users": bot._SUPPORT_PENDING_USER_IDS,
            "admin_user_id": bot._ADMIN_USER_ID,
            "admin_pending_price": bot._ADMIN_PENDING_PRICE_USER_ID,
            "admin_command": bot.ADMIN_PANEL_COMMAND,
            "firestore": bot._FIRESTORE_CLIENT,
            "settings_document": bot._FIRESTORE_COUNTRY_SETTINGS_DOCUMENT,
        }
        bot._USER_SEARCH_LAST_AT = OrderedDict()
        bot._USER_SEARCH_COOLDOWN_LOCK = asyncio.Lock()
        bot._COUNTRY_LOOKUP_CACHE = {}
        bot._COUNTRY_ORDER_CACHE = {}
        bot._COUNTRY_SUBSCRIPTION_CACHE = {}
        bot._COUNTRY_PENDING_ORDER_CACHE = {}
        bot._COUNTRY_PROCESSED_PAYMENT_CACHE = set()
        bot._COUNTRY_ACCESS_SETTINGS_CACHE = {"mode": "free", "stars_price": 0}
        bot._COUNTRY_ORDER_LOCK = asyncio.Lock()
        bot._COUNTRY_PAYMENT_LOCK = asyncio.Lock()
        bot._SUPPORT_PENDING_USER_IDS = set()
        bot._ADMIN_USER_ID = None
        bot._ADMIN_PENDING_PRICE_USER_ID = None
        bot.ADMIN_PANEL_COMMAND = ""
        bot._FIRESTORE_CLIENT = None
        bot._FIRESTORE_COUNTRY_SETTINGS_DOCUMENT = None

    def tearDown(self):
        bot._USER_SEARCH_LAST_AT = self.saved["last_at"]
        bot._USER_SEARCH_COOLDOWN_LOCK = self.saved["cooldown_lock"]
        bot._COUNTRY_LOOKUP_CACHE = self.saved["lookups"]
        bot._COUNTRY_ORDER_CACHE = self.saved["orders"]
        bot._COUNTRY_SUBSCRIPTION_CACHE = self.saved["subscriptions"]
        bot._COUNTRY_PENDING_ORDER_CACHE = self.saved["pending_orders"]
        bot._COUNTRY_PROCESSED_PAYMENT_CACHE = self.saved["processed_payments"]
        bot._COUNTRY_ACCESS_SETTINGS_CACHE = self.saved["settings"]
        bot._COUNTRY_ORDER_LOCK = self.saved["order_lock"]
        bot._COUNTRY_PAYMENT_LOCK = self.saved["payment_lock"]
        bot._SUPPORT_PENDING_USER_IDS = self.saved["support_users"]
        bot._ADMIN_USER_ID = self.saved["admin_user_id"]
        bot._ADMIN_PENDING_PRICE_USER_ID = self.saved["admin_pending_price"]
        bot.ADMIN_PANEL_COMMAND = self.saved["admin_command"]
        bot._FIRESTORE_CLIENT = self.saved["firestore"]
        bot._FIRESTORE_COUNTRY_SETTINGS_DOCUMENT = self.saved["settings_document"]

    @staticmethod
    def arun(awaitable):
        return asyncio.run(awaitable)

    @staticmethod
    def callback(callback_id, data, user_id, chat_type="private"):
        return SimpleNamespace(
            id=callback_id,
            data=data,
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(
                message_id=444,
                chat=SimpleNamespace(id=user_id, type=chat_type),
            ),
        )

    @staticmethod
    def lookup(country="كندا — Canada 🇨🇦 — المصدر: FinTok", username="sample"):
        return {"country": country, "username": username}

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
        self.assertEqual(bot.MAX_LOOKUP_SECONDS, 15)

    def test_cooldown_is_per_user_and_expires_after_twenty_seconds(self):
        async def scenario():
            first_user = await bot._reserve_user_search(101, now=100)
            same_user_early = await bot._reserve_user_search(101, now=119.5)
            other_user = await bot._reserve_user_search(202, now=119.5)
            ready = await bot._reserve_user_search(101, now=120)
            return first_user, same_user_early, other_user, ready

        self.assertEqual(self.arun(scenario()), (0, 1, 0, 0))
        self.assertEqual(bot.USER_SEARCH_COOLDOWN_SECONDS, 20)

    def test_blocked_attempt_does_not_extend_cooldown(self):
        async def scenario():
            await bot._reserve_user_search("user-a", now=50)
            blocked = await bot._reserve_user_search("user-a", now=60)
            still_blocked = await bot._reserve_user_search("user-a", now=65)
            allowed = await bot._reserve_user_search("user-a", now=70)
            return blocked, still_blocked, allowed

        self.assertEqual(self.arun(scenario()), (10, 5, 0))

    def test_cooldown_sends_one_gif_and_counts_down_on_that_message(self):
        source_message = SimpleNamespace(
            chat=SimpleNamespace(id=123), message_id=456,
            from_user=SimpleNamespace(id=101),
        )
        wait_message = SimpleNamespace(message_id=789)
        with patch.object(bot.bot, "send_animation", new_callable=AsyncMock) as send_gif, \
             patch.object(bot, "_run_search_cooldown_countdown", new_callable=AsyncMock) as countdown:
            send_gif.return_value = wait_message
            self.arun(bot._send_search_cooldown_message(source_message, 2.1))

        send_gif.assert_awaited_once()
        self.assertEqual(send_gif.await_args.kwargs["chat_id"], 123)
        self.assertEqual(send_gif.await_args.kwargs["reply_to_message_id"], 456)
        self.assertEqual(send_gif.await_args.kwargs["caption"], "🔎 البحث متاح بعد ٣ ثوانٍ")
        self.assertTrue(Path(bot.LOADING_GIF_PATH).is_file())
        countdown.assert_awaited_once()
        self.assertEqual(countdown.await_args.args[0:3], (123, wait_message, "animation"))

    def test_cooldown_expiry_requires_manual_resubmission(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123), message_id=456,
            from_user=SimpleNamespace(id=101),
        )
        with patch.object(bot.bot, "send_animation", new_callable=AsyncMock) as send_gif, \
             patch.object(bot, "_run_search_cooldown_countdown", new_callable=AsyncMock) as countdown, \
             patch.object(bot, "_start_username_search", new_callable=AsyncMock) as start_search:
            send_gif.return_value = SimpleNamespace(message_id=789)
            self.arun(bot._send_search_cooldown_message(message, 1))

        countdown.assert_awaited_once()
        self.assertIn("أعد إرسال اسم المستخدم", countdown.await_args.kwargs["completion_text"])
        start_search.assert_not_awaited()

    def test_countdown_edits_the_same_message_each_second_until_ready(self):
        class FakeClock:
            now = 0.0

            def __call__(self):
                return self.now

            async def sleep(self, delay):
                self.now += delay

        clock = FakeClock()
        wait_message = SimpleNamespace(message_id=789)
        edits = []

        async def fake_edit(chat_id, message, animation, text, reply_markup=None):
            edits.append((chat_id, message.message_id, animation, text))

        async def scenario():
            with patch.object(bot, "_edit_wait_message", side_effect=fake_edit):
                await bot._run_search_cooldown_countdown(
                    123,
                    wait_message,
                    "animation",
                    deadline=3.0,
                    initial_seconds=3,
                    clock=clock,
                    sleep=clock.sleep,
                )

        self.arun(scenario())
        self.assertEqual([item[1] for item in edits], [789, 789, 789])
        self.assertEqual([item[3] for item in edits], [
            "🔎 البحث متاح بعد ثانيتين",
            "🔎 البحث متاح بعد ثانية",
            "✅ البحث متاح الآن، أرسل اسم المستخدم من جديد.",
        ])

    def test_concurrent_requests_from_one_user_reserve_only_one_slot(self):
        async def scenario():
            return await asyncio.gather(
                bot._reserve_user_search("same-user", now=100),
                bot._reserve_user_search("same-user", now=100),
            )

        self.assertEqual(sorted(self.arun(scenario())), [0, 20])

    def test_fintok_result_gets_an_inline_country_button(self):
        wait_message = SimpleNamespace(message_id=789)
        result = {
            "markdown": "**Display** · `@sample`\n| البيان | القيمة |",
            "text": "Display",
            "username": "sample",
            "original_country": "كندا — Canada 🇨🇦 — المصدر: FinTok",
        }
        with patch.object(bot.bot, "delete_message", new_callable=AsyncMock), \
             patch.object(bot.bot, "send_rich_message", new_callable=AsyncMock) as send_rich:
            self.arun(bot._deliver_result(123, wait_message, "rich", result))

        markup = send_rich.await_args.kwargs["reply_markup"]
        button = markup.keyboard[0][0]
        self.assertEqual(button.text, "دولة الحساب الأصلية")
        self.assertTrue(button.callback_data.startswith("country:"))
        lookup_id = button.callback_data.removeprefix("country:")
        self.assertEqual(
            self.arun(bot._get_country_lookup(lookup_id))["country"],
            "كندا — Canada 🇨🇦 — المصدر: FinTok",
        )

    def test_country_name_resolves_to_a_real_png_flag_url(self):
        self.assertEqual(
            bot._country_flag_png_url("كندا — Canada 🇨🇦 — المصدر: FinTok"),
            "https://flagcdn.com/w160/ca.png",
        )
        self.assertEqual(
            bot._country_flag_png_url("المملكة العربية السعودية"),
            "https://flagcdn.com/w160/sa.png",
        )

    def test_free_country_button_sends_separate_flag_photo(self):
        call = self.callback("callback-free", "country:lookup_12345678", 101)
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value=self.lookup(),
        ), patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "free", "stars_price": 0},
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
             patch.object(bot.bot, "send_photo", new_callable=AsyncMock) as send_photo:
            self.arun(bot.country_button_callback(call))

        send_photo.assert_awaited_once()
        self.assertEqual(send_photo.await_args.kwargs["photo"], "https://flagcdn.com/w160/ca.png")
        self.assertIn("كندا", send_photo.await_args.kwargs["caption"])
        self.assertNotIn("lookup", send_photo.await_args.kwargs["caption"])

    def test_paid_country_button_displays_terms_before_any_invoice(self):
        call = self.callback("callback-paid", "country:lookup_12345678", 202)
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value=self.lookup(),
        ), patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "paid", "stars_price": 17},
        ), patch.object(
            bot, "_has_active_country_subscription", new_callable=AsyncMock,
            return_value=False,
        ), patch.object(
            bot, "_get_pending_country_order", new_callable=AsyncMock,
            return_value=None,
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
             patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send_message, \
             patch.object(bot.bot, "create_invoice_link", new_callable=AsyncMock) as create_link:
            self.arun(bot.country_button_callback(call))

        create_link.assert_not_awaited()
        self.assertEqual(send_message.await_args.kwargs["chat_id"], 202)
        self.assertIn("17 ⭐ كل 30 يومًا", send_message.await_args.kwargs["text"])
        self.assertIn("يتجدد تلقائيًا", send_message.await_args.kwargs["text"])
        self.assertEqual(
            send_message.await_args.kwargs["reply_markup"].keyboard[0][0].text,
            "أوافق على الشروط وأتابع",
        )

    def test_agreement_creates_one_xtr_recurring_invoice_link(self):
        call = self.callback("agreement-1", "country_agree:lookup_12345678", 303)
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value=self.lookup(username="sample"),
        ), patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "paid", "stars_price": 21},
        ), patch.object(
            bot, "_has_active_country_subscription", new_callable=AsyncMock,
            return_value=False,
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
             patch.object(bot.bot, "create_invoice_link", new_callable=AsyncMock) as create_link, \
             patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send_message:
            create_link.return_value = "https://t.me/$recurring_test_invoice"
            self.arun(bot.country_agree_to_terms_callback(call))

        create_link.assert_awaited_once()
        args = create_link.await_args.kwargs
        self.assertEqual(args["currency"], "XTR")
        self.assertEqual(args["provider_token"], "")
        self.assertEqual(args["subscription_period"], 2_592_000)
        self.assertEqual(len(args["prices"]), 1)
        self.assertEqual(args["prices"][0].amount, 21)
        order_id = args["payload"]
        order = bot._COUNTRY_ORDER_CACHE[order_id]
        self.assertEqual(order["user_id"], 303)
        self.assertEqual(order["status"], "pending")
        self.assertTrue(order["terms_accepted_at"])
        self.assertEqual(order["requested_country"], "كندا — Canada 🇨🇦 — المصدر: FinTok")
        self.assertEqual(bot._COUNTRY_PENDING_ORDER_CACHE["303"], order_id)
        markup = send_message.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.keyboard[0][0].url, "https://t.me/$recurring_test_invoice")
        self.assertIn("21 ⭐ كل 30 يومًا", send_message.await_args.kwargs["text"])

    def test_second_agreement_reuses_the_same_pending_invoice(self):
        first = self.callback("agreement-1", "country_agree:lookup_12345678", 303)
        second = self.callback("agreement-2", "country_agree:lookup_12345678", 303)
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value=self.lookup(),
        ), patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "paid", "stars_price": 21},
        ), patch.object(
            bot, "_has_active_country_subscription", new_callable=AsyncMock,
            return_value=False,
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
             patch.object(bot.bot, "create_invoice_link", new_callable=AsyncMock) as create_link, \
             patch.object(bot.bot, "send_message", new_callable=AsyncMock):
            create_link.return_value = "https://t.me/$same_invoice"
            async def scenario():
                await bot.country_agree_to_terms_callback(first)
                first_order_id = next(iter(bot._COUNTRY_ORDER_CACHE))
                await bot.country_agree_to_terms_callback(second)
                return first_order_id
            order_id = self.arun(scenario())

        create_link.assert_awaited_once()
        self.assertEqual(len(bot._COUNTRY_ORDER_CACHE), 1)
        self.assertEqual(bot._COUNTRY_PENDING_ORDER_CACHE["303"], order_id)

    def test_pre_checkout_checks_owner_price_currency_terms_and_expiry(self):
        order_id = "order_12345678"
        bot._COUNTRY_ORDER_CACHE[order_id] = {
            "user_id": 303, "currency": "XTR", "amount": 21,
            "status": "pending", "terms_accepted_at": 1,
            "expires_at": 9_999_999_999,
        }
        valid = SimpleNamespace(
            id="pre-1", invoice_payload=order_id, currency="XTR", total_amount=21,
            from_user=SimpleNamespace(id=303),
        )
        wrong_owner = SimpleNamespace(
            id="pre-2", invoice_payload=order_id, currency="XTR", total_amount=21,
            from_user=SimpleNamespace(id=304),
        )
        wrong_amount = SimpleNamespace(
            id="pre-3", invoice_payload=order_id, currency="XTR", total_amount=22,
            from_user=SimpleNamespace(id=303),
        )
        with patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "paid", "stars_price": 21},
        ), patch.object(
            bot, "_has_active_country_subscription", new_callable=AsyncMock,
            return_value=False,
        ), patch.object(bot.bot, "answer_pre_checkout_query", new_callable=AsyncMock) as answer:
            async def scenario():
                await bot.country_pre_checkout(valid)
                self.assertTrue(answer.await_args.kwargs["ok"])
                await bot.country_pre_checkout(wrong_owner)
                self.assertFalse(answer.await_args.kwargs["ok"])
                await bot.country_pre_checkout(wrong_amount)
                self.assertFalse(answer.await_args.kwargs["ok"])
            self.arun(scenario())

    def test_pre_checkout_rejects_expired_order_and_changed_admin_price(self):
        order_id = "order_22345678"
        bot._COUNTRY_ORDER_CACHE[order_id] = {
            "user_id": 303, "currency": "XTR", "amount": 21,
            "status": "pending", "terms_accepted_at": 1,
            "expires_at": 1,
        }
        query = SimpleNamespace(
            id="pre-expired", invoice_payload=order_id, currency="XTR", total_amount=21,
            from_user=SimpleNamespace(id=303),
        )
        with patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "paid", "stars_price": 22},
        ), patch.object(
            bot, "_has_active_country_subscription", new_callable=AsyncMock,
            return_value=False,
        ), patch.object(bot.bot, "answer_pre_checkout_query", new_callable=AsyncMock) as answer:
            self.arun(bot.country_pre_checkout(query))
        self.assertFalse(answer.await_args.kwargs["ok"])
        self.assertIsNotNone(answer.await_args.kwargs["error_message"])

    def test_successful_recurring_payment_activates_user_wide_access_and_sends_flag(self):
        order_id = "order_32345678"
        user_id = 404
        expires = int(1_900_000_000)
        bot._COUNTRY_ORDER_CACHE[order_id] = {
            "user_id": user_id, "currency": "XTR", "amount": 9,
            "status": "pending", "terms_accepted_at": 1,
            "expires_at": 9_999_999_999,
            "requested_lookup_id": "lookup_32345678",
        }
        bot._COUNTRY_LOOKUP_CACHE["lookup_32345678"] = {
            "country": "كندا — Canada 🇨🇦 — المصدر: FinTok",
            "username": "sample", "expires_at": 9_999_999_999,
        }
        message = SimpleNamespace(
            chat=SimpleNamespace(id=user_id), from_user=SimpleNamespace(id=user_id),
            successful_payment=SimpleNamespace(
                invoice_payload=order_id, currency="XTR", total_amount=9,
                telegram_payment_charge_id="charge-first",
                subscription_expiration_date=expires,
                is_recurring=True,
                is_first_recurring=True,
            ),
        )
        with patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send_message, \
             patch.object(bot.bot, "send_photo", new_callable=AsyncMock) as send_photo:
            self.arun(bot.country_successful_payment(message))

        sub = bot._COUNTRY_SUBSCRIPTION_CACHE[str(user_id)]
        self.assertEqual(sub["expires_at"], expires)
        self.assertTrue(self.arun(bot._has_active_country_subscription(user_id)))
        self.assertEqual(bot._COUNTRY_ORDER_CACHE[order_id]["status"], "paid")
        self.assertIn("charge-first", bot._COUNTRY_PROCESSED_PAYMENT_CACHE)
        send_message.assert_awaited_once()
        self.assertIn("تم تفعيل اشتراك", send_message.await_args.kwargs["text"])
        send_photo.assert_awaited_once()
        self.assertEqual(send_photo.await_args.kwargs["photo"], "https://flagcdn.com/w160/ca.png")

    def test_recurring_renewal_extends_subscription_and_is_idempotent(self):
        order_id = "order_42345678"
        user_id = 505
        earlier_expiry = 1_900_000_000
        later_expiry = earlier_expiry + 2_592_000
        bot._COUNTRY_ORDER_CACHE[order_id] = {
            "user_id": user_id, "currency": "XTR", "amount": 29,
            "status": "paid", "terms_accepted_at": 1,
            "expires_at": 1,
            "telegram_payment_charge_id": "charge-first",
        }
        bot._COUNTRY_SUBSCRIPTION_CACHE[str(user_id)] = {
            "user_id": user_id, "expires_at": earlier_expiry,
            "started_at": 1_800_000_000, "telegram_payment_charge_id": "charge-first",
        }
        message = SimpleNamespace(
            chat=SimpleNamespace(id=user_id), from_user=SimpleNamespace(id=user_id),
            successful_payment=SimpleNamespace(
                invoice_payload=order_id, currency="XTR", total_amount=29,
                telegram_payment_charge_id="charge-renewal",
                subscription_expiration_date=later_expiry,
                is_recurring=True,
                is_first_recurring=False,
            ),
        )
        with patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send_message:
            self.arun(bot.country_successful_payment(message))
            first_saved_expiry = bot._COUNTRY_SUBSCRIPTION_CACHE[str(user_id)]["expires_at"]
            self.arun(bot.country_successful_payment(message))

        self.assertEqual(first_saved_expiry, later_expiry)
        self.assertEqual(bot._COUNTRY_SUBSCRIPTION_CACHE[str(user_id)]["expires_at"], later_expiry)
        self.assertEqual(bot._COUNTRY_SUBSCRIPTION_CACHE[str(user_id)]["telegram_payment_charge_id"], "charge-renewal")
        self.assertEqual(send_message.await_count, 1)

    def test_expired_subscription_no_longer_grants_access(self):
        bot._COUNTRY_SUBSCRIPTION_CACHE["606"] = {
            "user_id": 606, "expires_at": 100,
        }
        self.assertFalse(self.arun(bot._has_active_country_subscription(606, now=101)))

    def test_active_subscription_reveals_multiple_search_results_without_payment(self):
        user_id = 707
        bot._COUNTRY_SUBSCRIPTION_CACHE[str(user_id)] = {
            "user_id": user_id, "expires_at": 9_999_999_999,
        }
        calls = [
            self.callback("active-1", "country:lookup_12345678", user_id),
            self.callback("active-2", "country:lookup_abcdefgh", user_id),
        ]
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            side_effect=[self.lookup("Canada 🇨🇦"), self.lookup("Italy 🇮🇹")],
        ), patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "paid", "stars_price": 17},
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
             patch.object(bot.bot, "send_photo", new_callable=AsyncMock) as send_photo, \
             patch.object(bot.bot, "create_invoice_link", new_callable=AsyncMock) as create_link:
            async def scenario():
                for call in calls:
                    await bot.country_button_callback(call)
            self.arun(scenario())

        self.assertEqual(send_photo.await_count, 2)
        create_link.assert_not_awaited()

    def test_admin_command_and_price_controls_keep_free_default(self):
        secret = "TEST_ONLY_ADMIN_COMMAND"
        bot.ADMIN_PANEL_COMMAND = secret
        message = SimpleNamespace(
            text=secret, from_user=SimpleNamespace(id=808),
            chat=SimpleNamespace(id=808, type="private"),
        )
        with patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send:
            self.arun(bot.handle_username_message(message))

        self.assertEqual(bot._ADMIN_USER_ID, 808)
        self.assertIn("مجاني", send.await_args.kwargs["text"])
        self.assertNotIn(secret, send.await_args.kwargs["text"])
        self.assertEqual(
            self.arun(bot._get_country_access_settings()),
            {"mode": "free", "stars_price": 0},
        )

    def test_legacy_per_result_paid_setting_migrates_to_free_monthly_default(self):
        class LegacySnapshot:
            exists = True

            @staticmethod
            def to_dict():
                return {"mode": "paid", "stars_price": 17}

        class LegacyDocument:
            saved = None

            @staticmethod
            def get():
                return LegacySnapshot()

            def set(self, payload):
                self.saved = dict(payload)

        document = LegacyDocument()
        with patch.object(bot, "_FIRESTORE_COUNTRY_SETTINGS_DOCUMENT", document):
            settings = self.arun(bot._get_country_access_settings())

        self.assertEqual(settings, {"mode": "free", "stars_price": 0})
        self.assertEqual(document.saved["billing_model"], "monthly")
        self.assertEqual(document.saved["mode"], "free")
        self.assertEqual(document.saved["stars_price"], 0)

    def test_admin_can_set_monthly_price_then_switch_paid_or_free(self):
        async def scenario():
            await bot._save_country_access_settings("free", 29)
            await bot._save_country_access_settings("paid", 29)
            paid = await bot._get_country_access_settings()
            await bot._save_country_access_settings("free", 29)
            free = await bot._get_country_access_settings()
            return paid, free

        paid, free = self.arun(scenario())
        self.assertEqual(paid, {"mode": "paid", "stars_price": 29})
        self.assertEqual(free, {"mode": "free", "stars_price": 29})
        self.assertIn("شهريًا", bot._country_admin_panel_text(paid))
        self.assertIn("مدة اشتراك Telegram Stars المتكرر ثابتة: 30 يومًا", bot._country_admin_panel_text(paid))
        self.assertIn("لا يدعم Telegram تحديد مدة مخصصة", bot._country_admin_panel_text(paid))
        with self.assertRaises(ValueError):
            self.arun(bot._save_country_access_settings("paid", 0))
        with self.assertRaises(ValueError):
            self.arun(bot._save_country_access_settings("paid", 10_001))

    def test_admin_panel_price_input_and_mode_buttons_work_end_to_end(self):
        bot._ADMIN_USER_ID = 909
        message = SimpleNamespace(
            text="٢٩", from_user=SimpleNamespace(id=909),
            chat=SimpleNamespace(id=909, type="private"),
        )
        price_call = self.callback("admin-price", "country_admin:price", 909)
        price_call.message.message_id = 1
        paid_call = self.callback("admin-paid", "country_admin:paid", 909)
        paid_call.message.message_id = 1
        free_call = self.callback("admin-free", "country_admin:free", 909)
        free_call.message.message_id = 1

        async def scenario():
            with patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
                 patch.object(bot.bot, "send_message", new_callable=AsyncMock):
                await bot.country_admin_callback(price_call)
            self.assertEqual(bot._ADMIN_PENDING_PRICE_USER_ID, 909)
            with patch.object(bot.bot, "send_message", new_callable=AsyncMock):
                await bot.handle_username_message(message)
            after_price = await bot._get_country_access_settings()
            with patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
                 patch.object(bot.bot, "edit_message_text", new_callable=AsyncMock):
                await bot.country_admin_callback(paid_call)
                paid = await bot._get_country_access_settings()
                await bot.country_admin_callback(free_call)
            free = await bot._get_country_access_settings()
            return after_price, paid, free

        price, paid, free = self.arun(scenario())
        self.assertEqual(price, {"mode": "free", "stars_price": 29})
        self.assertEqual(paid, {"mode": "paid", "stars_price": 29})
        self.assertEqual(free, {"mode": "free", "stars_price": 29})


if __name__ == "__main__":
    unittest.main()
