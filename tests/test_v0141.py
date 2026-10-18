from __future__ import annotations

import unittest
from unittest.mock import patch

from cogs.bot_info import BOT_VERSION
from cogs.boss_generator import BossGenerator
from cogs.helper_prefix import parse_helper_command_argument
from cogs.rng import (
    MAX_ABSOLUTE_BOUND,
    RNG_PREFIX_ALIASES,
    RngInputError,
    parse_compact_integer,
    parse_rng_range,
    roll_inclusive,
)
from cogs.tapdeck import (
    TAPDECK_GOOGLE_PLAY_URL,
    TAPDECK_PREFIX_ALIASES,
    TapDeckInfo,
    TapDeckLinks,
    build_tapdeck_embed,
)
from cogs.team_templates import parse_team_helper_command


class RngTests(unittest.TestCase):
    def test_compact_values_are_exact_whole_numbers(self) -> None:
        self.assertEqual(parse_compact_integer("100K"), 100_000)
        self.assertEqual(parse_compact_integer("2.5M"), 2_500_000)
        self.assertEqual(parse_compact_integer("1,000,000"), 1_000_000)
        self.assertEqual(parse_compact_integer("-1.25B"), -1_250_000_000)

    def test_one_bound_defaults_to_one_and_two_bounds_are_inclusive(self) -> None:
        self.assertEqual(parse_rng_range("1M"), (1, 1_000_000))
        self.assertEqual(parse_rng_range("100K, 2.5M"), (100_000, 2_500_000))
        self.assertEqual(
            parse_rng_range("min 1,000,000, max 2,000,000"),
            (1_000_000, 2_000_000),
        )

    def test_invalid_or_reversed_bounds_are_rejected(self) -> None:
        with self.assertRaises(RngInputError):
            parse_rng_range("2M, 1M")
        with self.assertRaises(RngInputError):
            parse_compact_integer(str(MAX_ABSOLUTE_BOUND + 1))
        with self.assertRaises(RngInputError):
            parse_compact_integer("2.25")

    def test_roll_is_inclusive_and_uses_integer_offset(self) -> None:
        with patch("cogs.rng.secrets.randbelow", return_value=0) as mocked:
            self.assertEqual(roll_inclusive(100, 200), 100)
            mocked.assert_called_once_with(101)
        with patch("cogs.rng.secrets.randbelow", return_value=100):
            self.assertEqual(roll_inclusive(100, 200), 200)

    def test_rng_prefix_respects_custom_server_prefix(self) -> None:
        self.assertEqual(
            parse_helper_command_argument("brng 1M", "b", RNG_PREFIX_ALIASES),
            "1M",
        )
        self.assertIsNone(
            parse_helper_command_argument("hrng 1M", "b", RNG_PREFIX_ALIASES)
        )


class TapDeckTests(unittest.TestCase):
    def test_google_play_is_the_only_public_install_link(self) -> None:
        urls = {str(child.url) for child in TapDeckLinks().children}
        self.assertEqual(urls, {TAPDECK_GOOGLE_PLAY_URL})

    def test_approved_public_copy_is_concise_and_factual(self) -> None:
        embed = build_tapdeck_embed()
        text = "\n".join(
            [embed.title or "", embed.description or ""]
            + [field.value for field in embed.fields]
        )
        self.assertEqual(embed.title, "📱 TapDeck Lite, one tap, one command")
        self.assertEqual(
            embed.description,
            "An offline Android shortcut keyboard for command-based chats and bots. "
            "Build a customizable deck of up to 40 keys, then insert or send a "
            "saved command with one tap.",
        )
        self.assertIn("one tap = one command", text.casefold())
        self.assertIn("no data collected or shared", text.casefold())
        self.assertIn("requests no android permissions", text.casefold())
        self.assertIn("google play", text.casefold())
        self.assertIn("40 commands", text.casefold())
        self.assertIn("send delays and cooldowns", text.casefold())
        self.assertIn("slash-command", text.casefold())
        self.assertIn("colors", text.casefold())
        self.assertNotIn("github", text.casefold())
        self.assertEqual(str(embed.url), TAPDECK_GOOGLE_PLAY_URL)
        self.assertIn("shared with owo's staff team", text.casefold())
        self.assertIn("confirmed as allowed under the rules at the time", text.casefold())
        self.assertNotIn("reviewed by an owo administrator", text.casefold())
        self.assertNotIn("automated", text.casefold())
        self.assertNotIn("automation", text.casefold())
        self.assertNotIn("cached for", text.casefold())

    def test_tapdeck_prefix_and_slash_names_are_spelled_correctly(self) -> None:
        self.assertEqual(
            parse_helper_command_argument("b grind", "b", TAPDECK_PREFIX_ALIASES),
            "",
        )
        self.assertEqual(
            parse_helper_command_argument("btapdeck", "b", TAPDECK_PREFIX_ALIASES),
            "",
        )

        self.assertEqual(TapDeckInfo.tapdeck.name, "tapdeck")

    def test_compact_tapdeck_aliases_do_not_become_team_queries(self) -> None:
        self.assertIsNone(parse_team_helper_command("htapdeck", "h"))
        self.assertIsNone(parse_team_helper_command("btapdeck", "b"))
        self.assertEqual(
            parse_team_helper_command("ht apdeck", "h"),
            ("open_query", "apdeck"),
        )
        self.assertEqual(
            parse_team_helper_command("htc raid", "h"),
            ("create", "raid"),
        )
        self.assertEqual(parse_team_helper_command("ht3", "h"), ("open", "3"))


class PublicSurfaceTests(unittest.TestCase):
    def test_version_and_help_include_the_whole_batch(self) -> None:
        self.assertEqual(BOT_VERSION, "0.16.13-beta")
        cog = BossGenerator.__new__(BossGenerator)
        cog.ui_emoji = lambda _name, fallback: fallback
        embed = BossGenerator.build_help_embed(cog, "b", "o")
        text = "\n".join(field.value for field in embed.fields)
        self.assertIn("brng", text)
        self.assertIn("b grind", text)
        self.assertIn("b tapdeck", text)
        self.assertIn("/tapdeck", text)
        self.assertIn("staff team reviewed", text.casefold())
        self.assertIn("allowed under the rules at the time", text.casefold())
        self.assertNotIn("an owo administrator", text.casefold())
        self.assertIn("Team 1", text)
        self.assertIn("Team 2", text)
        self.assertIn("b boss notify", text)
        self.assertIn("WS", text)
        self.assertIn("BWC", text)
        self.assertLessEqual(max(len(field.value) for field in embed.fields), 1024)
        total = len(embed.title or "") + len(embed.description or "")
        total += sum(len(field.name) + len(field.value) for field in embed.fields)
        self.assertLessEqual(total, 6000)


if __name__ == "__main__":
    unittest.main()
