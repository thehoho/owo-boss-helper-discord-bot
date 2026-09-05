from __future__ import annotations

import unittest
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.battle_log_hp import (
    BattleLogHP,
    decompress_json,
    extract_battle_log_hp,
    extract_battle_log_uuids,
    extract_battle_log_uuids_from_payload,
    replace_command_hp,
)
from cogs.boss_generator import BossGenerator, read_bounded_http_body


class BattleLogDecoderTests(unittest.TestCase):
    def test_open_source_compress_json_shape_is_decoded(self) -> None:
        compressed = [
            [
                "answer",
                "flag",
                "items",
                "a|0|1|2",
                "n|g",
                "b|T",
                "x",
                "y",
                "a|6|7",
                "o|3|4|5|8",
            ],
            "9",
        ]
        self.assertEqual(
            decompress_json(compressed),
            {"answer": 42, "flag": True, "items": ["x", "y"]},
        )

    def test_enemy_order_and_final_zero_hp_are_preserved(self) -> None:
        payload = {
            "metadata": {
                "info": {
                    "date": 1_788_000_000_123,
                    "enemy": {"name": "Boss Team", "team": ["cow", "cat", "pig"]},
                },
                "cow": {"name": "Epic Cow"},
                "cat": {"name": "Rare Cat"},
                "pig": {"name": "Epic Pig"},
            },
            "battle": [
                {
                    "state": {
                        "cow": {"hp": 84_417},
                        "cat": {"hp": 59_117},
                        "pig": {"hp": 46_745},
                    }
                },
                {
                    "state": {
                        "cow": {"hp": 72_001},
                        "cat": {"hp": 0},
                        "pig": {"hp": 40_002},
                    }
                },
            ],
        }
        result = extract_battle_log_hp(
            payload,
            "483ee741-5dbf-4aa8-acdc-a8dd9a4e5914",
        )
        self.assertEqual(result.initial_hp, (84_417, 59_117, 46_745))
        self.assertEqual(result.final_hp, (72_001, 0, 40_002))
        self.assertEqual(result.enemy_names, ("Epic Cow", "Rare Cat", "Epic Pig"))

    def test_invalid_two_pet_log_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            extract_battle_log_hp(
                {
                    "metadata": {
                        "info": {
                            "date": 1_788_000_000_123,
                            "enemy": {"team": ["one", "two"]},
                        }
                    },
                    "battle": [{"state": {}}],
                },
                "483ee741-5dbf-4aa8-acdc-a8dd9a4e5914",
            )


class BattleLogCommandTests(unittest.TestCase):
    def test_uuid_extraction_is_ordered_and_deduplicated(self) -> None:
        first = "483ee741-5dbf-4aa8-acdc-a8dd9a4e5914"
        second = "d623f308-a0dc-41b3-860c-f10ab5258641"
        text = (
            f"https://owobot.com/battle-log?uuid={first} "
            f"https://owobot.com/battle-log?uuid={second} "
            f"https://owobot.com/battle-log?uuid={first.upper()}"
        )
        self.assertEqual(extract_battle_log_uuids(text), [first, second])

    def test_uuid_extraction_reads_nested_component_url_fields(self) -> None:
        first = "483ee741-5dbf-4aa8-acdc-a8dd9a4e5914"
        second = "d623f308-a0dc-41b3-860c-f10ab5258641"
        payload = {
            "content": "No log URL is rendered as text here.",
            "components": [
                {
                    "components": [
                        {"label": "scroll", "url": f"https://owobot.com/battle-log?uuid={first}"},
                        {"accessory": {"url": f"https://owobot.com/battle-log?uuid={second}"}},
                    ]
                }
            ],
        }
        self.assertEqual(
            extract_battle_log_uuids_from_payload(payload),
            [first, second],
        )

    def test_only_hp_values_are_replaced(self) -> None:
        command = (
            "neon b myself vs 62 cow worn sword, 41 cat worn bow, "
            "41 pig worn wand -hp 100000 100000 100000 -m -qe55"
        )
        self.assertEqual(
            replace_command_hp(command, (84_417, 59_117, 46_745)),
            (
                "neon b myself vs 62 cow worn sword, 41 cat worn bow, "
                "41 pig worn wand -hp 84417 59117 46745 -m -qe55"
            ),
        )

    def test_battle_timestamp_must_fit_bound_boss_lifetime(self) -> None:
        expiry = 1_788_010_800
        current = BattleLogHP(
            uuid="483ee741-5dbf-4aa8-acdc-a8dd9a4e5914",
            timestamp_ms=(expiry - 60) * 1_000,
            initial_hp=(100, 200, 300),
            final_hp=(90, 180, 250),
            enemy_names=("Cow", "Cat", "Pig"),
        )
        stale = BattleLogHP(
            uuid="d623f308-a0dc-41b3-860c-f10ab5258641",
            timestamp_ms=(expiry - 4 * 60 * 60 - 1) * 1_000,
            initial_hp=(100, 200, 300),
            final_hp=(90, 180, 250),
            enemy_names=("Cow", "Cat", "Pig"),
        )
        self.assertTrue(BossGenerator.battle_log_matches_boss_lifetime(current, expiry))
        self.assertFalse(BossGenerator.battle_log_matches_boss_lifetime(stale, expiry))


class BattleLogRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_http_body_is_fully_joined_and_bounded(self) -> None:
        class FakeContent:
            def __init__(self, chunks: list[bytes]):
                self.chunks = chunks

            async def iter_chunked(self, _size: int):
                for chunk in self.chunks:
                    yield chunk

        response = SimpleNamespace(content=FakeContent([b'{"battle":', b"[]}"]))
        self.assertEqual(
            await read_bounded_http_body(response, 100),
            b'{"battle":[]}',
        )
        with self.assertRaises(ValueError):
            await read_bounded_http_body(response, 5)

    async def test_status_reply_is_upgraded_to_freshest_exact_hp(self) -> None:
        now = int(time.time())
        expiry = now + 3_600
        uuid = "483ee741-5dbf-4aa8-acdc-a8dd9a4e5914"
        command = (
            "neon b myself vs 62 cow worn sword, 41 cat worn bow, "
            "41 pig worn wand -hp 100000 100000 100000 -m"
        )
        cog = BossGenerator.__new__(BossGenerator)
        cog.bot = SimpleNamespace()
        cog.cooldown_config = {
            "1": {
                "generated_boss_command": command,
                "generated_boss_created_at": now,
                "generated_boss_replies": {},
            }
        }
        cog.boss_hp_refresh_locks = {}
        cog.freshest_battle_log_hp = AsyncMock(
            return_value=BattleLogHP(
                uuid=uuid,
                timestamp_ms=now * 1_000,
                initial_hp=(84_417, 59_117, 46_745),
                final_hp=(72_001, 50_002, 40_003),
                enemy_names=("Epic Cow", "Rare Cat", "Epic Pig"),
            )
        )
        cog.upsert_generated_boss_reply = AsyncMock()
        data = {
            "author": {"id": "408785106942164992"},
            "content": (
                "# A Guild Boss Appeared!\n"
                "### Top 10 Damage Dealt\n"
                "### Rewards\n"
                f"runs away <t:{expiry}:R> 1 fighters 2,322 defeated"
            ),
            "embeds": [],
            "components": [
                {"components": [{"label": "scroll", "url": f"https://owobot.com/battle-log?uuid={uuid}"}]}
            ],
        }

        with patch("cogs.boss_generator.save_cooldown_config"):
            await cog.refresh_generated_boss_command_from_logs(1, 2, 3, data)

        config = cog.cooldown_config["1"]
        self.assertEqual(config["generated_boss_bound_expiry"], expiry)
        self.assertEqual(config["generated_boss_hp"], [72_001, 50_002, 40_003])
        self.assertIn("-hp 72001 50002 40003", config["generated_boss_command"])
        self.assertEqual(cog.upsert_generated_boss_reply.await_count, 2)
        first_command = cog.upsert_generated_boss_reply.await_args_list[0].args[-1]
        exact_command = cog.upsert_generated_boss_reply.await_args_list[1].args[-1]
        self.assertIn("-hp 100000 100000 100000", first_command)
        self.assertIn("-hp 72001 50002 40003", exact_command)

    async def test_older_status_card_is_left_untouched(self) -> None:
        now = int(time.time())
        cog = BossGenerator.__new__(BossGenerator)
        cog.bot = SimpleNamespace()
        cog.cooldown_config = {
            "1": {
                "generated_boss_command": "neon b myself vs x -hp 1 2 3 -m",
                "generated_boss_created_at": now,
                "generated_boss_latest_status_message_id": 20,
                "generated_boss_latest_status_channel_id": 2,
            }
        }
        cog.boss_hp_refresh_locks = {}
        cog.freshest_battle_log_hp = AsyncMock()
        cog.upsert_generated_boss_reply = AsyncMock()
        data = {
            "content": (
                "# A Guild Boss Appeared!\n### Top 10 Damage Dealt\n"
                f"### Rewards\nruns away <t:{now + 3600}:R> 1 fighters 2,322 defeated"
            ),
            "embeds": [],
            "components": [],
        }

        await cog.refresh_generated_boss_command_from_logs(1, 2, 19, data)

        cog.freshest_battle_log_hp.assert_not_awaited()
        cog.upsert_generated_boss_reply.assert_not_awaited()

    async def test_periodic_reconcile_fetches_only_newest_known_active_card(self) -> None:
        now = int(time.time())
        cog = BossGenerator.__new__(BossGenerator)
        cog.bot = SimpleNamespace()
        cog.cooldown_config = {
            "1": {
                "generated_boss_command": "neon b myself vs x -hp 1 2 3 -m",
                "generated_boss_created_at": now,
                "generated_boss_latest_status_message_id": 10,
                "generated_boss_latest_status_channel_id": 100,
                "active_boss_message_id": 20,
                "active_boss_channel_id": 200,
            }
        }
        cog.refresh_generated_boss_command_from_logs = AsyncMock()
        data = {
            "author": {"id": "408785106942164992"},
            "content": (
                "# A Guild Boss Appeared!\n### Top 10 Damage Dealt\n"
                f"### Rewards\nruns away <t:{now + 3600}:R> 1 fighters 2,322 defeated"
            ),
            "embeds": [],
            "components": [],
        }

        with patch("cogs.boss_generator.fetch_raw_message", AsyncMock(return_value=data)) as fetch:
            await cog.reconcile_generated_boss_commands_once()

        fetch.assert_awaited_once_with(cog.bot, 200, 20)
        cog.refresh_generated_boss_command_from_logs.assert_awaited_once_with(
            1, 200, 20, data
        )


if __name__ == "__main__":
    unittest.main()
