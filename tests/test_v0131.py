from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

from cogs.team_guides import (
    FullGuideView,
    GuideDraft,
    GuideEditorView,
    GuideSlot,
    TeamGuideStore,
    build_emoji_variable_help_embed,
    build_full_guide_embeds,
    render_guide_markdown,
    unresolved_guide_variables,
)
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
