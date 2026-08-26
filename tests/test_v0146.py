from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.boss_generator import (
    OWO_BOT_ID,
    BossGenerator,
    active_boss_message_ids,
    is_authoritative_boss_outcome_source,
)


ACTIVE_TEXT = """
# A Guild Boss Appeared!
### Top 10 Damage Dealt
-# No damage dealt yet...
### Rewards
-# runs away <t:1999999999:R> **0** fighters **3,021** defeated
"""

DEFEATED_TEXT = """
# Guild Boss Defeated!
### Top 10 Damage Dealt
### Rewards
-# defeated <t:1787727425:R> **14** fighters **3,022** defeated
"""


def raw_message(text: str) -> dict[str, object]:
    return {
        "author": {"id": str(OWO_BOT_ID)},
        "content": text,
        "embeds": [],
        "components": [],
    }


def bare_cog(config: dict[str, object]) -> BossGenerator:
    cog = BossGenerator.__new__(BossGenerator)
    cog.bot = object()
    cog.cooldown_config = {"1": config}
    cog.processed_outcome_messages = set()
    cog.guild_boss_fetch_locks = {}
    cog.guild_boss_outcome_locks = {}
    cog.guild_boss_watch_tasks = {}
    return cog


class BossOutcomeIdentityTests(unittest.IsolatedAsyncioTestCase):
    def test_active_id_list_supports_migration_and_multiple_cards(self) -> None:
        config = {
            "active_boss_message_id": "103",
            "active_boss_message_ids": [101, "102", 101, "invalid"],
        }
        self.assertEqual(active_boss_message_ids(config), [101, 102, 103])
        self.assertTrue(is_authoritative_boss_outcome_source(config, 102))
        self.assertFalse(is_authoritative_boss_outcome_source(config, 999))

    async def test_untracked_result_copy_cannot_finish_active_boss(self) -> None:
        cog = bare_cog(
            {
                "active_boss_message_id": 100,
                "active_boss_message_ids": [100, 101],
                "active_boss_expires_at": 1_999_999_999,
            }
        )
        cog.start_defeat_cooldown = AsyncMock()

        await cog.maybe_handle_outcome(1, 500, raw_message(DEFEATED_TEXT))

        cog.start_defeat_cooldown.assert_not_awaited()

    async def test_known_active_card_can_finish_its_boss(self) -> None:
        cog = bare_cog(
            {
                "active_boss_message_id": 101,
                "active_boss_message_ids": [100, 101],
                "active_boss_expires_at": 1_999_999_999,
            }
        )
        cog.start_defeat_cooldown = AsyncMock()

        await cog.maybe_handle_outcome(1, 100, raw_message(DEFEATED_TEXT))

        cog.start_defeat_cooldown.assert_awaited_once()
        self.assertEqual(
            cog.start_defeat_cooldown.await_args.kwargs["boss_key"],
            1_999_999_999,
        )

    async def test_known_card_edit_is_refetched_before_tracking(self) -> None:
        cog = bare_cog(
            {
                "channel_id": 9,
                "active_boss_channel_id": 20,
                "active_boss_message_id": 100,
                "active_boss_message_ids": [100],
                "active_boss_expires_at": 1_999_999_999,
            }
        )
        cog.track_latest_guild_boss_message = AsyncMock()
        payload = SimpleNamespace(
            guild_id=1,
            channel_id=20,
            message_id=100,
            data={"content": "The guild boss was defeated!"},
        )
        full_active_data = raw_message(ACTIVE_TEXT)

        with patch(
            "cogs.boss_generator.fetch_raw_message",
            AsyncMock(return_value=full_active_data),
        ) as fetch:
            await cog.on_raw_message_edit(payload)

        fetch.assert_awaited_once_with(cog.bot, 20, 100)
        cog.track_latest_guild_boss_message.assert_awaited_once_with(
            1,
            20,
            100,
            full_active_data,
        )


if __name__ == "__main__":
    unittest.main()
