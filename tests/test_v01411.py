from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.bot_info import BOT_VERSION
from cogs.team_guides import GuideSlot, TeamGuide, animal_emoji_key, build_guide_embed
from cogs.team_templates import (
    EmptyTeamCreateConfirmView,
    ParsedTeamMessage,
    TeamMember,
    TeamTemplate,
    TeamTemplateStore,
    TeamTemplates,
    TemplateEditView,
)
from cogs.ui_emojis import UIEmojiManager


def saved_template(*, members: tuple[TeamMember, ...] = ()) -> TeamTemplate:
    return TeamTemplate(
        template_id=7,
        user_id=42,
        slot=3,
        name="MBT",
        source_title="Created as an empty team",
        members=members,
        created_at=1,
        updated_at=1,
    )


def guide_bot() -> SimpleNamespace:
    manager = UIEmojiManager.__new__(UIEmojiManager)
    keys = (
        "pet_fish",
        "rank_gem",
        "weapon_pd",
        "passive_mtap",
        "passive_crit",
        "rank_legendary",
        "pet_lizard",
        "rank_fabled",
        "weapon_crune",
        "passive_res",
        "pet_hedgebot",
        "weapon_hstaff",
        "passive_mag",
        "rank_mythical",
        "weapon_sword",
    )
    manager.emojis = {
        key: discord.PartialEmoji(name=key, id=500 + index)
        for index, key in enumerate(keys)
    }
    return SimpleNamespace(ui_emoji_manager=manager)


class EmptyTeamStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_create_is_safe_and_normal_reply_save_can_still_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TeamTemplateStore(Path(directory) / "team_templates.db")
            await store.initialize()
            created, error = await store.save(
                42,
                "MBT",
                "Created as an empty team",
                (),
                replace_existing=False,
            )
            self.assertIsNone(error)
            self.assertIsNotNone(created)
            assert created is not None
            self.assertEqual(created.members, ())

            duplicate, duplicate_error = await store.save(
                42,
                "MBT",
                "Created as an empty team",
                (),
                replace_existing=False,
            )
            self.assertIsNone(duplicate)
            self.assertIn("already have", duplicate_error or "")

            member = TeamMember(1, "gfish", "ABC123")
            updated, update_error = await store.save(
                42,
                "MBT",
                "OwO Team",
                (member,),
            )
            self.assertIsNone(update_error)
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated.template_id, created.template_id)
            self.assertEqual(updated.members, (member,))


class EmptyTeamConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_reply_asks_before_creating_any_team(self) -> None:
        cog = TeamTemplates.__new__(TeamTemplates)
        cog.store = SimpleNamespace(save=AsyncMock())
        message = SimpleNamespace(
            author=SimpleNamespace(id=42),
            guild=SimpleNamespace(id=9),
            reference=None,
            reply=AsyncMock(),
        )
        with patch(
            "cogs.team_templates.get_guild_helper_prefix",
            new=AsyncMock(return_value="h"),
        ):
            await cog.save_from_reply(message, "MBT")

        cog.store.save.assert_not_awaited()
        message.reply.assert_awaited_once()
        call = message.reply.await_args
        self.assertIn("did not reply to an official OwO team message", call.args[0])
        self.assertIn("empty team named **MBT**", call.args[0])
        self.assertIsInstance(call.kwargs["view"], EmptyTeamCreateConfirmView)
        self.assertEqual(
            [item.label for item in call.kwargs["view"].children],
            ["Yes, create empty team", "Cancel"],
        )

    async def test_confirmation_rejects_other_members(self) -> None:
        cog = TeamTemplates.__new__(TeamTemplates)
        cog.store = SimpleNamespace(save=AsyncMock())
        view = EmptyTeamCreateConfirmView(cog, 42, "MBT", "h")
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=99),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        self.assertFalse(await view.interaction_check(interaction))
        cog.store.save.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_yes_creates_empty_editable_team_and_cancel_creates_nothing(self) -> None:
        cog = TeamTemplates.__new__(TeamTemplates)
        template = saved_template()
        cog.store = SimpleNamespace(save=AsyncMock(return_value=(template, None)))
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )
        view = EmptyTeamCreateConfirmView(cog, 42, "MBT", "h")
        await view.children[0].callback(interaction)

        cog.store.save.assert_awaited_once_with(
            42,
            "MBT",
            "Created as an empty team",
            (),
            replace_existing=False,
        )
        edit = interaction.response.edit_message.await_args.kwargs
        self.assertIn("Empty team #3 created", edit["embed"].title or "")
        self.assertIsInstance(edit["view"], TemplateEditView)

        cog.store.save.reset_mock()
        interaction.response.edit_message.reset_mock()
        cancel_view = EmptyTeamCreateConfirmView(cog, 42, "MBT", "h")
        await cancel_view.children[1].callback(interaction)
        cog.store.save.assert_not_awaited()
        cancel = interaction.response.edit_message.await_args.kwargs
        self.assertIn("Cancelled", cancel["content"])
        self.assertIsNone(cancel["view"])

    async def test_official_reply_keeps_the_existing_import_flow(self) -> None:
        parsed = ParsedTeamMessage(
            source_title="OwO Team",
            members=(TeamMember(1, "fish", "ABC123"),),
            missing_positions=(),
            missing_weapon_positions=(),
        )
        cog = TeamTemplates.__new__(TeamTemplates)
        cog.store = SimpleNamespace(save=AsyncMock())
        cog.parse_team_reply = AsyncMock(return_value=parsed)
        cog.confirm_or_save_create = AsyncMock()
        message = SimpleNamespace(
            author=SimpleNamespace(id=42),
            guild=SimpleNamespace(id=9),
            reference=SimpleNamespace(message_id=123),
            reply=AsyncMock(),
        )
        with patch(
            "cogs.team_templates.get_guild_helper_prefix",
            new=AsyncMock(return_value="h"),
        ):
            await cog.save_from_reply(message, "MBT")

        cog.parse_team_reply.assert_awaited_once_with(message)
        cog.confirm_or_save_create.assert_awaited_once_with(message, "MBT", parsed)
        message.reply.assert_not_awaited()


class CompactGuideCompositionTests(unittest.TestCase):
    def test_ranked_pet_and_weapon_emojis_render_on_one_compact_line(self) -> None:
        guide = TeamGuide(
            guide_id=1,
            name="Compact Guide",
            aliases=("compact",),
            categories=("boss",),
            authors="Editable Author",
            description="Summary with {sword}.",
            full_guide="",
            viability=5,
            ease=4,
            slots=(
                GuideSlot(1, "gfish", 50, "gem", "pd + mtap + crit @ legendary"),
                GuideSlot(2, "lizard", None, "fabled", "crune + res @ fabled"),
                GuideSlot(
                    3,
                    "hedgebot",
                    50,
                    "legendary",
                    "hstaff + mag @ mythical",
                    "Optional note with {sword}.",
                ),
            ),
            creator_id=10,
            updated_by=11,
            version=2,
            created_at=1,
            updated_at=2,
        )
        self.assertEqual(animal_emoji_key("gfish"), "pet_fish")
        embed = build_guide_embed(guide_bot(), guide)
        fields = {field.name: field.value for field in embed.fields}
        composition = fields["Composition"]
        core_lines = [line for line in composition.splitlines() if not line.startswith("-#")]

        self.assertEqual(len(core_lines), 3)
        self.assertNotIn("\n\n", composition)
        self.assertTrue(all(" │ " in line for line in core_lines))
        self.assertIn("L.50 <:pet_fish:500>", core_lines[0])
        self.assertIn("<:weapon_pd:502>", core_lines[0])
        self.assertIn("<:passive_mtap:503>", core_lines[0])
        self.assertIn("Any level <:pet_lizard:506>", core_lines[1])
        self.assertIn("-# Optional note with <:weapon_sword:514>.", composition)
        self.assertIn("Editable Author", fields["Properties"])


class ReleaseSurfaceTests(unittest.TestCase):
    def test_phase_two_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.14.14-beta")


if __name__ == "__main__":
    unittest.main()
