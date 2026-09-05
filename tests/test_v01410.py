from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.bot_info import BOT_VERSION
from cogs.boss_generator import (
    BossGenerator,
    detect_boss_outcome,
    extract_all_text_from_raw,
    is_guild_boss_status,
)
from cogs.ticket_tracker import looks_like_guild_boss_card


MAIL_REWARD_DATA: dict[str, object] = {
    "author": {"id": "408785106942164992"},
    "timestamp": "2026-09-02T19:10:35.260000+00:00",
    "edited_timestamp": "2026-09-02T19:10:41.410000+00:00",
    "content": "",
    "embeds": [],
    "components": [
        {
            "type": 17,
            "components": [
                {
                    "type": 10,
                    "content": (
                        "## :dog2: :pig2: :cat2: You defeated a guild boss!\n"
                        "-# Received: <t:1788375972:R>\n"
                        "-# Expires: <t:1788981041:R>"
                    ),
                },
                {
                    "type": 10,
                    "content": (
                        "You and 107 other users defeated the guild boss in "
                        "**OwO Bot Support**!\n\n"
                        "You dealt `5,071` damage and were ranked 55th in the "
                        "damage leaderboard."
                    ),
                },
                {
                    "type": 10,
                    "content": (
                        "**__Rewards__**\n"
                        "- <:weaponshard:655902978712272917> 179\n"
                        "- <:crate:523771259302182922> 3\n"
                        "- <:bcrate:1447146215379828777> 3\n"
                        "- +47,336xp"
                    ),
                },
            ],
        }
    ],
}

ACTIVE_TEXT = """
# A Guild Boss Appeared!
### Top 10 Damage Dealt
**`1`** `43,626` <@975396105665781820>
### Rewards
-# runs away <t:1788379287:R> **6** fighters **2,322** defeated
"""

DEFEATED_TEXT = """
# Guild Boss Defeated!
### Top 10 Damage Dealt
**`1`** `98,604` <@746723893229912165>
### Rewards
-# defeated <t:1788377425:R> **14** fighters **2,323** defeated
"""


def raw_status(text: str) -> dict[str, object]:
    return {
        "author": {"id": "408785106942164992"},
        "content": text,
        "embeds": [],
        "components": [],
    }


class BossMailIsolationTests(unittest.IsolatedAsyncioTestCase):
    def test_mail_reward_can_never_be_a_boss_status(self) -> None:
        text = extract_all_text_from_raw(MAIL_REWARD_DATA)
        self.assertEqual(detect_boss_outcome(text), "defeated")
        self.assertNotIn("top 10 damage dealt", text.lower())
        self.assertFalse(is_guild_boss_status(MAIL_REWARD_DATA))
        self.assertFalse(looks_like_guild_boss_card(text))

    async def test_mail_reward_never_reaches_outcome_handler(self) -> None:
        cog = BossGenerator.__new__(BossGenerator)
        cog.cooldown_config = {
            "1": {
                "channel_id": 9,
                "active_boss_message_id": 100,
                "active_boss_message_ids": [100],
                "active_boss_first_seen_at": 1_788_375_688,
                "active_boss_expires_at": 1_788_379_287,
                "last_result": "active",
            }
        }
        cog.bot = SimpleNamespace(get_cog=lambda _name: None)
        cog.maybe_handle_outcome = AsyncMock()

        await cog.track_latest_guild_boss_message(
            1,
            1367410322851496046,
            1544786638054359132,
            MAIL_REWARD_DATA,
        )

        cog.maybe_handle_outcome.assert_not_awaited()

    def test_real_active_and_completed_cards_remain_recognized(self) -> None:
        for text in (ACTIVE_TEXT, DEFEATED_TEXT):
            with self.subTest(text=text.splitlines()[1]):
                self.assertTrue(is_guild_boss_status(raw_status(text)))
                self.assertTrue(looks_like_guild_boss_card(text))


class ReleaseSurfaceTests(unittest.TestCase):
    def test_incident_hotfix_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.15.0-beta")


if __name__ == "__main__":
    unittest.main()
