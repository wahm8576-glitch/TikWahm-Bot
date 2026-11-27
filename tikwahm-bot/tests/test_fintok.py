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
            "is_email": True,
            "is_mobile": False,
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

    def test_outputs_all_fields_in_exact_site_order_and_preserves_display_values(self):
        result = bot.format_fintok_result(
            "exact_user", self.profile, "United States", "Canada", self.security
        )
        self.assertEqual(set(result), {"text", "markdown", "avatar_url"})
        self.assertEqual(result["avatar_url"], self.profile["profile"])
        combined = f'{result["text"]}\n{result["markdown"]}'
        expected_labels = [
            "Following", "Followers", "Likes", "User ID", "Account Type",
            "Active Region", "Registered Region", "Language", "Created Time",
            "Last Name Change", "Email Linked", "Mobile Linked", "Password Set",
            "is PassKey", "OAuth Enabled", "Connected Platforms", "Logged In",
            "FTC User", "Most Device", "Organization Account",
        ]
        positions = [combined.index(label) for label in expected_labels]
        self.assertEqual(positions, sorted(positions))
        for value in (
            "1.23K", "56.79K", "987.65K", "9007199254740993", "Public Account",
            "United States 🇺🇸", "Canada 🇨🇦", "en-US", "03/07/2021, 04:05:06 PM",
            "11/29/2024, 09:08:07 AM", "Google, Facebook", "Yes", "No",
        ):
            self.assertIn(value, combined)
        self.assertIn("لم يُتحقق من صحتها", combined)

    def test_private_accounts_and_missing_fields_are_not_fabricated(self):
        profile = {"fullname": "Only Name", "username": "sparse_user", "profile": "", "private": True}
        result = bot.format_fintok_result("sparse_user", profile, None, None, None)
        combined = f'{result["text"]}\n{result["markdown"]}'
        self.assertEqual(result["avatar_url"], "")
        self.assertIn("Only Name", combined)
        self.assertIn("sparse_user", combined)
        self.assertIn("Account Type", combined)
        self.assertIn("Private Account", combined)
        self.assertNotIn("Active Region", combined)
        self.assertNotIn("Registered Region", combined)
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
