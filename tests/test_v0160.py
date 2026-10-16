from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

from cogs.team_templates import TeamMember, extract_all_text, extract_message_text
from cogs.xp_tools import (
    KNOWLEDGE_SCENARIOS,
    ForecastActivityModal,
    ForecastSettings,
    KnowledgePresetModal,
    XPReminderStore,
    XPTools,
    BattleResult,
    PendingBattle,
    TeamXPToolsView,
    TeamLevelForecastView,
    average_winning_streak_xp,
    catchup_multiplier,
    changed_positions,
    current_streak_average_xp,
    estimated_battles_needed,
    knowledge_effect_text,
    knowledge_preset_from_ids,
    milestone_bonus,
    parse_battle_result,
    parse_team_xp_snapshot,
    next_level_forecasts,
    raw_battle_xp_needed,
    should_prompt_switch,
    simulate_team_forecast,
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

    def test_forecast_uses_current_streak_point_estimate(self) -> None:
        average = current_streak_average_xp(1265)
        self.assertEqual(round(average, 1), 358.3)
        self.assertEqual(
            estimated_battles_needed(49, 50, 1_772_662, 56, average),
            4128,
        )
        self.assertEqual(
            estimated_battles_needed(
                49,
                50,
                1_772_662,
                56,
                average,
                fixed_xp_per_battle=100,
            ),
            3741,
        )

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

    def test_team_snapshot_reads_current_streak_from_embed_footer(self) -> None:
        body = TEAM_TEXT.split("Current Streak:", 1)[0]
        raw = {
            "embeds": [
                {
                    "description": body,
                    "footer": {"text": "Current Streak: 1,734 | Highest Streak: 3,395 ⭐"},
                }
            ]
        }
        extracted = extract_all_text(raw)
        snapshot = parse_team_xp_snapshot(extracted)
        assert snapshot is not None
        self.assertEqual(snapshot.streak, 1734)

        embed = discord.Embed.from_dict(raw["embeds"][0])
        message = SimpleNamespace(content="", embeds=[embed], components=[])
        self.assertIn("Current Streak: 1,734", extract_message_text(message))

    def test_team_forecast_is_three_column_and_streak_derived(self) -> None:
        snapshot = parse_team_xp_snapshot(TEAM_TEXT)
        assert snapshot is not None
        cog = XPTools.__new__(XPTools)
        cog.bot = SimpleNamespace()
        embed = cog.build_team_forecast_embed(snapshot)
        self.assertIn("358.3 XP/battle", embed.description)
        self.assertEqual(len(embed.fields), 3)
        self.assertTrue(all(field.inline for field in embed.fields))
        self.assertIn("4,128 battles", embed.fields[0].value)

    def test_team_tool_buttons_use_requested_icons(self) -> None:
        snapshot = parse_team_xp_snapshot(TEAM_TEXT)
        assert snapshot is not None
        cog = XPTools.__new__(XPTools)
        cog.bot = SimpleNamespace()
        view = TeamXPToolsView(cog, 1, 1, snapshot, "h")
        calculator = next(item for item in view.children if item.label == "XP calculator")
        icon_buttons = {
            item.custom_id: item
            for item in view.children
            if item.label is None
        }
        self.assertEqual(str(calculator.emoji), "🧮")
        self.assertEqual(str(icon_buttons["xp_tools:save_normal"].emoji), "⚔️")
        self.assertEqual(str(icon_buttons["xp_tools:save_knowledge"].emoji), "🧠")
        self.assertEqual(str(icon_buttons["xp_tools:reminders"].emoji), "🔔")
        self.assertEqual(str(icon_buttons["xp_tools:save_team_help"].emoji), "💾")
        self.assertTrue(all(item.row == 0 for item in view.children))

        profile = SimpleNamespace(
            normal=(TeamMember(1, "snake", "ABC123"),),
            knowledge=(),
        )
        modal = KnowledgePresetModal(cog, 1, 1, profile)
        self.assertIn("Current ABC123", modal.position_1.placeholder)
        self.assertIn("snake", modal.position_1.placeholder)

    def test_neon_style_settings_match_reported_next_level_scale(self) -> None:
        text = (
            TEAM_TEXT.replace("1,772,662", "2,246,348")
            .replace("943,981", "1,417,667")
            .replace("3,904,670", "4,129,306")
            .replace("1,265", "1,754")
        )
        snapshot = parse_team_xp_snapshot(text)
        assert snapshot is not None
        reference_settings = ForecastSettings(
            battles_per_day=2000,
            fixed_xp_per_day=200_000,
            average_xp_per_hunt=21,
            tie_rate_percent=0.82,
            maximum_streak=2600,
        )
        normal, always = next_level_forecasts(snapshot, reference_settings)
        self.assertLessEqual(abs(normal[1] - 3246), 5)
        self.assertEqual(normal[2], 4043)
        self.assertLess(always[1], normal[1])

        lines, _ = simulate_team_forecast(
            snapshot,
            replace(reference_settings, max_lines=6),
            use_knowledge=True,
            knowledge_interval=10,
        )
        self.assertEqual(lines[0].battles, 0)
        self.assertEqual(lines[0].streak, 1754)
        self.assertGreaterEqual(len(lines), 5)

    def test_compact_forecast_defaults_and_controls(self) -> None:
        snapshot = parse_team_xp_snapshot(TEAM_TEXT)
        assert snapshot is not None
        settings = ForecastSettings()
        self.assertEqual(
            (
                settings.battles_per_day,
                settings.fixed_xp_per_day,
                settings.average_xp_per_hunt,
                settings.tie_rate_percent,
                settings.loss_rate_percent,
                settings.maximum_streak,
            ),
            (0, 0, 0.0, 0.0, 0.0, 0),
        )
        cog = XPTools.__new__(XPTools)
        cog.bot = SimpleNamespace()
        panel = TeamLevelForecastView(cog, 1, snapshot)
        embed = panel.build_embed()
        labels = {item.label for item in panel.children}
        self.assertIn("Show stats", labels)
        self.assertIn("Hide streak", labels)
        self.assertNotIn("Knowledge & lines", labels)
        self.assertEqual(settings.max_lines, 20)
        self.assertEqual(len(embed.fields), 1)
        self.assertNotIn("%", embed.fields[0].value)
        self.assertIn("⬛", embed.fields[0].value)
        self.assertIn("🆙", embed.fields[0].value)
        first_data_row = embed.fields[0].value.splitlines()[2]
        self.assertEqual(first_data_row.count(" | "), 5)
        self.assertIn("0 battles/day", embed.footer.text)
        self.assertIn("0% loss", embed.footer.text)

        modal = ForecastActivityModal(panel)
        self.assertTrue(all(not item.required for item in modal.children))
        self.assertEqual(modal.maximum_streak.default, "0")
        self.assertEqual(modal.knowledge_quality.default, "0.15")
        self.assertEqual(modal.max_lines.default, "20")
        self.assertIsNone(ForecastActivityModal._values("", 2, "rates"))
        self.assertEqual(ForecastActivityModal._optional_int("", 2600), 2600)
        self.assertEqual(ForecastActivityModal._optional_float("", 0.15), 0.15)

    def test_knowledge_effect_explains_additive_multiplier(self) -> None:
        self.assertEqual(knowledge_effect_text(2.7, 0.15), "2.70× → 2.85× (+5.6%)")
        self.assertEqual(knowledge_effect_text(1.0, 0.15), "1.00× → 1.15× (+15.0%)")

    def test_loss_rate_resets_streak_and_slows_forecast(self) -> None:
        snapshot = parse_team_xp_snapshot(TEAM_TEXT)
        assert snapshot is not None
        no_loss = ForecastSettings(max_lines=8)
        with_loss = replace(no_loss, loss_rate_percent=10)
        no_loss_lines, no_loss_next = simulate_team_forecast(
            snapshot, no_loss, use_knowledge=False, stop_after_next_levels=True
        )
        loss_lines, loss_next = simulate_team_forecast(
            snapshot, with_loss, use_knowledge=False, stop_after_next_levels=True
        )
        self.assertGreater(loss_next[1], no_loss_next[1])
        self.assertLess(loss_lines[-1].streak, no_loss_lines[-1].streak)

    def test_team_panel_combines_forecast_and_calculator_cycles_six_plans(self) -> None:
        snapshot = parse_team_xp_snapshot(TEAM_TEXT)
        assert snapshot is not None
        cog = XPTools.__new__(XPTools)
        cog.bot = SimpleNamespace()
        saved_settings = ForecastSettings(
            battles_per_day=500,
            fixed_xp_per_day=200_000,
            average_xp_per_hunt=21,
            tie_rate_percent=0.82,
            loss_rate_percent=0.1,
            maximum_streak=2600,
            knowledge_quality=0.12,
            max_lines=24,
        )
        team_embed = cog.build_team_tools_embed(snapshot, saved_settings)
        self.assertEqual(team_embed.title, "Team XP tools")
        self.assertEqual(len(team_embed.fields), 3)
        self.assertIn("Normal: up to", team_embed.fields[0].value)
        self.assertIn("Your model: up to", team_embed.fields[0].value)
        self.assertIn("Model + Max Knowledge", team_embed.fields[0].value)
        self.assertIn("500 battles/day", team_embed.description)
        self.assertIn("21 XP/hunt", team_embed.description)
        self.assertIn("0.82% ties", team_embed.description)
        self.assertIn("0.1% losses", team_embed.description)
        self.assertIn("max streak 2,600", team_embed.description)
        self.assertIn("Heather", team_embed.footer.text)
        self.assertIn("owo.bwep.net/exp/", team_embed.footer.text)

        panel = TeamLevelForecastView(cog, 1, snapshot, saved_settings)
        forecast = panel.build_embed()
        self.assertEqual(len(KNOWLEDGE_SCENARIOS), 6)
        self.assertIn("Level Forecaster", forecast.title)
        self.assertNotIn("Heather", forecast.description)
        self.assertEqual(panel.settings, saved_settings)
        self.assertLessEqual(len(forecast.fields[-1].value), 1024)
        self.assertTrue(any(item.label.startswith("Scenario:") for item in panel.children))

        calculator = cog.build_calculator_embed(
            start_level=1,
            target_level=2,
            highest_level=2,
            current_xp=0,
            battles_per_day=100,
            fixed_xp_per_day=0,
            knowledge_quality=0.15,
            current_streak=100,
        )
        self.assertNotIn("Heather", calculator.description)

    def test_knowledge_ids_replace_only_entered_positions(self) -> None:
        normal = (
            TeamMember(1, "snake", "AAA111"),
            TeamMember(2, "lobbot", "BBB222"),
            TeamMember(3, "owl", "CCC333"),
        )
        knowledge = knowledge_preset_from_ids(normal, {1: "kno111", 2: "", 3: "kno333"})
        self.assertEqual(
            tuple(member.weapon_id for member in knowledge),
            ("KNO111", "BBB222", "KNO333"),
        )
        with self.assertRaises(ValueError):
            knowledge_preset_from_ids(normal, {1: "bad"})

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
    async def test_normal_icon_saves_current_weapons_in_one_click(self) -> None:
        snapshot = parse_team_xp_snapshot(TEAM_TEXT)
        assert snapshot is not None
        saved = SimpleNamespace(normal=snapshot.members)
        cog = XPTools.__new__(XPTools)
        cog.bot = SimpleNamespace()
        cog.store = SimpleNamespace(upsert=AsyncMock(return_value=saved))
        view = TeamXPToolsView(cog, 42, 1, snapshot, "h")
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await view.save_normal.callback(interaction)

        cog.store.upsert.assert_awaited_once_with(
            1,
            42,
            normal=snapshot.members,
            enabled=False,
            awaiting_restore=False,
        )
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_team_reaction_reply_has_no_ping_or_auto_delete(self) -> None:
        cog = XPTools.__new__(XPTools)
        cog.bot = SimpleNamespace()
        settings = ForecastSettings(battles_per_day=500, average_xp_per_hunt=21)
        cog.store = SimpleNamespace(
            get_forecast_settings=AsyncMock(return_value=settings)
        )
        cog.pending_by_channel = {}
        channel = SimpleNamespace(send=AsyncMock())
        payload = SimpleNamespace(user_id=42, guild_id=1, channel_id=5, message_id=9)
        with patch("cogs.xp_tools.get_guild_helper_prefix", AsyncMock(return_value="h")):
            handled = await cog.send_team_tools_from_reaction(
                payload, {"content": TEAM_TEXT}, channel
            )
        self.assertTrue(handled)
        self.assertEqual(channel.send.await_args.args, ())
        kwargs = channel.send.await_args.kwargs
        self.assertNotIn("delete_after", kwargs)
        self.assertIn("embed", kwargs)
        self.assertIn("view", kwargs)
        self.assertEqual(kwargs["view"].settings, settings)
        self.assertIn("500 battles/day", kwargs["embed"].description)

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

    async def test_forecast_settings_persist_per_user_across_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "xp.db"
            store = XPReminderStore(path)
            await store.initialize()
            settings = ForecastSettings(
                battles_per_day=500,
                fixed_xp_per_day=200_000,
                average_xp_per_hunt=21,
                tie_rate_percent=0.82,
                loss_rate_percent=0.1,
                maximum_streak=2600,
                knowledge_quality=0.12,
                max_lines=24,
            )
            self.assertEqual(
                await store.upsert_forecast_settings(42, settings), settings
            )

            reopened = XPReminderStore(path)
            await reopened.initialize()
            self.assertEqual(await reopened.get_forecast_settings(42), settings)
            self.assertEqual(
                await reopened.get_forecast_settings(99), ForecastSettings()
            )

    async def test_forecast_modal_submit_saves_the_user_model(self) -> None:
        snapshot = parse_team_xp_snapshot(TEAM_TEXT)
        assert snapshot is not None
        saved = ForecastSettings(
            battles_per_day=500,
            fixed_xp_per_day=200_000,
            average_xp_per_hunt=21,
            tie_rate_percent=0.82,
            loss_rate_percent=0.1,
            maximum_streak=2600,
            knowledge_quality=0.12,
            max_lines=24,
        )
        cog = XPTools.__new__(XPTools)
        cog.bot = SimpleNamespace()
        cog.store = SimpleNamespace(
            upsert_forecast_settings=AsyncMock(return_value=saved)
        )
        source_message = SimpleNamespace(edit=AsyncMock())
        panel = TeamLevelForecastView(
            cog,
            42,
            snapshot,
            guild_id=1,
            helper_prefix="h",
            team_tools_message=source_message,
        )
        modal = ForecastActivityModal(panel)
        modal.activity._value = "500, 200000, 21"
        modal.rates._value = "0.82, 0.1"
        modal.maximum_streak._value = "2600"
        modal.knowledge_quality._value = "0.12"
        modal.max_lines._value = "24"
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        await modal.on_submit(interaction)

        cog.store.upsert_forecast_settings.assert_awaited_once_with(42, saved)
        self.assertEqual(panel.settings, saved)
        interaction.edit_original_response.assert_awaited_once()
        source_message.edit.assert_awaited_once()
        self.assertIn(
            "500 battles/day",
            source_message.edit.await_args.kwargs["embed"].description,
        )

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
                credits = next(field for field in help_embed.fields if field.name == "Credits")
                self.assertIn("Heather", credits.value)
                self.assertIn("https://owo.bwep.net/exp/", credits.value)
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
