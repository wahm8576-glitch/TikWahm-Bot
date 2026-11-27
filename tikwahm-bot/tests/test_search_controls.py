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
        self.original_last_at = bot._USER_SEARCH_LAST_AT
        self.original_lock = bot._USER_SEARCH_COOLDOWN_LOCK
        self.original_country_lookups = bot._COUNTRY_LOOKUP_CACHE
        self.original_country_orders = bot._COUNTRY_ORDER_CACHE
        self.original_country_entitlements = bot._COUNTRY_ENTITLEMENT_CACHE
        self.original_country_settings = bot._COUNTRY_ACCESS_SETTINGS_CACHE
        self.original_admin_user_id = bot._ADMIN_USER_ID
        self.original_admin_pending_price = bot._ADMIN_PENDING_PRICE_USER_ID
        self.original_admin_command = bot.ADMIN_PANEL_COMMAND
        bot._USER_SEARCH_LAST_AT = OrderedDict()
        bot._USER_SEARCH_COOLDOWN_LOCK = asyncio.Lock()
        bot._COUNTRY_LOOKUP_CACHE = {}
        bot._COUNTRY_ORDER_CACHE = {}
        bot._COUNTRY_ENTITLEMENT_CACHE = set()
        bot._COUNTRY_ACCESS_SETTINGS_CACHE = {"mode": "free", "stars_price": 0}
        bot._ADMIN_USER_ID = None
        bot._ADMIN_PENDING_PRICE_USER_ID = None
        bot.ADMIN_PANEL_COMMAND = ""

    def tearDown(self):
        bot._USER_SEARCH_LAST_AT = self.original_last_at
        bot._USER_SEARCH_COOLDOWN_LOCK = self.original_lock
        bot._COUNTRY_LOOKUP_CACHE = self.original_country_lookups
        bot._COUNTRY_ORDER_CACHE = self.original_country_orders
        bot._COUNTRY_ENTITLEMENT_CACHE = self.original_country_entitlements
        bot._COUNTRY_ACCESS_SETTINGS_CACHE = self.original_country_settings
        bot._ADMIN_USER_ID = self.original_admin_user_id
        bot._ADMIN_PENDING_PRICE_USER_ID = self.original_admin_pending_price
        bot.ADMIN_PANEL_COMMAND = self.original_admin_command

    @staticmethod
    def arun(awaitable):
        return asyncio.run(awaitable)

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

    def test_cooldown_is_per_user_and_expires_after_fifteen_seconds(self):
        async def scenario():
            first_user = await bot._reserve_user_search(101, now=100)
            same_user_early = await bot._reserve_user_search(101, now=104)
            other_user = await bot._reserve_user_search(202, now=104)
            almost_ready = await bot._reserve_user_search(101, now=114.999)
            ready = await bot._reserve_user_search(101, now=115)
            return first_user, same_user_early, other_user, almost_ready, ready

        result = self.arun(scenario())
        self.assertEqual(result, (0, 11, 0, 1, 0))

    def test_blocked_attempt_does_not_extend_cooldown(self):
        async def scenario():
            await bot._reserve_user_search("user-a", now=50)
            blocked = await bot._reserve_user_search("user-a", now=60)
            still_blocked = await bot._reserve_user_search("user-a", now=64)
            allowed = await bot._reserve_user_search("user-a", now=65)
            return blocked, still_blocked, allowed

        self.assertEqual(self.arun(scenario()), (5, 1, 0))

    def test_cooldown_sends_one_gif_and_starts_countdown_on_that_message(self):
        source_message = SimpleNamespace(
            chat=SimpleNamespace(id=123), message_id=456
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

        self.assertEqual(sorted(self.arun(scenario())), [0, 15])

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

    def test_free_country_button_reveals_country_in_callback_alert(self):
        call = SimpleNamespace(
            id="callback-1",
            data="country:lookup_12345678",
            from_user=SimpleNamespace(id=101),
            message=SimpleNamespace(chat=SimpleNamespace(id=101, type="private")),
        )
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"country": "كندا 🇨🇦 — المصدر: FinTok", "username": "sample"},
        ), patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "free", "stars_price": 0},
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock) as answer:
            self.arun(bot.country_button_callback(call))

        self.assertEqual(answer.await_args.kwargs["show_alert"], True)
        self.assertIn("كندا", answer.await_args.kwargs["text"])

    def test_paid_country_button_creates_single_item_xtr_invoice_without_revealing_country(self):
        call = SimpleNamespace(
            id="callback-2",
            data="country:lookup_12345678",
            from_user=SimpleNamespace(id=202),
            message=SimpleNamespace(chat=SimpleNamespace(id=202, type="private")),
        )
        with patch.object(
            bot, "_get_country_lookup", new_callable=AsyncMock,
            return_value={"country": "كندا 🇨🇦 — المصدر: FinTok", "username": "sample"},
        ), patch.object(
            bot, "_get_country_access_settings", new_callable=AsyncMock,
            return_value={"mode": "paid", "stars_price": 17},
        ), patch.object(
            bot, "_has_country_entitlement", new_callable=AsyncMock, return_value=False,
        ), patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock) as answer, \
             patch.object(bot.bot, "send_invoice", new_callable=AsyncMock) as invoice:
            self.arun(bot.country_button_callback(call))

        invoice.assert_awaited_once()
        args = invoice.await_args.kwargs
        self.assertEqual(args["currency"], "XTR")
        self.assertEqual(args["provider_token"], "")
        self.assertEqual(len(args["prices"]), 1)
        self.assertEqual(args["prices"][0].amount, 17)
        self.assertEqual(args["start_parameter"], args["invoice_payload"])
        self.assertNotIn("كندا", answer.await_args.kwargs.get("text", ""))
        self.assertEqual(bot._COUNTRY_ORDER_CACHE[args["invoice_payload"]]["user_id"], 202)

    def test_pre_checkout_checks_payload_owner_currency_amount_and_expiry(self):
        order_id = "order_12345678"
        bot._COUNTRY_ORDER_CACHE[order_id] = {
            "lookup_id": "lookup_12345678", "country": "كندا", "username": "sample",
            "user_id": 303, "currency": "XTR", "amount": 21, "status": "pending",
            "expires_at": 9_999_999_999,
        }
        valid_query = SimpleNamespace(
            id="pre-1", invoice_payload=order_id, currency="XTR", total_amount=21,
            from_user=SimpleNamespace(id=303),
        )
        invalid_query = SimpleNamespace(
            id="pre-2", invoice_payload=order_id, currency="XTR", total_amount=22,
            from_user=SimpleNamespace(id=303),
        )
        with patch.object(bot.bot, "answer_pre_checkout_query", new_callable=AsyncMock) as answer:
            self.arun(bot.country_pre_checkout(valid_query))
            self.assertTrue(answer.await_args.kwargs["ok"])
            self.arun(bot.country_pre_checkout(invalid_query))
            self.assertFalse(answer.await_args.kwargs["ok"])
            self.assertIsNotNone(answer.await_args.kwargs["error_message"])

    def test_successful_payment_is_required_before_country_delivery(self):
        order_id = "order_22345678"
        bot._COUNTRY_ORDER_CACHE[order_id] = {
            "lookup_id": "lookup_22345678", "country": "كندا 🇨🇦 — المصدر: FinTok",
            "username": "sample", "user_id": 404, "currency": "XTR", "amount": 9,
            "status": "pending", "expires_at": 9_999_999_999,
        }
        message = SimpleNamespace(
            chat=SimpleNamespace(id=404), from_user=SimpleNamespace(id=404),
            successful_payment=SimpleNamespace(
                invoice_payload=order_id, currency="XTR", total_amount=9,
                telegram_payment_charge_id="charge-123",
            ),
        )
        with patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send:
            self.arun(bot.country_successful_payment(message))

        send.assert_awaited_once()
        self.assertIn("كندا", send.await_args.kwargs["text"])
        self.assertEqual(bot._COUNTRY_ORDER_CACHE[order_id]["status"], "paid")
        self.assertIn("lookup_22345678_404", bot._COUNTRY_ENTITLEMENT_CACHE)

    def test_admin_command_is_exact_and_settings_remain_free_by_default(self):
        secret = "TEST_ONLY_ADMIN_COMMAND"
        bot.ADMIN_PANEL_COMMAND = secret
        message = SimpleNamespace(
            text=secret, from_user=SimpleNamespace(id=505),
            chat=SimpleNamespace(id=505, type="private"),
        )
        with patch.object(bot.bot, "send_message", new_callable=AsyncMock) as send:
            self.arun(bot.handle_username_message(message))

        self.assertEqual(bot._ADMIN_USER_ID, 505)
        self.assertIn("مجاني", send.await_args.kwargs["text"])
        self.assertNotIn(secret, send.await_args.kwargs["text"])
        self.assertEqual(
            self.arun(bot._get_country_access_settings()),
            {"mode": "free", "stars_price": 0},
        )

    def test_admin_can_set_price_then_enable_paid_mode_or_keep_free(self):
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

    def test_admin_panel_price_input_and_mode_buttons_work_end_to_end(self):
        bot._ADMIN_USER_ID = 606
        message = SimpleNamespace(
            text="٢٩", from_user=SimpleNamespace(id=606),
            chat=SimpleNamespace(id=606, type="private"),
        )
        price_call = SimpleNamespace(
            id="admin-price", data="country_admin:price",
            from_user=SimpleNamespace(id=606),
            message=SimpleNamespace(chat=SimpleNamespace(id=606, type="private"), message_id=1),
        )
        paid_call = SimpleNamespace(
            id="admin-paid", data="country_admin:paid",
            from_user=SimpleNamespace(id=606), message=price_call.message,
        )
        free_call = SimpleNamespace(
            id="admin-free", data="country_admin:free",
            from_user=SimpleNamespace(id=606), message=price_call.message,
        )

        async def scenario():
            with patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
                 patch.object(bot.bot, "send_message", new_callable=AsyncMock):
                await bot.country_admin_callback(price_call)
            self.assertEqual(bot._ADMIN_PENDING_PRICE_USER_ID, 606)
            with patch.object(bot.bot, "send_message", new_callable=AsyncMock):
                await bot.handle_username_message(message)
            settings_after_price = await bot._get_country_access_settings()
            with patch.object(bot.bot, "answer_callback_query", new_callable=AsyncMock), \
                 patch.object(bot.bot, "edit_message_text", new_callable=AsyncMock):
                await bot.country_admin_callback(paid_call)
                settings_paid = await bot._get_country_access_settings()
                await bot.country_admin_callback(free_call)
            settings_free = await bot._get_country_access_settings()
            return settings_after_price, settings_paid, settings_free

        price, paid, free = self.arun(scenario())
        self.assertEqual(price, {"mode": "free", "stars_price": 29})
        self.assertEqual(paid, {"mode": "paid", "stars_price": 29})
        self.assertEqual(free, {"mode": "free", "stars_price": 29})


if __name__ == "__main__":
    unittest.main()
