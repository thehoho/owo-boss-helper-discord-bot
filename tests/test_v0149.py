from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.animal_dex import AnimalDex
from cogs.bot_info import BOT_VERSION
from cogs.boss_generator import (
    DEFAULT_HP,
    OWO_BOT_ID,
    BossGenerator,
    is_authoritative_boss_outcome_source,
)
from cogs.team_templates import normalize_animal_emoji_alias


FIRST_SEEN = 1_787_724_000
DEFEATED_AT = 1_787_724_425
EXPIRES_AT = 1_787_725_800

ACTIVE_TEXT = f"""
# A Guild Boss Appeared!
### Top 10 Damage Dealt
-# No damage dealt yet...
### Rewards
-# runs away <t:{EXPIRES_AT}:R> **0** fighters **3,021** defeated
"""

DEFEATED_TEXT = f"""
# Guild Boss Defeated!
### Top 10 Damage Dealt
### Rewards
-# defeated <t:{DEFEATED_AT}:R> **14** fighters **3,022** defeated
"""

STALE_DEFEATED_TEXT = f"""
# Guild Boss Defeated!
### Rewards
-# defeated <t:{FIRST_SEEN - 300}:R> **9** fighters **3,021** defeated
"""


def raw_message(text: str) -> dict[str, object]:
    return {
        "author": {"id": str(OWO_BOT_ID)},
        "content": text,
        "embeds": [],
        "components": [],
    }


def active_config() -> dict[str, object]:
    return {
        "channel_id": 9,
        "active_boss_channel_id": 20,
        "active_boss_message_id": 100,
        "active_boss_message_ids": [100],
        "active_boss_first_seen_at": FIRST_SEEN,
        "active_boss_expires_at": EXPIRES_AT,
        "last_result": "active",
    }


def bare_boss_cog(config: dict[str, object]) -> BossGenerator:
    cog = BossGenerator.__new__(BossGenerator)
    cog.bot = SimpleNamespace(get_cog=lambda _name: None)
    cog.cooldown_config = {"1": config}
    cog.processed_outcome_messages = set()
    cog.guild_boss_fetch_locks = {}
    cog.guild_boss_outcome_locks = {}
    cog.guild_boss_watch_tasks = {}
    cog.cooldown_tasks = {}
    return cog


class BossReplacementOutcomeTests(unittest.IsolatedAsyncioTestCase):
    def test_newer_timestamped_result_matches_current_boss(self) -> None:
        self.assertTrue(
            is_authoritative_boss_outcome_source(
                active_config(),
                101,
                outcome_timestamp=DEFEATED_AT,
                now=DEFEATED_AT + 1,
            )
        )

    def test_old_result_copy_does_not_match_current_boss(self) -> None:
        self.assertFalse(
            is_authoritative_boss_outcome_source(
                active_config(),
                101,
                outcome_timestamp=FIRST_SEEN - 300,
                now=DEFEATED_AT,
            )
        )

    def test_replacement_must_be_newer_than_active_card(self) -> None:
        self.assertFalse(
            is_authoritative_boss_outcome_source(
                active_config(),
                99,
                outcome_timestamp=DEFEATED_AT,
                now=DEFEATED_AT,
            )
        )

    def test_late_result_copy_after_expiry_is_rejected(self) -> None:
        self.assertFalse(
            is_authoritative_boss_outcome_source(
                active_config(),
                101,
                outcome_timestamp=DEFEATED_AT,
                now=EXPIRES_AT + 91,
            )
        )

    async def test_newer_replacement_starts_defeat_cooldown(self) -> None:
        cog = bare_boss_cog(active_config())
        cog.start_defeat_cooldown = AsyncMock()

        with patch("cogs.boss_generator.time.time", return_value=DEFEATED_AT + 1):
            await cog.maybe_handle_outcome(1, 101, raw_message(DEFEATED_TEXT))

        cog.start_defeat_cooldown.assert_awaited_once()
        self.assertEqual(
            cog.start_defeat_cooldown.await_args.kwargs["boss_key"],
            EXPIRES_AT,
        )

    async def test_stale_replacement_does_not_start_cooldown(self) -> None:
        cog = bare_boss_cog(active_config())
        cog.start_defeat_cooldown = AsyncMock()

        with patch("cogs.boss_generator.time.time", return_value=DEFEATED_AT + 1):
            await cog.maybe_handle_outcome(1, 101, raw_message(STALE_DEFEATED_TEXT))

        cog.start_defeat_cooldown.assert_not_awaited()

    async def test_active_card_records_first_seen_time(self) -> None:
        config: dict[str, object] = {"channel_id": 9, "last_result": "ready"}
        cog = bare_boss_cog(config)
        cog.start_guild_boss_watcher = unittest.mock.Mock()
        cog.send_new_boss_message = AsyncMock()

        with (
            patch("cogs.boss_generator.time.time", return_value=FIRST_SEEN),
            patch("cogs.boss_generator.save_cooldown_config"),
        ):
            await cog.track_latest_guild_boss_message(
                1,
                20,
                100,
                raw_message(ACTIVE_TEXT),
            )

        self.assertEqual(config["active_boss_first_seen_at"], FIRST_SEEN)
        self.assertEqual(config["active_boss_expires_at"], EXPIRES_AT)

    def test_clearing_active_state_removes_instance_window(self) -> None:
        config = active_config()
        cog = bare_boss_cog(config)
        with patch("cogs.boss_generator.save_cooldown_config"):
            cog.clear_active_boss_tracking(1, "test")
        self.assertNotIn("active_boss_first_seen_at", config)


class CompactAnimalDexTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def message(content: str) -> SimpleNamespace:
        return SimpleNamespace(
            guild=SimpleNamespace(id=1),
            author=SimpleNamespace(bot=False),
            content=content,
            channel=SimpleNamespace(send=AsyncMock()),
            reply=AsyncMock(),
        )

    async def test_compact_had_uses_silent_lookup(self) -> None:
        message = self.message("had pridebee")
        cog = AnimalDex.__new__(AnimalDex)
        cog.send_lookup = AsyncMock(return_value=True)
        with patch(
            "cogs.animal_dex.get_guild_helper_prefix",
            new=AsyncMock(return_value="h"),
        ):
            await cog.on_message(message)
        cog.send_lookup.assert_awaited_once_with(
            message.channel,
            "pridebee",
            reference=message,
            silent_if_missing=True,
        )

    async def test_normal_sentence_starting_with_had_is_silent_when_unknown(self) -> None:
        message = self.message("had a really good day")
        cog = AnimalDex.__new__(AnimalDex)
        cog.store = SimpleNamespace(find=lambda _query: None)
        with patch(
            "cogs.animal_dex.get_guild_helper_prefix",
            new=AsyncMock(return_value="h"),
        ):
            await cog.on_message(message)
        message.channel.send.assert_not_awaited()
        message.reply.assert_not_awaited()

    async def test_bare_had_is_silent(self) -> None:
        message = self.message("had")
        cog = AnimalDex.__new__(AnimalDex)
        cog.send_lookup = AsyncMock()
        with patch(
            "cogs.animal_dex.get_guild_helper_prefix",
            new=AsyncMock(return_value="h"),
        ):
            await cog.on_message(message)
        cog.send_lookup.assert_not_awaited()
        message.reply.assert_not_awaited()


class CorrectnessSurfaceTests(unittest.TestCase):
    def test_default_hp_is_100k(self) -> None:
        self.assertEqual(DEFAULT_HP, "100000")

    def test_squid_identities_remain_distinct(self) -> None:
        self.assertEqual(normalize_animal_emoji_alias("squid"), "squid")
        self.assertEqual(normalize_animal_emoji_alias("hsquid"), "hsquid")
        self.assertEqual(normalize_animal_emoji_alias("hfish"), "fish")

    def test_release_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.14.13-beta")


if __name__ == "__main__":
    unittest.main()
