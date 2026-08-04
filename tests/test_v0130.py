from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.animal_dex import (
    AnimalDex,
    AnimalDexRecord,
    AnimalDexStore,
    clean_dex_description,
    is_owo_dex_refusal,
    parse_owo_animal_dex,
    parse_owo_dex_request,
)
from cogs.boss_generator import BossGenerator, is_prefix_boss_report_trigger
from cogs.game_catalog import (
    PASSIVES,
    RANKS,
    WEAPONS,
    resolve_passive,
    resolve_rank,
    resolve_special_animal,
    resolve_weapon,
    special_animals,
)
from cogs.helper_prefix import parse_helper_command_argument
from cogs.team_guides import (
    GuideAliasConflict,
    GuideDraft,
    GuideSlot,
    TeamGuideStore,
    build_guide_embed,
)
from cogs.ui_emojis import UIEmojiManager


class AnimalDexParserTests(unittest.TestCase):
    @staticmethod
    def message(title: str, description: str) -> SimpleNamespace:
        embed = discord.Embed(title=title, description=description)
        return SimpleNamespace(content="", embeds=[embed])

    def test_parses_normal_owo_dex_without_storing_requester_count(self) -> None:
        message = self.message(
            "<:snail:100> snail",
            "*The slowest animal, but the toughest in the zoo*\n\n"
            "Count: 23,565/232,877\n"
            "Rank: <:common:101> common\n"
            "Rarity: 28,152,132,856 total caught\n"
            "Alias: snail, slug\n"
            "Points: 1\n"
            "Sell: 1 Cowoncy | 19,347 sold\n"
            "Sacrifice: 1 Essence | 189,965 killed\n"
            "<:hp:1> 8 <:att:2> 1 <:pr:3> 2\n"
            "<:wp:4> 3 <:mag:5> 5 <:mr:6> 1",
        )
        record = parse_owo_animal_dex(message)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.animal_key, "snail")
        self.assertEqual(record.rank, "common")
        self.assertEqual(record.total_caught, 28152132856)
        self.assertEqual((record.hp, record.strength, record.pr), (8, 1, 2))
        self.assertEqual((record.wp, record.mag, record.mr), (3, 5, 1))
        self.assertEqual(record.emoji_name, "snail")
        self.assertNotIn("Count", record.description)

    def test_bold_count_starts_the_private_details_block(self) -> None:
        value = "A public description\n\n**Count:** 3/3\n**Rank:** special"
        self.assertEqual(clean_dex_description(value), "A public description")

    def test_parses_custom_patreon_pasted_layout(self) -> None:
        message = self.message(
            ":Arya: Arya",
            "Created by Arjun and Riya\nfor being a February 2024 Patreon!\n\n"
            "Count: 0/1\nRank: :cpatreon: cpatreon\n"
            "Rarity: 1,104 total caught\nAlias: Arya, arya, Riya\n"
            "Points: 25000\nSell: 50000 Cowoncy | 1 sold\nSacrifice: ???\n"
            ":hp: 11 :att: 1 :pr: 3\n:wp: 3 :mag: 1 :mr: 1",
        )
        record = parse_owo_animal_dex(message)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.display_name, "Arya")
        self.assertEqual(record.rank, "custom_patreon")
        self.assertEqual(record.points, 25000)
        self.assertEqual(record.total_caught, 1104)
        self.assertEqual(record.hp, 11)

    def test_request_and_refusal_matching(self) -> None:
        self.assertEqual(parse_owo_dex_request("wd pridebee", "w"), "pridebee")
        self.assertEqual(parse_owo_dex_request("owo dex pridebee", "x"), "pridebee")
        self.assertIsNone(parse_owo_dex_request("wd pridebee", "x"))
        self.assertTrue(is_owo_dex_refusal("Hassaan, I couldn't find this animal in your zoo!"))

    def test_animal_lookup_does_not_take_over_neon_dex_command(self) -> None:
        aliases = {
            "h animal dex",
            "hanimal dex",
            "h pet dex",
            "hpet dex",
            "h adex",
            "hadex",
            "h ad",
            "had",
        }
        self.assertEqual(
            parse_helper_command_argument("?animal dex pridebee", "?", aliases),
            "pridebee",
        )
        self.assertIsNone(parse_helper_command_argument("?dex dagger", "?", aliases))
        self.assertEqual(parse_helper_command_argument("?ad pridebee", "?", aliases), "pridebee")

    def test_compact_dex_embed_uses_official_stat_order(self) -> None:
        manager = UIEmojiManager.__new__(UIEmojiManager)
        manager.emojis = {
            f"stat_{key}": discord.PartialEmoji(name=f"stat_{key}", id=index)
            for index, key in enumerate(("hp", "att", "pr", "wp", "mag", "mr"), 100)
        }
        bot = SimpleNamespace(ui_emoji_manager=manager)
        cog = AnimalDex.__new__(AnimalDex)
        cog.bot = bot
        record = AnimalDexRecord(
            animal_key="test",
            display_name="Test",
            rank="special",
            description="Public prose\n\n**Count:** 9/9\n**Rank:** special",
            aliases=("Test", "test_alias"),
            total_caught=1234,
            rarity_text="1,234 total caught",
            points=500,
            sell_text="6000 Cowoncy",
            sacrifice_text="5000 Essence",
            hp=4,
            strength=1,
            pr=10,
            wp=1,
            mag=1,
            mr=3,
            emoji_name="",
            emoji_id=None,
            emoji_animated=False,
            image_url="",
            source="owo",
            updated_at=1,
        )
        embed = cog.build_embed(record)
        description = embed.description or ""
        self.assertFalse(embed.fields)
        self.assertNotIn("Count", description)
        self.assertIn("`test_alias`", description)
        positions = [description.index(f"<:stat_{key}:{index}>") for index, key in enumerate(("hp", "att", "pr", "wp", "mag", "mr"), 100)]
        self.assertEqual(positions, sorted(positions))


class AnimalDexSilenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_owo_dex_request_does_not_trigger_helper_output(self) -> None:
        async def unexpected_output(*args: object, **kwargs: object) -> None:
            raise AssertionError("ordinary OwO Dex activity must remain silent")

        message = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            author=SimpleNamespace(bot=False),
            content="wd pridebee",
            channel=SimpleNamespace(send=unexpected_output),
            reply=unexpected_output,
        )
        cog = AnimalDex.__new__(AnimalDex)
        with patch(
            "cogs.animal_dex.get_guild_helper_prefix",
            new=AsyncMock(return_value="h"),
        ):
            await cog.on_message(message)


class CatalogTests(unittest.TestCase):
    def test_complete_reference_catalog(self) -> None:
        self.assertEqual(len(special_animals()), 173)
        self.assertEqual(len(WEAPONS), 29)
        self.assertEqual(len(PASSIVES), 28)
        self.assertEqual(len(RANKS), 14)
        pridebee = resolve_special_animal("pridebee")
        self.assertIsNotNone(pridebee)
        self.assertEqual(pridebee["name"], "2022pridebee")
        self.assertEqual(resolve_weapon("dagger").key, "pd")
        self.assertEqual(resolve_passive("mana tap").key, "mtap")
        self.assertEqual(resolve_rank("M").key, "mythical")

    def test_seeded_animal_store_is_searchable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AnimalDexStore(Path(directory) / "animal_dex.db")
            store.initialize()
            record = store.find("pridebee")
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.display_name, "2022pridebee")
            self.assertEqual(record.mag, 13)
            connection = sqlite3.connect(store.path)
            try:
                count = connection.execute("SELECT COUNT(*) FROM animals").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 173)


class TrustedGuideTests(unittest.TestCase):
    @staticmethod
    def complete_draft(editor_id: int = 1) -> GuideDraft:
        return GuideDraft(
            editor_id=editor_id,
            name="Double Dagger Crune",
            aliases=["ddc", "double_dagger_crune"],
            categories=["boss"],
            authors="Arjun / Riya",
            description="A trusted boss team guide.",
            viability=5,
            ease=4,
            slots={
                1: GuideSlot(1, "lizard", 50, "mythical", "pd + mtap + crit @ legendary"),
                2: GuideSlot(2, "2022pridebee", 50, "special", "crune + res @ fabled"),
                3: GuideSlot(3, "hedgebot", 50, "legendary", "hstaff + mag @ mythical"),
            },
        )

    def test_expert_permission_alias_uniqueness_and_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TeamGuideStore(Path(directory) / "team_guides.db")
            store.initialize()
            store.set_expert(1, "Expert", True, 99)
            self.assertTrue(store.is_expert(1))
            guide = store.save(self.complete_draft(), 1)
            self.assertEqual(guide.version, 1)
            self.assertEqual(store.find("ddc").guide_id, guide.guide_id)
            revised = self.complete_draft()
            revised.guide_id = guide.guide_id
            revised.description = "Updated after a meta change."
            guide = store.save(revised, 2)
            self.assertEqual(guide.version, 2)
            self.assertEqual(guide.updated_by, 2)
            self.assertEqual(store.list_guides("Arjun")[0].guide_id, guide.guide_id)
            self.assertEqual(store.list_guides("boss")[0].guide_id, guide.guide_id)
            with self.assertRaises(GuideAliasConflict):
                store.save(self.complete_draft(editor_id=3), 3)

    def test_visual_guide_embed_stays_within_discord_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TeamGuideStore(Path(directory) / "team_guides.db")
            store.initialize()
            guide = store.save(self.complete_draft(), 1)
        bot = SimpleNamespace(ui_emoji_manager=None)
        embed = build_guide_embed(bot, guide)
        self.assertLessEqual(len(embed.description or ""), 4096)
        self.assertTrue(embed.fields)
        self.assertLessEqual(max(len(field.value) for field in embed.fields), 1024)
        self.assertIn("Double Dagger Crune", embed.title or "")


class BossDecisionEmojiTests(unittest.TestCase):
    def test_hit_and_skip_stickies_keep_their_selected_emoji(self) -> None:
        cog = BossGenerator.__new__(BossGenerator)
        self.assertEqual(
            cog.boss_decision_default_content({"boss_decision": "hit", "boss_hit_emoji": "<:go:1>"}),
            "# HIT <:go:1>",
        )
        self.assertEqual(
            cog.boss_decision_default_content({"boss_decision": "skip", "boss_skip_emoji": "<:no:2>"}),
            "# SKIP <:no:2>",
        )


class BossReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_rollover_keeps_latest_report_without_report_channel(self) -> None:
        cog = BossGenerator.__new__(BossGenerator)
        cog.cooldown_config = {
            "1": {
                "boss_report_cycle": "2026-08-01",
                "boss_report_defeated": 10,
                "boss_report_escaped": 20,
            }
        }
        with patch("cogs.boss_generator.save_cooldown_config"):
            await cog.rollover_boss_report(1, "2026-08-02")
        self.assertEqual(
            cog.latest_boss_report(cog.cooldown_config["1"]),
            ("2026-08-01", 10, 20),
        )
        embed = cog.build_boss_daily_report_embed(
            "2026-08-01", 10, 20, title="Latest Guild Boss Report"
        )
        self.assertEqual(embed.title, "Latest Guild Boss Report")
        self.assertEqual([field.value for field in embed.fields], ["30", "10", "20"])

    async def test_report_command_uses_custom_helper_prefix(self) -> None:
        self.assertTrue(is_prefix_boss_report_trigger("? boss report", "?"))
        self.assertTrue(is_prefix_boss_report_trigger("?boss report", "?"))
        self.assertFalse(is_prefix_boss_report_trigger("h boss report", "?"))
        self.assertFalse(is_prefix_boss_report_trigger("? boss rebirth", "?"))


if __name__ == "__main__":
    unittest.main()
