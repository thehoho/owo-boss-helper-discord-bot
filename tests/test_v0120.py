from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs import helper_prefix
from cogs.boss_generator import (
    BossGenerator,
    is_prefix_help_trigger,
    parse_boss_decision_command,
)
from cogs.neon_weapons import parse_neon_command
from cogs.team_templates import parse_team_helper_command
from cogs.ticket_tracker import (
    is_ticket_list_command,
    is_ticket_nickname_command,
    is_ticket_settings_command,
    parse_ticket_lookup_query,
)
from cogs.ui_emojis import MAX_EMOJI_BYTES, discover_emoji_assets, prepare_emoji_image


class HelperPrefixTests(unittest.TestCase):
    def test_normalization_and_command_formatting(self) -> None:
        self.assertEqual(helper_prefix.normalize_helper_prefix(" BH "), "bh")
        self.assertEqual(helper_prefix.normalize_helper_prefix("?"), "?")
        self.assertIsNone(helper_prefix.normalize_helper_prefix("too long"))
        self.assertIsNone(helper_prefix.normalize_helper_prefix("🔥"))
        self.assertEqual(helper_prefix.helper_command("?", "boss", "skip"), "? boss skip")
        self.assertEqual(helper_prefix.helper_alias("bh", "hwd"), "bhwd")

    def test_custom_prefix_rewrites_and_disables_old_h(self) -> None:
        self.assertEqual(
            helper_prefix.canonicalize_helper_command("?WD dagger", "?"),
            "hWD dagger",
        )
        self.assertEqual(helper_prefix.canonicalize_helper_command("HWD", "?"), "")
        self.assertEqual(helper_prefix.canonicalize_helper_command("w boss i", "?"), "w boss i")

    def test_all_command_families_accept_custom_prefix(self) -> None:
        self.assertEqual(parse_boss_decision_command("? boss skip", "?"), "skip")
        self.assertTrue(is_prefix_help_trigger("?help extra text", "?"))
        self.assertEqual(parse_team_helper_command("?T C raid", "?"), ("create", "raid"))
        self.assertEqual(parse_neon_command("?WD dagger", "?"), ("dex", "dagger"))
        self.assertTrue(is_ticket_list_command("?BL", "?"))
        self.assertTrue(is_ticket_settings_command("?BS", "?"))
        self.assertTrue(is_ticket_nickname_command("?BN", "?"))
        self.assertEqual(parse_ticket_lookup_query("?BT member", "?"), "member")

    def test_old_prefix_is_rejected_after_customization(self) -> None:
        self.assertIsNone(parse_boss_decision_command("H boss skip", "?"))
        self.assertFalse(is_prefix_help_trigger("H help", "?"))
        self.assertIsNone(parse_team_helper_command("HT", "?"))
        self.assertIsNone(parse_neon_command("HWD", "?"))
        self.assertFalse(is_ticket_list_command("HBL", "?"))
        self.assertIsNone(parse_ticket_lookup_query("HBT member", "?"))

    def test_database_migration_preserves_owo_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "team_templates.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE team_guild_config (
                        guild_id INTEGER PRIMARY KEY,
                        owo_prefix TEXT NOT NULL DEFAULT 'w',
                        updated_by INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO team_guild_config (guild_id, owo_prefix) VALUES (1, 'o')"
                )
                connection.commit()
            finally:
                connection.close()
            with patch.object(helper_prefix, "DATABASE_FILE", database):
                helper_prefix._PREFIX_CACHE.clear()
                self.assertEqual(helper_prefix.get_guild_helper_prefix_sync(1), "h")
                self.assertEqual(helper_prefix.set_guild_helper_prefix_sync(1, "?", 99), "?")
                self.assertEqual(helper_prefix.get_guild_helper_prefix_sync(1), "?")
            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT owo_prefix, helper_prefix FROM team_guild_config WHERE guild_id = 1"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row, ("o", "?"))


class HelpAndEmojiTests(unittest.TestCase):
    def test_help_embed_stays_within_discord_limits(self) -> None:
        cog = BossGenerator.__new__(BossGenerator)
        cog.ui_emoji = lambda _name, fallback: fallback
        embed = BossGenerator.build_help_embed(cog, "bh", "o")
        self.assertTrue(embed.fields)
        self.assertLessEqual(max(len(field.value) for field in embed.fields), 1024)
        total = len(embed.title or "") + len(embed.description or "")
        total += sum(len(field.name) + len(field.value) for field in embed.fields)
        total += len(embed.footer.text or "")
        self.assertLessEqual(total, 6000)
        self.assertIn("bh help", embed.description or "")

    def test_complete_emoji_catalog_is_discord_safe(self) -> None:
        assets = discover_emoji_assets()
        self.assertEqual(len(assets), 305)
        self.assertEqual(sum(name.startswith("pet_") for name in assets), 228)
        self.assertEqual(sum(name.startswith("weapon_") for name in assets), 29)
        self.assertEqual(sum(name.startswith("passive_") for name in assets), 28)
        self.assertEqual(sum(name.startswith("rank_") for name in assets), 14)
        self.assertEqual(len(assets), len(set(assets)))
        for name, path in assets.items():
            self.assertRegex(name, re.compile(r"^[a-z0-9_]{2,32}$"))
            self.assertTrue(path.is_file(), path)
            self.assertLessEqual(len(prepare_emoji_image(path)), MAX_EMOJI_BYTES, path)


class RandomSkipEmojiTests(unittest.TestCase):
    class FakeEmoji:
        def __init__(self, value: str, *, available: bool = True, usable: bool = True) -> None:
            self.value = value
            self.available = available
            self.usable = usable

        def is_usable(self) -> bool:
            return self.usable

        def __str__(self) -> str:
            return self.value

    def test_random_server_emoji_uses_only_available_usable_entries(self) -> None:
        blocked = self.FakeEmoji("<:blocked:1>", available=False)
        unusable = self.FakeEmoji("<:unusable:2>", usable=False)
        valid = self.FakeEmoji("<:valid:3>")
        guild = type("Guild", (), {"emojis": [blocked, unusable, valid]})()
        bot = type("Bot", (), {"get_guild": lambda _self, _guild_id: guild})()
        cog = BossGenerator.__new__(BossGenerator)
        cog.bot = bot
        self.assertEqual(cog.random_server_emoji(123), "<:valid:3>")


if __name__ == "__main__":
    unittest.main()
