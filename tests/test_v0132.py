from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.bot_info import BOT_VERSION
from cogs.team_templates import (
    SMART_REPLACE_TEAM_DELAY_SECONDS,
    GuidedTeamSession,
    SmartReplacePlanningError,
    SmartReplaceScanSession,
    TeamMember,
    TeamTemplate,
    TeamTemplates,
    build_smart_replace_plan,
    classify_team_confirmation,
    exact_reset_commands,
    interleaved_member_commands,
    is_smart_team_display_command,
    parse_team_message_detailed,
    smart_replace_selection_text,
    smart_replace_transition_delay,
)


def saved_template(*members: TeamMember) -> TeamTemplate:
    return TeamTemplate(
        template_id=7,
        user_id=42,
        slot=1,
        name="Boss team",
        source_title="Hassaan's team",
        members=tuple(members),
        created_at=1,
        updated_at=1,
    )


def team_member(position: int, animal: str, weapon_id: str) -> TeamMember:
    return TeamMember(position=position, animal=animal, weapon_id=weapon_id)


class SmartReplacePlannerTests(unittest.TestCase):
    def test_weapon_only_plan_keeps_all_animals_and_alternates_aliases(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "owo", "CCC333"),
        )
        current = (
            team_member(1, "snake", "OLD111"),
            team_member(2, "eagle", "OLD222"),
            team_member(3, "owo", "CCC333"),
        )

        plan = build_smart_replace_plan(target, current)

        self.assertEqual(plan.team_change_count, 0)
        self.assertEqual(plan.weapon_change_count, 2)
        self.assertEqual(plan.already_correct_positions, (1, 2, 3))
        self.assertEqual(plan.commands, ("ww AAA111 snake", "wuse BBB222 eagle"))

    def test_two_animal_swap_opens_cycle_once_then_rotates(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "owo", "CCC333"),
        )
        current = (
            team_member(1, "eagle", "BBB222"),
            team_member(2, "snake", "AAA111"),
            team_member(3, "owo", "CCC333"),
        )

        plan = build_smart_replace_plan(target, current)

        self.assertEqual(
            plan.commands,
            ("wtm d 2", "wtm a snake 1", "wtm a eagle 2"),
        )
        self.assertEqual(plan.team_change_count, 3)
        self.assertEqual(plan.weapon_change_count, 0)
        self.assertEqual(plan.already_correct_positions, (3,))

    def test_swap_with_weapon_changes_uses_available_weapon_between_team_edits(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "owo", "CCC333"),
        )
        current = (
            team_member(1, "eagle", "OLD111"),
            team_member(2, "snake", "OLD222"),
            team_member(3, "owo", "OLD333"),
        )

        plan = build_smart_replace_plan(target, current)

        self.assertEqual(
            plan.commands,
            (
                "ww AAA111 snake",
                "wtm d 2",
                "wuse BBB222 eagle",
                "wtm a snake 1",
                "ww CCC333 owo",
                "wtm a eagle 2",
            ),
        )
        for previous, following in zip(plan.commands, plan.commands[1:]):
            self.assertEqual(smart_replace_transition_delay(previous, following), 0)
    def test_three_animal_cycle_needs_one_delete_and_three_adds(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "owo", "CCC333"),
        )
        current = (
            team_member(1, "eagle", "BBB222"),
            team_member(2, "owo", "CCC333"),
            team_member(3, "snake", "AAA111"),
        )

        plan = build_smart_replace_plan(target, current, "o")

        self.assertEqual(
            plan.commands,
            ("otm d 3", "otm a snake 1", "otm a eagle 2", "otm a owo 3"),
        )
        self.assertEqual(plan.weapon_change_count, 0)

    def test_missing_animals_directly_overwrite_unrelated_positions(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "owo", "CCC333"),
        )
        current = (
            team_member(1, "snake", "AAA111"),
            team_member(3, "panda", "PAN123"),
        )

        plan = build_smart_replace_plan(target, current)

        self.assertEqual(
            plan.commands,
            (
                "ww BBB222 eagle",
                "wtm a eagle 2",
                "wuse CCC333 owo",
                "wtm a owo 3",
            ),
        )
        self.assertEqual(plan.team_change_count, 2)
        self.assertEqual(plan.weapon_change_count, 2)

    def test_exact_match_generates_no_commands(self) -> None:
        members = (
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "owo", "CCC333"),
        )
        plan = build_smart_replace_plan(saved_template(*members), members)
        self.assertEqual(plan.commands, ())
        self.assertEqual(plan.already_correct_positions, (1, 2, 3))

    def test_moving_the_only_current_animal_fails_closed(self) -> None:
        target = saved_template(team_member(1, "snake", "AAA111"))
        current = (team_member(2, "snake", "AAA111"),)
        with self.assertRaisesRegex(SmartReplacePlanningError, "final animal"):
            build_smart_replace_plan(target, current)


class SmartReplaceCommandTests(unittest.TestCase):
    def test_release_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.14.14-beta")

    def test_team_display_commands_respect_configured_prefix(self) -> None:
        for command in (
            "otm",
            "oteam",
            "oteam display",
            "osetteam 1",
            "osetteam 2",
            "oteams 1",
            "oteams 2",
        ):
            with self.subTest(command=command):
                self.assertTrue(is_smart_team_display_command(command, "o"))
        self.assertTrue(is_smart_team_display_command("owoteams 1", "owo"))
        for command in (
            "wtm",
            "oteams",
            "oteams 3",
            "otm a snake 1",
            "osetteam 3",
            "osetteam 9",
            "obattle",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_smart_team_display_command(command, "o"))

    def test_selection_text_lists_both_numbered_team_aliases(self) -> None:
        session = SmartReplaceScanSession(
            user_id=42,
            guild_id=10,
            channel_id=20,
            template_id=7,
            template_slot=1,
            template_name="Boss team",
            identity_tokens=("hassaan",),
            owo_prefix="o",
        )
        text = smart_replace_selection_text(session)
        self.assertIn("`osetteam 1` or `oteams 1`", text)
        self.assertIn("`osetteam 2` or `oteams 2`", text)

    def test_shared_team_commands_wait_but_weapon_aliases_can_alternate(self) -> None:
        self.assertEqual(
            smart_replace_transition_delay("wtm", "wtm d 2"),
            SMART_REPLACE_TEAM_DELAY_SECONDS,
        )
        self.assertEqual(
            smart_replace_transition_delay("wteam", "wtm a snake 1"),
            SMART_REPLACE_TEAM_DELAY_SECONDS,
        )
        self.assertEqual(smart_replace_transition_delay("ww AAA111 snake", "wuse BBB222 eagle"), 0)
        self.assertEqual(
            smart_replace_transition_delay("ww AAA111 snake", "ww BBB222 eagle"),
            SMART_REPLACE_TEAM_DELAY_SECONDS,
        )

    def test_interleaved_add_and_equip_use_the_team_cooldown_window(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
        )
        current = (
            team_member(1, "panda", "PAN123"),
            team_member(2, "camel", "CAM123"),
        )

        plan = build_smart_replace_plan(target, current)

        self.assertEqual(
            plan.commands,
            (
                "ww AAA111 snake",
                "wtm a snake 1",
                "wuse BBB222 eagle",
                "wtm a eagle 2",
            ),
        )
        self.assertEqual(
            smart_replace_transition_delay("wtm", plan.commands[0]), 0
        )
        self.assertEqual(
            smart_replace_transition_delay(plan.commands[0], plan.commands[1]), 0
        )
        self.assertEqual(
            smart_replace_transition_delay(plan.commands[1], plan.commands[2]), 0
        )

    def test_exact_reset_alternates_equips_after_its_deletes(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "owo", "CCC333"),
        )

        self.assertEqual(
            exact_reset_commands(target),
            [
                "wtm d 1",
                "wtm d 2",
                "wtm d 3",
                "wtm a snake 1",
                "ww AAA111 snake",
                "wtm a eagle 2",
                "wuse BBB222 eagle",
                "wtm a owo 3",
                "ww CCC333 owo",
            ],
        )

    def test_all_commands_alternate_weapon_aliases(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "owo", "CCC333"),
        )

        self.assertEqual(
            interleaved_member_commands(target),
            [
                "wtm a snake 1",
                "ww AAA111 snake",
                "wtm a eagle 2",
                "wuse BBB222 eagle",
                "wtm a owo 3",
                "ww CCC333 owo",
            ],
        )

    def test_wuse_confirmation_is_accepted(self) -> None:
        self.assertEqual(
            classify_team_confirmation(
                "Eagle is now wielding the selected weapon.",
                "wuse BBB222 eagle",
            ),
            "success",
        )

    def test_refreshed_two_animal_page_confirms_delete(self) -> None:
        payload = """
Hassaan's team
owo team remove {animal}
[1] <:hsnake:100> Snake
AAA111 <:weapon:101> 99%
[3] <:customowo:104> 4millionowo
CCC333 <:weapon:105> 97%
Current Streak: 0
"""
        self.assertEqual(
            classify_team_confirmation(payload, "wtm d 2"),
            "success",
        )
        self.assertIsNone(classify_team_confirmation(payload, "wtm d 1"))

    def test_team_page_parser_keeps_positions_and_weapon_ids(self) -> None:
        payload = """
Hassaan's team
owo team add {animal} {pos}
[1] <:hsnake:100> Snake
Lvl 50
AAA111 <:weapon:101> 99%
[2] <:deagle:102> Eagle
Lvl 50
BBB222 <:weapon:103> 98%
[3] <:customowo:104> 4millionowo
Lvl 50
CCC333 <:weapon:105> 97%
Current Streak: 0
"""
        parsed = parse_team_message_detailed(payload)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(
            parsed.members,
            (
                team_member(1, "snake", "AAA111"),
                team_member(2, "eagle", "BBB222"),
                team_member(3, "customowo", "CCC333"),
            ),
        )
        self.assertEqual(parsed.missing_positions, ())
        self.assertEqual(parsed.missing_weapon_positions, ())


class _FakePartialMessage:
    def __init__(self) -> None:
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class _FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent: list[dict[str, object]] = []
        self.partials: dict[int, _FakePartialMessage] = {}
        self.fetched: dict[int, object] = {}
        self.fetch_calls: list[int] = []

    async def send(self, content: str | None = None, **kwargs: object) -> SimpleNamespace:
        self.sent.append({"content": content, **kwargs})
        return SimpleNamespace(id=1000 + len(self.sent))

    def get_partial_message(self, message_id: int) -> _FakePartialMessage:
        return self.partials.setdefault(message_id, _FakePartialMessage())

    async def fetch_message(self, message_id: int) -> object:
        self.fetch_calls.append(message_id)
        return self.fetched[message_id]


class _FakeInteractionResponse:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred = ephemeral


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        self.sent.append((content, ephemeral))


class _FakeInteraction:
    def __init__(self, channel: _FakeChannel) -> None:
        self.channel = channel
        self.response = _FakeInteractionResponse()
        self.followup = _FakeFollowup()
        self.message = None


class _FakeStore:
    def __init__(self, template: TeamTemplate) -> None:
        self.template = template

    async def get(self, user_id: int, template_id: int) -> TeamTemplate | None:
        if user_id == self.template.user_id and template_id == self.template.template_id:
            return self.template
        return None


class SmartReplaceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_refetches_latest_button_edited_team(self) -> None:
        target = saved_template(
            team_member(1, "snake", "AAA111"),
            team_member(2, "eagle", "BBB222"),
            team_member(3, "customowo", "CCC333"),
        )
        channel = _FakeChannel(20)
        cog = TeamTemplates(SimpleNamespace())
        cog.store = _FakeStore(target)
        key = cog.guided_key(10, channel.id, target.user_id)
        scan = SmartReplaceScanSession(
            user_id=target.user_id,
            guild_id=10,
            channel_id=channel.id,
            template_id=target.template_id,
            template_slot=target.slot,
            template_name=target.name,
            identity_tokens=("hassaan",),
            waiting_for_owo=True,
            ready_for_user=False,
            command_message_id=55,
            command_sent_at=time.monotonic(),
            display_command="wtm",
        )
        cog.smart_replace_scans[key] = scan
        first_payload = """
Hassaan's team
owo team add {animal} {pos}
[1] <:hsnake:100> Snake
OLD111 <:weapon:101> 99%
[2] <:deagle:102> Eagle
OLD222 <:weapon:103> 98%
[3] <:customowo:104> 4millionowo
CCC333 <:weapon:105> 97%
Current Streak: 0
"""
        first_message = SimpleNamespace(
            id=77,
            content=first_payload,
            embeds=[],
            components=[],
            guild=SimpleNamespace(id=10),
            channel=channel,
            reference=None,
            mentions=[],
        )

        handled = await cog.handle_smart_replace_owo_team(first_message)

        self.assertTrue(handled)
        self.assertIn(key, cog.smart_replace_scans)
        self.assertNotIn(key, cog.guided_sessions)
        self.assertEqual(scan.owo_response_message_id, 77)
        self.assertIn("Is this the OwO team", str(channel.sent[-1]["content"]))

        # Simulate the member using OwO's own message buttons before confirming.
        latest_payload = """
Hassaan's team
owo team add {animal} {pos}
[1] <:hsnake:100> Snake
OLD111 <:weapon:101> 99%
[2] <:deagle:102> Eagle
BBB222 <:weapon:103> 98%
[3] <:customowo:104> 4millionowo
CCC333 <:weapon:105> 97%
Current Streak: 0
"""
        channel.fetched[77] = SimpleNamespace(
            id=77,
            content=latest_payload,
            embeds=[],
            components=[],
        )
        interaction = _FakeInteraction(channel)
        await cog.confirm_smart_replace_from_interaction(
            interaction,
            key,
            scan,
            77,
        )

        self.assertEqual(channel.fetch_calls, [77])
        self.assertTrue(interaction.response.deferred)
        self.assertNotIn(key, cog.smart_replace_scans)
        self.assertEqual(cog.guided_sessions[key].commands, ("ww AAA111 snake",))
        self.assertIn("`ww AAA111 snake`", str(channel.sent[-1]["content"]))
        await cog.cog_unload()
        await asyncio.sleep(0)
    async def test_delete_refresh_advances_and_shows_cooldown_status(self) -> None:
        channel = _FakeChannel(20)
        cog = TeamTemplates(SimpleNamespace())
        key = cog.guided_key(10, channel.id, 42)
        session = GuidedTeamSession(
            user_id=42,
            guild_id=10,
            channel_id=channel.id,
            template_id=7,
            template_slot=1,
            template_name="Boss team",
            identity_tokens=("hassaan",),
            mode="Smart replace",
            commands=("wtm d 2", "wtm a eagle 2"),
            waiting_for_owo=True,
            command_message_id=55,
            command_sent_at=time.monotonic(),
            last_activity=time.monotonic(),
        )
        cog.guided_sessions[key] = session
        payload = """
Hassaan's team
owo team remove {animal}
[1] <:hsnake:100> Snake
AAA111 <:weapon:101> 99%
[3] <:customowo:104> 4millionowo
CCC333 <:weapon:105> 97%
Current Streak: 0
"""
        message = SimpleNamespace(
            id=88,
            content=payload,
            embeds=[],
            components=[],
            guild=SimpleNamespace(id=10),
            channel=channel,
            reference=SimpleNamespace(message_id=55),
            mentions=[],
        )

        with patch(
            "cogs.team_templates.SMART_REPLACE_TEAM_DELAY_SECONDS", 0.01
        ):
            handled = await cog.handle_guided_owo_confirmation(message)

        self.assertTrue(handled)
        self.assertEqual(session.next_index, 1)
        self.assertEqual(session.expected_command, "wtm a eagle 2")
        self.assertIn("OwO confirmed `wtm d 2`", str(channel.sent[-2]["content"]))
        self.assertIn("team cooldown", str(channel.sent[-2]["content"]))
        self.assertIn("`wtm a eagle 2`", str(channel.sent[-1]["content"]))
        self.assertTrue(channel.partials[55].deleted)
        self.assertTrue(channel.partials[1001].deleted)
        await cog.cog_unload()
        await asyncio.sleep(0)
