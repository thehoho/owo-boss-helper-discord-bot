from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

from cogs.boss_generator import BossGenerator
from cogs.bot_info import BOT_VERSION, BotInfo
from cogs.game_catalog import resolve_passive, resolve_weapon
from cogs.team_guides import (
    FullGuideView,
    GuideDraft,
    GuideEditorView,
    GuideSlot,
    TeamGuideStore,
    build_emoji_variable_help_embed,
    build_full_guide_embeds,
    guide_variable_emoji_key,
    render_guide_markdown,
    unresolved_guide_variables,
)
from cogs.team_templates import STANDARD_ANIMAL_NAMES
from cogs.ticket_tracker import TicketTracker
from cogs.ui_emojis import UIEmojiManager


def guide_draft() -> GuideDraft:
    return GuideDraft(
        editor_id=1,
        name="Arcane Teams",
        aliases=["arcane"],
        categories=["boss", "beginner"],
        authors="Pencilvester",
        description="Use {wpd} with {fplifesteal} on {agfish}.",
        full_guide="Detailed guide with {wsword}, {swp}, and {rlegendary}.",
        viability=4,
        ease=4,
        slots={
            1: GuideSlot(1, "gfish", 50, "gem", "pd + ls @ legendary"),
            2: GuideSlot(2, "lizard", 50, "hidden", "scepter + dc @ fabled"),
            3: GuideSlot(3, "owl", 50, "legendary", "shield + hgen @ fabled"),
        },
    )


def emoji_bot() -> SimpleNamespace:
    manager = UIEmojiManager.__new__(UIEmojiManager)
    keys = (
        "weapon_sword",
        "weapon_pd",
        "passive_str",
        "passive_ls",
        "pet_fish",
        "pet_2026may_beeday",
        "stat_wp",
        "rank_legendary",
        "weapon_vstaff",
        "passive_mtap",
        "passive_snail",
    )
    manager.emojis = {
        key: discord.PartialEmoji(name=key, id=100 + index)
        for index, key in enumerate(keys)
    }
    return SimpleNamespace(ui_emoji_manager=manager)


class GuideVariableTests(unittest.TestCase):
    def test_neon_style_variables_resolve_and_unknown_values_stay_visible(self) -> None:
        rendered = render_guide_markdown(
            emoji_bot(),
            "{wsword} {wpdagger} {wvampstaff} {fpstr} {fpmana_mtap} "
            "{fpsnail_passive} {agfish} {abeeday} {swp_stat} "
            "{rlegendary} {unknown}",
        )
        self.assertIn("<:weapon_sword:100>", rendered)
        self.assertIn("<:weapon_pd:101>", rendered)
        self.assertIn("<:weapon_vstaff:108>", rendered)
        self.assertIn("<:passive_str:102>", rendered)
        self.assertIn("<:passive_mtap:109>", rendered)
        self.assertIn("<:passive_snail:110>", rendered)
        self.assertIn("<:pet_fish:104>", rendered)
        self.assertIn("<:pet_2026may_beeday:105>", rendered)
        self.assertIn("<:stat_wp:106>", rendered)
        self.assertIn("<:rank_legendary:107>", rendered)
        self.assertIn("{unknown}", rendered)
        self.assertEqual(unresolved_guide_variables(rendered), ("unknown",))

    def test_every_supplied_weapon_alias_resolves(self) -> None:
        aliases = {
            "sword": ("sword", "gsword", "greatsword"),
            "hstaff": ("hstaff", "healstaff"),
            "bow": ("bow",),
            "rune": ("rune",),
            "shield": ("shield", "aegis"),
            "orb": ("orb",),
            "vstaff": ("vstaff", "vampstaff"),
            "pd": ("dagger", "pdagger", "pdag", "pd"),
            "wand": ("wand", "awand"),
            "fstaff": ("fstaff",),
            "estaff": ("estaff",),
            "sstaff": ("sstaff",),
            "scepter": ("ascept", "arcane", "scepter"),
            "rstaff": ("rstaff",),
            "axe": ("axe", "gaxe"),
            "vban": ("vban", "banner"),
            "sythe": ("sythe", "csyth", "csy", "scythe"),
            "crune": ("crune", "cel"),
            "pstaff": ("pstaff",),
            "lsy": ("lsyth", "lsy", "lscythe"),
            "ffish": ("ffish",),
            "lrune": ("lrune",),
            "cstaff": ("cstaff",),
            "soul": ("stithe", "soul", "tithe"),
            "bhstaff": ("bhstaff",),
            "edge": ("aedge", "edge"),
            "xbow": ("woundb", "crossbow", "wbow", "wcbow", "xbow"),
            "bgaz": ("bgaz", "gaze", "bgaze"),
            "claw": ("cclaw", "claw"),
        }
        for expected_key, values in aliases.items():
            for value in values:
                with self.subTest(value=value):
                    entry = resolve_weapon(value)
                    self.assertIsNotNone(entry)
                    self.assertEqual(entry.key, expected_key)
                    self.assertEqual(
                        guide_variable_emoji_key(value),
                        entry.emoji_key,
                    )

    def test_every_supplied_passive_alias_resolves(self) -> None:
        aliases = {
            "str": ("att", "str", "strength"),
            "mag": ("mag", "magic"),
            "hp": ("hp",),
            "wp": ("wp",),
            "pr": ("pr",),
            "mr": ("mr",),
            "ls": ("lifesteal", "ls"),
            "th": ("thorns", "th"),
            "mtap": ("mtap", "manatap"),
            "absv": ("absolve", "absv"),
            "sg": ("safeguard", "sg"),
            "crit": ("critical", "crit"),
            "dc": ("discharge", "dc"),
            "kk": ("kkaze", "kamikaze", "kk"),
            "hgen": ("hgen", "regen", "regeneration"),
            "wgen": ("wgen", "energ", "energize"),
            "sprout": ("sprout", "sprt"),
            "enra": ("enrage", "enra"),
            "sac": ("sac", "sacrifice"),
            "snail": ("snail",),
            "kno": ("knowledge", "kno", "n"),
            "gslay": ("gslay", "slay", "slayer"),
            "adapt": ("adapt", "adaptation"),
            "res": ("resonance", "res", "reso"),
            "swarm": ("swarm", "hive", "lhive"),
            "lwolf": ("lwolf", "wolf"),
            "ds": ("dstrike", "strike", "ds"),
            "fr": ("frarm", "armor", "fr"),
        }
        ambiguous_animals = {"snail", "wolf"}
        for expected_key, values in aliases.items():
            for value in values:
                with self.subTest(value=value):
                    entry = resolve_passive(value)
                    self.assertIsNotNone(entry)
                    self.assertEqual(entry.key, expected_key)
                    if value not in ambiguous_animals:
                        self.assertEqual(
                            guide_variable_emoji_key(value),
                            entry.emoji_key,
                        )

    def test_direct_animals_stats_ranks_and_collisions_are_predictable(self) -> None:
        for animal in STANDARD_ANIMAL_NAMES:
            with self.subTest(animal=animal):
                self.assertEqual(
                    guide_variable_emoji_key(animal),
                    f"pet_{animal}",
                )
        self.assertEqual(guide_variable_emoji_key("gfish"), "pet_fish")
        self.assertEqual(
            guide_variable_emoji_key("beeday"),
            "pet_2026may_beeday",
        )
        self.assertEqual(guide_variable_emoji_key("ffish"), "weapon_ffish")
        self.assertEqual(guide_variable_emoji_key("lwolf"), "passive_lwolf")
        self.assertEqual(guide_variable_emoji_key("snail"), "pet_snail")
        self.assertEqual(
            guide_variable_emoji_key("snail_passive"),
            "passive_snail",
        )
        self.assertEqual(guide_variable_emoji_key("wp"), "passive_wp")
        self.assertEqual(guide_variable_emoji_key("wp_stat"), "stat_wp")
        self.assertEqual(guide_variable_emoji_key("wp_stats"), "stat_wp")
        self.assertEqual(guide_variable_emoji_key("swp_stat"), "stat_wp")
        self.assertEqual(guide_variable_emoji_key("swp_stats"), "stat_wp")
        self.assertEqual(
            guide_variable_emoji_key("legendary"),
            "rank_legendary",
        )

    def test_prefixed_variables_remain_backward_compatible(self) -> None:
        expected = {
            "wsword": "weapon_sword",
            "wpdagger": "weapon_pd",
            "fplifesteal": "passive_ls",
            "fpmana_mtap": "passive_mtap",
            "fpsnail_passive": "passive_snail",
            "afish": "pet_fish",
            "agfish": "pet_fish",
            "abeeday": "pet_2026may_beeday",
            "swp_stat": "stat_wp",
            "rlegendary": "rank_legendary",
        }
        self.assertEqual(
            {value: guide_variable_emoji_key(value) for value in expected},
            expected,
        )
    def test_store_migrates_old_database_and_persists_full_guide(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "team_guides.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    CREATE TABLE team_guides (
                        guide_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        aliases_json TEXT NOT NULL,
                        categories_json TEXT NOT NULL,
                        authors TEXT NOT NULL,
                        description TEXT NOT NULL,
                        viability INTEGER NOT NULL,
                        ease INTEGER NOT NULL,
                        creator_id INTEGER NOT NULL,
                        updated_by INTEGER NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()
            store = TeamGuideStore(path)
            store.initialize()
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(team_guides)")
                }
            finally:
                connection.close()
            self.assertIn("full_guide", columns)
            saved = store.save(guide_draft(), 1)
            self.assertEqual(saved.full_guide, guide_draft().full_guide)
            self.assertEqual(store.find("arcane").full_guide, guide_draft().full_guide)

    def test_full_guide_pages_stay_inside_embed_limits_after_emoji_expansion(self) -> None:
        draft = guide_draft()
        draft.full_guide = ("{wsword} detailed advice " * 250)[:4000]
        with tempfile.TemporaryDirectory() as directory:
            store = TeamGuideStore(Path(directory) / "team_guides.db")
            store.initialize()
            guide = store.save(draft, 1)
        pages = build_full_guide_embeds(emoji_bot(), guide)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(embed.description or "") <= 3800 for embed in pages))
        self.assertTrue(all((embed.footer.text or "").startswith("Guide v1") for embed in pages))


class GuideViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_editor_and_full_guide_views_fit_discord_component_limits(self) -> None:
        pages = (
            discord.Embed(title="x", description="a"),
            discord.Embed(title="x", description="b"),
        )
        pager = FullGuideView(pages, 1)
        self.assertEqual(len(pager.children), 2)
        self.assertTrue(pager.children[0].disabled)
        self.assertFalse(pager.children[1].disabled)

        editor = GuideEditorView(
            SimpleNamespace(bot=SimpleNamespace(ui_emoji_manager=None)),
            GuideDraft(editor_id=1),
        )
        self.assertEqual(len(editor.children), 10)
        self.assertLessEqual(max(item.row or 0 for item in editor.children), 3)

        help_embed = build_emoji_variable_help_embed(
            SimpleNamespace(ui_emoji_manager=None)
        )
        self.assertLessEqual(len(help_embed.description or ""), 4096)


class FakeThread(discord.Thread):
    def __init__(
        self,
        permissions: SimpleNamespace,
        *,
        archived: bool = False,
        locked: bool = False,
    ) -> None:
        self._test_permissions = permissions
        self._test_archived = archived
        self._test_locked = locked
        self.edit_calls = 0
        self.id = 123

    @property
    def mention(self) -> str:
        return "<#123>"

    @property
    def archived(self) -> bool:
        return self._test_archived

    @property
    def locked(self) -> bool:
        return self._test_locked

    def permissions_for(self, member: object, /) -> SimpleNamespace:
        return self._test_permissions

    async def edit(self, **kwargs: object) -> "FakeThread":
        self.edit_calls += 1
        if kwargs.get("archived") is False:
            self._test_archived = False
        return self


def thread_permissions(**overrides: bool) -> SimpleNamespace:
    values = {
        "view_channel": True,
        "send_messages_in_threads": True,
        "send_messages": True,
        "embed_links": True,
        "read_message_history": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PublicSurfaceTests(unittest.TestCase):
    def test_help_setup_and_about_surface_thread_boards_and_version(self) -> None:
        boss = BossGenerator.__new__(BossGenerator)
        boss.ui_emoji = lambda _name, fallback: fallback
        boss.cooldown_config = {}
        boss.decision_role_ids = lambda _guild_id: []
        boss.fighter_role_ids = lambda _guild_id: []

        help_embed = BossGenerator.build_help_embed(boss, "h", "w")
        help_text = "\n".join(field.value for field in help_embed.fields)
        self.assertIn("/boss-ticket-channel", help_text)
        self.assertIn("text channel or thread", help_text.casefold())
        self.assertIn("current channel or thread", help_text.casefold())

        setup_embed = BossGenerator.build_setup_guide_embed(
            boss,
            SimpleNamespace(id=123),
            "h",
        )
        setup_text = "\n".join(field.value for field in setup_embed.fields)
        self.assertIn("ticket-board text channel or thread", setup_text.casefold())

        info = BotInfo.__new__(BotInfo)
        info.description = "Test description"
        info.developer_name = "Hassaan"
        info.bot = SimpleNamespace(guilds=[])
        about_embed = BotInfo.build_about_embed(info)
        about_text = "\n".join(field.value for field in about_embed.fields)
        self.assertIn(BOT_VERSION, about_text)
        self.assertIn("channels or threads", about_text.casefold())

class TicketThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_slash_command_accepts_text_and_thread_destinations(self) -> None:
        parameter = TicketTracker.boss_ticket_channel.parameters[0]
        self.assertIn(discord.ChannelType.text, parameter.channel_types)
        self.assertIn(discord.ChannelType.public_thread, parameter.channel_types)
        self.assertIn(discord.ChannelType.private_thread, parameter.channel_types)
        self.assertIn(discord.ChannelType.news_thread, parameter.channel_types)

    async def test_thread_resolver_and_archived_thread_reopen(self) -> None:
        thread = FakeThread(thread_permissions(), archived=True)
        tracker = TicketTracker.__new__(TicketTracker)
        tracker.bot = SimpleNamespace(get_channel=lambda channel_id: thread)
        resolved = await tracker.get_ticket_board_destination(123)
        self.assertIs(resolved, thread)
        error = await tracker.ensure_ticket_board_destination(
            thread,
            SimpleNamespace(me=object()),
        )
        self.assertIsNone(error)
        self.assertEqual(thread.edit_calls, 1)
        self.assertFalse(thread.archived)

    async def test_locked_or_unwritable_threads_are_rejected(self) -> None:
        guild = SimpleNamespace(me=object())
        locked = FakeThread(thread_permissions(), locked=True)
        self.assertIn(
            "locked",
            TicketTracker.ticket_board_permission_error(locked, guild).casefold(),
        )
        unwritable = FakeThread(
            thread_permissions(send_messages_in_threads=False)
        )
        error = TicketTracker.ticket_board_permission_error(unwritable, guild)
        self.assertIn("Send Messages in Threads", error)


if __name__ == "__main__":
    unittest.main()
