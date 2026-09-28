from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.boss_generator import (
    BOSS_WATCH_INTERVAL_SECONDS,
    OWO_BOT_ID,
    BossGenerator,
)
from cogs.bot_info import BOT_VERSION
from cogs.team_guides import FullGuideView, PublicGuideView, TeamGuide


ACTIVE_TEXT = """
# A Guild Boss Appeared!
### Top 10 Damage Dealt
-# No damage dealt yet...
### Rewards
-# runs away <t:1999999999:R> **0** fighters **3,021** defeated
"""


def raw_active(*, edited_timestamp: str = "") -> dict[str, object]:
    data: dict[str, object] = {
        "author": {"id": str(OWO_BOT_ID)},
        "content": ACTIVE_TEXT,
        "embeds": [],
        "components": [],
    }
    if edited_timestamp:
        data["edited_timestamp"] = edited_timestamp
    return data


def guide(full_guide: str) -> TeamGuide:
    return TeamGuide(
        guide_id=1,
        name="Reliable Guide",
        aliases=("reliable",),
        categories=("boss",),
        authors="Expert",
        description="Summary",
        full_guide=full_guide,
        viability=4,
        ease=4,
        slots=(),
        creator_id=1,
        updated_by=1,
        version=1,
        created_at=1,
        updated_at=1,
    )


def boss_cog() -> BossGenerator:
    cog = BossGenerator.__new__(BossGenerator)
    cog.bot = SimpleNamespace(get_cog=lambda _name: None)
    cog.cooldown_config = {
        "1": {
            "channel_id": 9,
            "active_boss_channel_id": 20,
            "active_boss_message_id": 100,
            "active_boss_message_ids": [100],
            "active_boss_expires_at": 1_999_999_999,
            "last_result": "active",
        }
    }
    cog.guild_boss_fetch_locks = {}
    cog.guild_boss_message_cache = {}
    cog.guild_boss_outcome_locks = {}
    cog.guild_boss_watch_tasks = {}
    cog.track_latest_guild_boss_message = AsyncMock()
    cog.refresh_generated_boss_command_from_logs = AsyncMock()
    return cog


class FullGuideReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_page_omits_view_keyword_instead_of_sending_none(self) -> None:
        cog = SimpleNamespace(bot=SimpleNamespace(ui_emoji_manager=None))
        view = PublicGuideView(cog, guide("Short instructions."))
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await view.open_full_guide(interaction)

        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertNotIn("view", kwargs)
        self.assertTrue(kwargs["ephemeral"])

    async def test_multi_page_keeps_pagination_view(self) -> None:
        cog = SimpleNamespace(bot=SimpleNamespace(ui_emoji_manager=None))
        view = PublicGuideView(cog, guide("Detailed advice. " * 500))
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await view.open_full_guide(interaction)

        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertIsInstance(kwargs["view"], FullGuideView)


class BossFetchReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_partial_edit_burst_fetches_once(self) -> None:
        cog = boss_cog()
        payload = SimpleNamespace(
            guild_id=1,
            channel_id=20,
            message_id=100,
            data={
                "content": "partial component update",
                "edited_timestamp": "2026-09-28T09:00:00+00:00",
            },
        )
        fetched = raw_active(
            edited_timestamp="2026-09-28T09:00:00+00:00"
        )

        with patch(
            "cogs.boss_generator.fetch_raw_message",
            AsyncMock(return_value=fetched),
        ) as fetch:
            await cog.on_raw_message_edit(payload)
            await cog.on_raw_message_edit(payload)

        fetch.assert_awaited_once_with(cog.bot, 20, 100)
        self.assertEqual(cog.track_latest_guild_boss_message.await_count, 2)

    async def test_new_edit_version_is_never_hidden_by_short_cache(self) -> None:
        cog = boss_cog()
        first = SimpleNamespace(
            guild_id=1,
            channel_id=20,
            message_id=100,
            data={"content": "partial", "edited_timestamp": "version-one"},
        )
        second = SimpleNamespace(
            guild_id=1,
            channel_id=20,
            message_id=100,
            data={"content": "partial", "edited_timestamp": "version-two"},
        )

        with patch(
            "cogs.boss_generator.fetch_raw_message",
            AsyncMock(return_value=raw_active()),
        ) as fetch:
            await cog.on_raw_message_edit(first)
            await cog.on_raw_message_edit(second)

        self.assertEqual(fetch.await_count, 2)

    async def test_complete_gateway_edit_needs_no_rest_fetch(self) -> None:
        cog = boss_cog()
        payload = SimpleNamespace(
            guild_id=1,
            channel_id=20,
            message_id=100,
            data=raw_active(edited_timestamp="complete-version"),
        )

        with patch(
            "cogs.boss_generator.fetch_raw_message",
            AsyncMock(),
        ) as fetch:
            await cog.on_raw_message_edit(payload)

        fetch.assert_not_awaited()
        cog.track_latest_guild_boss_message.assert_awaited_once_with(
            1,
            20,
            100,
            payload.data,
        )

    def test_fallback_polling_is_one_minute(self) -> None:
        self.assertEqual(BOSS_WATCH_INTERVAL_SECONDS, 60)

    def test_public_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.15.10-beta")


if __name__ == "__main__":
    unittest.main()
