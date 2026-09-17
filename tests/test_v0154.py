from __future__ import annotations

import unittest
from types import SimpleNamespace

from cogs.boss_generator import BossGenerator
from cogs.boss_notifications import BossNotifications, PREFIX_ALIASES
from cogs.helper_prefix import parse_helper_command_argument
from cogs.tapdeck import TAPDECK_GOOGLE_PLAY_URL, TapDeckLinks, build_tapdeck_embed


class CompactBossNotifyTests(unittest.IsolatedAsyncioTestCase):
    def test_spaced_and_compact_forms_follow_the_configured_prefix(self) -> None:
        cases = (
            ("h boss notify", "h", ""),
            ("hboss notify", "h", ""),
            ("b boss notify wc x2", "b", "wc x2"),
            ("bboss notify wc x2", "b", "wc x2"),
        )
        for content, prefix, expected in cases:
            with self.subTest(content=content, prefix=prefix):
                self.assertEqual(
                    parse_helper_command_argument(content, prefix, PREFIX_ALIASES),
                    expected,
                )
        self.assertIsNone(
            parse_helper_command_argument("hboss notify", "b", PREFIX_ALIASES)
        )

    async def test_notification_guide_documents_both_forms(self) -> None:
        cog = BossNotifications.__new__(BossNotifications)
        cog.store = SimpleNamespace(list_subscriptions=lambda _guild, _user: [])
        embed = await cog.build_guide_embed(1, 2, "b")
        text = "\n".join(field.value for field in embed.fields)
        self.assertIn("`b boss notify`", text)
        self.assertIn("`bboss notify`", text)
        self.assertIn("/boss-notify", text)


class GooglePlaySurfaceTests(unittest.TestCase):
    def test_tapdeck_card_uses_the_verified_android_package_url(self) -> None:
        self.assertEqual(
            TAPDECK_GOOGLE_PLAY_URL,
            "https://play.google.com/store/apps/details?id=app.tapdeck.keyboard.lite",
        )
        self.assertEqual(str(build_tapdeck_embed().url), TAPDECK_GOOGLE_PLAY_URL)
        urls = {str(item.url) for item in TapDeckLinks().children}
        self.assertIn(TAPDECK_GOOGLE_PLAY_URL, urls)

    def test_main_help_mentions_compact_notify_and_google_play(self) -> None:
        cog = BossGenerator.__new__(BossGenerator)
        cog.ui_emoji = lambda _name, fallback: fallback
        embed = BossGenerator.build_help_embed(cog, "b", "o")
        text = "\n".join(field.value for field in embed.fields)
        self.assertIn("`b boss notify`", text)
        self.assertIn("`bboss notify`", text)
        self.assertIn("Google Play", text)
        self.assertIn("GitHub APK fallback", text)


if __name__ == "__main__":
    unittest.main()
