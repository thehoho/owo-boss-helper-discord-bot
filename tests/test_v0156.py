from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.boss_generator import BossGenerator
from cogs.bot_info import BOT_VERSION
from cogs.team_guides import render_guide_markdown, unresolved_guide_variables
from cogs.ui_emojis import UIEmojiManager


def emoji_bot() -> SimpleNamespace:
    manager = UIEmojiManager.__new__(UIEmojiManager)
    manager.emojis = {
        "weapon_sword": discord.PartialEmoji(name="W_sword", id=101),
        "passive_crit": discord.PartialEmoji(name="PS_crit", id=102),
        "effect_taunt": discord.PartialEmoji(name="EF_taunt", id=103),
        "pet_dog": discord.PartialEmoji(name="AN_dog", id=104),
        "rank_mythical": discord.PartialEmoji(name="R_mythical", id=105),
        "stat_hp": discord.PartialEmoji(name="ST_hp", id=106),
    }
    return SimpleNamespace(ui_emoji_manager=manager)


class StickyBotEmojiVariableTests(unittest.IsolatedAsyncioTestCase):
    def test_existing_bot_emoji_variables_render_without_uploading_anything(self) -> None:
        bot = emoji_bot()
        note = "Use {sword} {crit} {taunt} {dog} {mythical} {hp_stat} ⚔️"
        rendered = render_guide_markdown(bot, note)
        for name in ("W_sword", "PS_crit", "EF_taunt", "AN_dog", "R_mythical", "ST_hp"):
            self.assertIn(f"<:{name}:", rendered)
        self.assertIn("⚔️", rendered)
        self.assertNotIn("{sword}", rendered)
        self.assertFalse(hasattr(bot, "create_application_emoji"))

    def test_unknown_variables_and_direct_custom_markup_stay_unchanged(self) -> None:
        bot = emoji_bot()
        external = "<:external:123456789012345678>"
        note = f"{{unknown_weapon}} {external}"
        self.assertEqual(render_guide_markdown(bot, note), note)
        self.assertEqual(unresolved_guide_variables(note), ("unknown_weapon",))

    async def test_h_sticky_saves_rendered_bot_emojis_and_warns_on_typos(self) -> None:
        bot = emoji_bot()
        referenced = SimpleNamespace(
            content="Use {sword} with {crit}; typo {not_a_real_icon}",
            embeds=[],
        )
        channel = SimpleNamespace(
            id=10,
            fetch_message=AsyncMock(return_value=referenced),
        )
        guild = SimpleNamespace(id=1)
        message = SimpleNamespace(
            guild=guild,
            channel=channel,
            author=SimpleNamespace(id=42),
            reference=SimpleNamespace(message_id=77),
        )
        cog = BossGenerator.__new__(BossGenerator)
        cog.bot = bot
        cog.cooldown_config = {"1": {"channel_id": 10}}
        cog.member_can_set_boss_decision = lambda _member, _guild_id: True
        cog.upsert_boss_decision_message = AsyncMock()

        with (
            patch("cogs.boss_generator.get_guild_helper_prefix", new=AsyncMock(return_value="h")),
            patch("cogs.boss_generator.save_cooldown_config"),
            patch("cogs.boss_generator.safe_reply", new=AsyncMock()) as reply,
        ):
            await cog.handle_boss_sticky_command(message, "set")

        saved = cog.cooldown_config["1"]["sticky_custom_text"]
        self.assertIn("<:W_sword:101>", saved)
        self.assertIn("<:PS_crit:102>", saved)
        self.assertIn("{not_a_real_icon}", saved)
        cog.upsert_boss_decision_message.assert_awaited_once_with(1, force_repost=True)
        confirmation = reply.await_args.args[1]
        self.assertIn("Custom sticky note saved", confirmation)
        self.assertIn("Unknown emoji variables stayed as text", confirmation)
        self.assertIn("`{not_a_real_icon}`", confirmation)


class StickyBotEmojiPublicSurfaceTests(unittest.TestCase):
    def test_version_and_help_explain_the_existing_emoji_catalog(self) -> None:
        self.assertEqual(BOT_VERSION, "0.15.6-beta")
        cog = BossGenerator.__new__(BossGenerator)
        cog.ui_emoji = lambda _name, fallback: fallback
        embed = BossGenerator.build_help_embed(cog, "h", "o")
        text = "\n".join(field.value for field in embed.fields)
        self.assertIn("`{sword} {crit} {taunt}`", text)
        self.assertIn("/guide-emojis", text)
        self.assertIn("no new emoji is uploaded", text)
        self.assertIn("`h sticky`", text)


if __name__ == "__main__":
    unittest.main()