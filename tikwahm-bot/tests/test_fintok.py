import hashlib
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:unit-test-token-not-for-network-use")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


class FinTokFormattingTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "fullname": "Exact Display Name",
            "username": "exact_user",
            "profile": "https://cdn.example.test/avatar/exact_user.jpg",
            "following": 1234,
            "followers": 56789,
            "likes": 987654,
            "userid": "9007199254740993",
            "private": False,
            "language": "en-US",
            "created_time": "03/07/2021, 04:05:06 PM",
            "last_change_name": "11/29/2024, 09:08:07 AM",
        }
        self.security = {
            "is_email": "true",
            "is_mobile": "false",
            "is_password": True,
            "is_passkey": False,
            "oauth": {"is_enabled": True, "platforms": ["Google", "Facebook"]},
            "account_status": {
                "is_logged_in": True,
                "is_ftc_user": False,
                "is_most_device": True,
                "is_org_account": False,
            },
        }

    def test_arabic_labels_preserve_every_value_and_site_order(self):
        result = bot.format_fintok_result(
            "exact_user", self.profile, "United States", "Canada", self.security
        )
        self.assertEqual(
            set(result), {"text", "markdown", "avatar_url", "username", "original_country"}
        )
        self.assertEqual(result["avatar_url"], self.profile["profile"])
        self.assertEqual(result["username"], "exact_user")
        self.assertEqual(result["original_country"], "كندا — Canada 🇨🇦 — المصدر: FinTok")
        combined = f'{result["text"]}\n{result["markdown"]}'
        expected_labels = [
            "يتابع", "المتابعون", "الإعجابات", "معرّف المستخدم", "نوع الحساب",
            "المنطقة النشطة", "اللغة", "وقت إنشاء الحساب",
            "آخر تغيير للاسم", "البريد الإلكتروني مرتبط", "رقم الجوال مرتبط",
            "كلمة المرور مُعيّنة", "مفتاح المرور (Passkey)", "OAuth مفعّل",
            "المنصات المتصلة", "حالة تسجيل الدخول", "مستخدم FTC",
            "حالة تعدد الأجهزة", "حساب مؤسسة",
        ]
        positions = [combined.index(label) for label in expected_labels]
        self.assertEqual(positions, sorted(positions))
        for value in (
            "1.23K", "56.79K", "987.65K", "9007199254740993", "عام (Public Account)",
            "الولايات المتحدة — United States 🇺🇸 — المصدر: FinTok",
            "الإنجليزية (en-US)", "03/07/2021, 04:05:06 PM", "11/29/2024, 09:08:07 AM",
            "Google, Facebook", "نعم", "لا",
        ):
            self.assertIn(value, combined)
        self.assertNotIn("Yes", combined)
        self.assertNotIn("No", combined)
        self.assertNotIn("Canada", combined)
        self.assertIn("لم يُتحقق من صحتها", combined)
        self.assertIn("**Exact Display Name**", result["markdown"])
        table_lines = [line for line in result["markdown"].splitlines() if line.startswith("|")]
        self.assertEqual(len(table_lines), 21)  # header, separator, and nineteen field rows
        self.assertTrue(all(line.count("|") == 3 for line in table_lines))

    def test_private_accounts_and_missing_fields_are_not_fabricated(self):
        profile = {"fullname": "Only Name", "username": "sparse_user", "profile": "", "private": True}
        result = bot.format_fintok_result("sparse_user", profile, None, None, None)
        combined = f'{result["text"]}\n{result["markdown"]}'
        self.assertIsNone(result["original_country"])
        self.assertEqual(result["avatar_url"], "")
        self.assertIn("Only Name", combined)
        self.assertIn("sparse_user", combined)
        self.assertIn("نوع الحساب", combined)
        self.assertIn("خاص (Private Account)", combined)
        self.assertNotIn("المنطقة النشطة", combined)
        self.assertNotIn("منطقة التسجيل", combined)
        self.assertNotIn("Unavailable", combined)
        self.assertNotIn("N/A", combined)
        self.assertNotIn("None", combined)

    def test_signature_is_sha256_of_ordered_parts_timestamp_and_public_salt(self):
        timestamp = "1712345678"
        parts = ("exact_user", "profile-id-42", "nonce")
        expected_input = "".join(parts) + timestamp + bot.FINTOK_SIGNATURE_SALT
        expected = hashlib.sha256(expected_input.encode("utf-8")).hexdigest()
        self.assertEqual(bot._fintok_signature(timestamp, *parts), expected)


if __name__ == "__main__":
    unittest.main()
