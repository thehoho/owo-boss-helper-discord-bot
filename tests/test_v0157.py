from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cogs.bot_info import BOT_VERSION
from cogs.neon_weapons import (
    ActiveDexSession,
    NeonWeaponEntry,
    NeonWeapons,
    PendingWeaponCommand,
)
from cogs.ticket_tracker import TicketTracker


def weapon(weapon_id: str) -> NeonWeaponEntry:
    return NeonWeaponEntry(
        owner_user_id=1,
        weapon_id=weapon_id,
        current_quality=50.0,
        max_quality=100.0,
        needs_dex=True,
        saved=False,
        exact=False,
        weapon_type="sword",
        passive_types=("hp",),
        blueprint="",
        last_seen_at=0,
        dexed_at=0,
    )


def session(channel_id: int, runner_user_id: int, weapon_id: str) -> ActiveDexSession:
    return ActiveDexSession(
        runner_user_id=runner_user_id,
        owner_user_id=1,
        channel_id=channel_id,
        entries=[weapon(weapon_id)],
        owner_display_name="Owner",
        runner_display_name=f"Runner {runner_user_id}",
    )


class PacificResetNicknameTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_syncs_every_enabled_nickname_guild_even_when_db_was_pre_normalized(
        self,
    ) -> None:
        tracker = TicketTracker.__new__(TicketTracker)
        tracker.bot = SimpleNamespace(
            wait_until_ready=AsyncMock(),
            is_closed=Mock(side_effect=[False, True]),
        )
        tracker.store = SimpleNamespace(
            reset_all_for_current_cycle=AsyncMock(return_value=[]),
            list_configured_guilds=AsyncMock(return_value=[]),
            list_nickname_enabled_guilds=AsyncMock(return_value=[101, 202]),
        )
        tracker.cleanup_stale_once = AsyncMock(return_value=0)
        tracker.sync_guild_nicknames = AsyncMock(return_value=SimpleNamespace())
        tracker.invalidate_board_cache = Mock()
        tracker.queue_board_refresh = Mock()

        with (
            patch("cogs.ticket_tracker.next_pacific_reset_timestamp", return_value=0),
            patch("cogs.ticket_tracker.asyncio.sleep", new=AsyncMock()),
        ):
            await tracker.daily_reset_loop()

        self.assertEqual(
            tracker.sync_guild_nicknames.await_args_list,
            [unittest.mock.call(101), unittest.mock.call(202)],
        )


class SharedDexSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_advances_matching_channel_sessions_not_command_author(
        self,
    ) -> None:
        cog = NeonWeapons.__new__(NeonWeapons)
        cog.active_dex_sessions = {
            (55, 10): session(55, 10, "ABC123"),
            (55, 20): session(55, 20, "abc123"),
            (55, 30): session(55, 30, "OTHER"),
            (99, 40): session(99, 40, "ABC123"),
        }
        cog.advance_dex_session_after_confirmation = AsyncMock()

        matched = await cog.advance_matching_dex_sessions_after_confirmation(
            55, "AbC123"
        )

        self.assertEqual(matched, 2)
        self.assertCountEqual(
            [item.args for item in cog.advance_dex_session_after_confirmation.await_args_list],
            [(55, 10, "AbC123"), (55, 20, "AbC123")],
        )

    async def test_already_saved_blueprint_still_advances_the_waiting_session(self) -> None:
        cog = NeonWeapons.__new__(NeonWeapons)
        cog.store = SimpleNamespace(
            mark_dexed_weapon_any_owner=AsyncMock(return_value=0)
        )
        cog.find_pending_for_neon_reply = Mock(
            return_value=PendingWeaponCommand(
                user_id=999,
                channel_id=55,
                weapon_id="ABC123",
                created_at=time.monotonic(),
            )
        )
        cog.advance_matching_dex_sessions_after_confirmation = AsyncMock()

        with (
            patch("cogs.neon_weapons.parse_neon_weapon_page", return_value=None),
            patch("cogs.neon_weapons.parse_neon_max_quality_report", return_value=None),
            patch("cogs.neon_weapons.parse_blueprint", return_value="confirmed blueprint"),
            patch("cogs.neon_weapons.classify_blueprint", return_value=("sword", ("hp",))),
        ):
            await cog.process_neon_text(
                "Neon confirmation",
                guild_id=1,
                channel_id=55,
                message_id=777,
            )

        cog.advance_matching_dex_sessions_after_confirmation.assert_awaited_once_with(
            55, "ABC123"
        )


class ReleaseSurfaceTests(unittest.TestCase):
    def test_public_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.15.8-beta")


if __name__ == "__main__":
    unittest.main()