from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

from cogs.team_templates import TeamMember
from cogs.xp_tools import (
    XPReminderStore,
    XPTools,
    BattleResult,
    PendingBattle,
    average_winning_streak_xp,
    catchup_multiplier,
    changed_positions,
    milestone_bonus,
    parse_battle_result,
    parse_team_xp_snapshot,
    raw_battle_xp_needed,
    should_prompt_switch,
    switch_benefit,
    total_level_xp,
    weapon_switch_commands,
    xp_for_next_level,
)


TEAM_TEXT = """
Hassaan's second team
owo team add {animal} {pos}
[1] <:hsnake:100> laviniamm
Lvl 49
[1,772,662/5,765,801]
EPJ52L <:weapon:101> 86.4%
[2] <:dlobbot:102> lobbot
Lvl 49
[943,981/5,765,801]
EFV0Z5 <:weapon:103> 81%
[3] <:gowl:104> 4m_owo
Lvl 56
[3,904,670/9,835,496]
DL60GW <:weapon:105> 87.5%
Current Streak: 1,265 | Highest Streak: 3,395
"""


class XPFormulaTests(unittest.TestCase):
    def test_level_formula_and_reference_total(self) -> None:
        self.assertEqual(xp_for_next_level(49), 5_765_801)
        self.assertEqual(total_level_xp(1, 50), 59_465_665)

    def test_dynamic_catchup_recalculates_by_level(self) -> None:
        self.assertAlmostEqual(catchup_multiplier(49, 56), 2.7)
        expected = xp_for_next_level(49) / 2.7 + xp_for_next_level(50) / 2.6
        self.assertAlmostEqual(raw_battle_xp_needed(49, 51, 0, 56), expected)

    def test_milestone_uses_highest_applicable_category(self) -> None:
        self.assertEqual(milestone_bonus(10), 532)
        self.assertEqual(milestone_bonus(50), 1712)
        self.assertEqual(milestone_bonus(100), 3000)
        self.assertEqual(milestone_bonus(500), 7236)
        self.assertEqual(milestone_bonus(1000), 20406)
        self.assertEqual(round(average_winning_streak_xp(1000), 2), 334.39)
        self.assertEqual(round(average_winning_streak_xp(2000), 2), 349.89)
        self.assertEqual(round(average_winning_streak_xp(5000), 2), 381.24)

    def test_switch_comparison_and_prompt_boundary(self) -> None:
        count_10, extra_10 = switch_benefit(10)
        count_100, extra_100 = switch_benefit(100)
        self.assertEqual(count_10, 100)
        self.assertEqual(count_100, 10)
        self.assertGreater(extra_10, extra_100)
        self.assertTrue(should_prompt_switch(99, 100))
        self.assertFalse(should_prompt_switch(100, 100))


class XPParsingTests(unittest.TestCase):
    def test_team_snapshot_reads_levels_progress_and_streak(self) -> None:
        snapshot = parse_team_xp_snapshot(TEAM_TEXT)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.streak, 1265)
        self.assertEqual([pet.level for pet in snapshot.pets], [49, 49, 56])
        self.assertEqual(snapshot.pets[0].current_xp, 1_772_662)
        self.assertEqual(snapshot.pets[2].next_xp, 9_835_496)

    def test_battle_results_distinguish_ties(self) -> None:
        win = parse_battle_result("You won in 14 turns! | +200 xp | Streak: 1,363")
        tie = parse_battle_result("The battle was a tie! | +100 xp | Streak: 1,363")
        loss = parse_battle_result("You lost in 8 turns! | +50 xp | Streak: 0")
        self.assertEqual((win.outcome, win.streak), ("win", 1363))
        self.assertEqual((tie.outcome, tie.streak), ("tie", 1363))
        self.assertEqual((loss.outcome, loss.streak), ("loss", 0))

    def test_switch_packet_changes_only_different_weapons_and_alternates_aliases(self) -> None:
        normal = (
            TeamMember(1, "snake", "AAA111"),
            TeamMember(2, "lobbot", "BBB222"),
            TeamMember(3, "owl", "CCC333"),
        )
        knowledge = (
            TeamMember(1, "snake", "KNO111"),
            TeamMember(2, "lobbot", "KNO222"),
            TeamMember(3, "owl", "KNO333"),
        )
        self.assertEqual(changed_positions(normal, knowledge), (1, 2, 3))
        self.assertEqual(
            weapon_switch_commands(normal, knowledge, "w"),
            ("ww KNO111 snake", "wuse KNO222 lobbot", "ww KNO333 owl"),
        )


class XPStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_profiles_persist_settings_presets_and_restore_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = XPReminderStore(Path(directory) / "xp.db")
            await store.initialize()
            normal = (TeamMember(1, "snake", "AAA111"),)
            knowledge = (TeamMember(1, "snake", "KNO111"),)
            saved = await store.upsert(
                1,
                42,
                enabled=True,
                interval=50,
                mention=False,
                normal=normal,
                knowledge=knowledge,
                awaiting_restore=True,
                trigger_streak=50,
            )
            self.assertTrue(saved.ready)
            reopened = XPReminderStore(Path(directory) / "xp.db")
            await reopened.initialize()
            profile = await reopened.get(1, 42)
            self.assertTrue(profile.enabled)
            self.assertEqual(profile.interval, 50)
            self.assertFalse(profile.mention)
            self.assertTrue(profile.awaiting_restore)
            self.assertEqual(profile.knowledge[0].weapon_id, "KNO111")

    async def test_prompt_tie_and_restore_workflow_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cog = XPTools.__new__(XPTools)
            cog.bot = SimpleNamespace()
            cog.store = XPReminderStore(Path(directory) / "xp.db")
            cog.pending_by_channel = {}
            await cog.store.initialize()
            normal = (TeamMember(1, "snake", "AAA111"),)
            knowledge = (TeamMember(1, "snake", "KNO111"),)
            await cog.store.upsert(
                1, 42, enabled=True, interval=10, normal=normal, knowledge=knowledge
            )
            channel = SimpleNamespace(id=5, send=AsyncMock())

            def battle_message(message_id: int) -> SimpleNamespace:
                return SimpleNamespace(
                    id=message_id,
                    guild=SimpleNamespace(id=1),
                    channel=channel,
                    reference=None,
                    mentions=[],
                    to_reference=lambda **_kwargs: SimpleNamespace(message_id=message_id),
                )

            def arm(command_id: int) -> None:
                cog.pending_by_channel[5] = [
                    PendingBattle(1, 5, 42, command_id, ("member",), time.monotonic())
                ]

            with patch("cogs.xp_tools.get_guild_owo_prefix", AsyncMock(return_value="w")):
                arm(100)
                await cog.process_battle_result(
                    battle_message(200), BattleResult("win", 9, 200), "You won | Streak: 9"
                )
                self.assertTrue((await cog.store.get(1, 42)).awaiting_restore)
                self.assertIn("ww KNO111 snake", channel.send.await_args.args[0])

                arm(101)
                await cog.process_battle_result(
                    battle_message(201), BattleResult("tie", 9, 100), "Tie | Streak: 9"
                )
                self.assertTrue((await cog.store.get(1, 42)).awaiting_restore)
                self.assertEqual(channel.send.await_count, 1)

                arm(102)
                await cog.process_battle_result(
                    battle_message(202), BattleResult("loss", 0, 50), "You lost | Streak: 0"
                )
                self.assertFalse((await cog.store.get(1, 42)).awaiting_restore)
                self.assertIn("ww AAA111 snake", channel.send.await_args.args[0])

    async def test_cog_registers_both_public_slash_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            import cogs.xp_tools as module

            original = module.DATABASE_FILE
            module.DATABASE_FILE = Path(directory) / "xp.db"
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
            try:
                await bot.add_cog(module.XPTools(bot))
                self.assertIsNotNone(bot.tree.get_command("xp-calc"))
                self.assertIsNotNone(bot.tree.get_command("xp-reminders"))
                help_embed = bot.get_cog("XPTools").build_xp_help_embed("h")
                self.assertIn("/xp-calc", "\n".join(field.value for field in help_embed.fields))
            finally:
                module.DATABASE_FILE = original
                await bot.close()


class ReliabilityPatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_reply_swallows_missing_send_permission_after_fallback(self) -> None:
        from cogs.message_utils import safe_reply

        response = SimpleNamespace(status=403, reason="Forbidden")
        error = discord.Forbidden(response, "Missing permissions")
        message = SimpleNamespace(
            reply=AsyncMock(side_effect=error),
            channel=SimpleNamespace(id=5, send=AsyncMock(side_effect=error)),
            guild=SimpleNamespace(id=1),
        )
        self.assertIsNone(await safe_reply(message, "hello"))
        message.channel.send.assert_awaited_once()

    async def test_neon_raw_edit_uses_partial_message_without_fetch(self) -> None:
        from cogs.neon_weapons import NeonWeapons

        partial = SimpleNamespace(add_reaction=AsyncMock())
        channel = SimpleNamespace(
            get_partial_message=lambda message_id: partial,
            fetch_message=AsyncMock(),
        )
        cog = NeonWeapons.__new__(NeonWeapons)
        cog.store = SimpleNamespace(initialize=AsyncMock())
        cog.bot = SimpleNamespace(get_channel=lambda _channel_id: channel)
        cog.process_neon_text = AsyncMock()

        await cog.handle_neon_raw(1, 5, 9, {"content": "Neon page"})

        channel.fetch_message.assert_not_awaited()
        self.assertIs(cog.process_neon_text.await_args.kwargs["reaction_message"], partial)

    async def test_boss_notify_omits_none_confirmation_view(self) -> None:
        from cogs.boss_notifications import BossNotifications

        cog = BossNotifications.__new__(BossNotifications)
        cog.request_subscription = AsyncMock(return_value=("Already enabled.", None))
        interaction = SimpleNamespace(
            guild_id=1,
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        reward = discord.app_commands.Choice(name="XP", value="xp")
        with patch(
            "cogs.boss_notifications.get_guild_helper_prefix",
            AsyncMock(return_value="h"),
        ):
            await BossNotifications.boss_notify.callback(
                cog, interaction, reward, 20_000, None, True
            )

        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertNotIn("view", kwargs)
        self.assertTrue(kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
