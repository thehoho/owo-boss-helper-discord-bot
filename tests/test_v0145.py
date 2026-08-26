from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from cogs.bot_info import BOT_VERSION
from cogs.boss_generator import (
    current_boss_report_cycle,
    detect_boss_outcome,
    is_active_boss_summary_text,
    is_explicit_active_boss_status,
    is_guild_boss_status,
    load_hp_templates,
    read_hp_from_image_bytes,
    repair_false_completed_boss_state,
    should_reactivate_completed_boss,
)


ACTIVE_EXPIRY = 1_787_724_772
ACTIVE_NOW = 1_787_720_000
ACTIVE_BOSS_TEXT = """
# <:owo_notlikethis:1171297001170817104> A Guild Boss Appeared!

### Top 10 Damage Dealt

-# No damage dealt yet...

### Rewards

-# <:hourglass_top_dim:1372810045988802591> runs away <t:1787724772:R>
   <:sword_dim:1371312649089974344> **0** fighters
   <:skull_dim:1463751359596593152> **3,020** defeated
"""


TRUE_DEFEATED_TEXT = """
# <:owo_yay:1171297064374784130> Guild Boss Defeated!

### Top 10 Damage Dealt

**`1   `** ` 98,604` <@746723893229912165>

### Rewards

-# <:hourglass_pause_dim:1372811523751149659> defeated <t:1787727425:R>
   <:sword_dim:1371312649089974344> **14** fighters
   <:skull_dim:1463751359596593152> **3,021** defeated
"""

def raw_message(text: str) -> dict[str, object]:
    return {"content": text, "embeds": [], "components": []}


class BossStatusRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.active_data = raw_message(ACTIVE_BOSS_TEXT)

    def test_active_reward_counter_is_not_a_defeat(self) -> None:
        self.assertIsNone(detect_boss_outcome(ACTIVE_BOSS_TEXT))
        self.assertTrue(is_active_boss_summary_text(ACTIVE_BOSS_TEXT))
        self.assertTrue(is_guild_boss_status(self.active_data))
        self.assertTrue(
            is_explicit_active_boss_status(self.active_data, ACTIVE_NOW)
        )

    def test_real_completed_wording_still_wins(self) -> None:
        self.assertEqual(detect_boss_outcome(TRUE_DEFEATED_TEXT), "defeated")
        self.assertFalse(is_active_boss_summary_text(TRUE_DEFEATED_TEXT))
        self.assertTrue(is_guild_boss_status(raw_message(TRUE_DEFEATED_TEXT)))
        self.assertEqual(
            detect_boss_outcome("The guild boss was defeated!"),
            "defeated",
        )
        self.assertEqual(
            detect_boss_outcome("The guild boss has been slain."),
            "defeated",
        )
        self.assertEqual(
            detect_boss_outcome("The guild boss ran away."),
            "escaped",
        )
        self.assertEqual(
            detect_boss_outcome(
                ACTIVE_BOSS_TEXT + "\nThe guild boss was defeated!"
            ),
            "defeated",
        )

    def test_completed_state_repair_requires_newer_active_card(self) -> None:
        config = {"last_source_message_id": 500}
        self.assertTrue(
            should_reactivate_completed_boss(
                config,
                self.active_data,
                message_id=501,
                now=ACTIVE_NOW,
            )
        )
        self.assertFalse(
            should_reactivate_completed_boss(
                config,
                self.active_data,
                message_id=500,
                now=ACTIVE_NOW,
            )
        )
        self.assertFalse(
            should_reactivate_completed_boss(
                config,
                self.active_data,
                message_id=501,
                now=ACTIVE_EXPIRY + 1,
            )
        )

    def test_state_repair_clears_dedup_and_false_daily_count(self) -> None:
        event_cycle = current_boss_report_cycle(
            datetime.fromtimestamp(ACTIVE_NOW, tz=timezone.utc)
        )
        config = {
            "last_result": "defeated",
            "last_boss_key": ACTIVE_EXPIRY,
            "last_source_message_id": 500,
            "last_outcome_event_time": ACTIVE_NOW,
            "last_detected_at": ACTIVE_NOW,
            "cooldown_end": ACTIVE_NOW + 300,
            "boss_report_cycle": event_cycle,
            "boss_report_defeated": 4,
        }

        repair_false_completed_boss_state(config)

        self.assertEqual(config["last_result"], "active")
        self.assertEqual(config["cooldown_end"], 0)
        self.assertEqual(config["boss_report_defeated"], 3)
        self.assertNotIn("last_boss_key", config)
        self.assertNotIn("last_source_message_id", config)
        self.assertNotIn("last_outcome_event_time", config)


class BossHpRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = load_hp_templates()
        cls.fixtures = Path(__file__).parent / "fixtures"

    def assert_hp(self, filename: str, expected: str) -> None:
        image_bytes = (self.fixtures / filename).read_bytes()
        hp, confidence = read_hp_from_image_bytes(image_bytes, self.templates)
        self.assertEqual(hp, expected, f"confidence={confidence:.4f}")
        self.assertGreaterEqual(confidence, 0.65)

    def test_cow_hp_with_comma_touching_next_digit(self) -> None:
        self.assert_hp("boss_hp_cow_166463.png", "166463")

    def test_owl_hp_with_digit_touching_comma(self) -> None:
        self.assert_hp("boss_hp_owl_207864.png", "207864")

    def test_pig_hp_with_comma_touching_narrow_one(self) -> None:
        self.assert_hp("boss_hp_pig_103177.png", "103177")

    def test_cow_hp_with_comma_touching_narrow_one(self) -> None:
        self.assert_hp("boss_hp_cow_125175.png", "125175")


class ReleaseSurfaceTests(unittest.TestCase):
    def test_hotfix_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.14.7-beta")


if __name__ == "__main__":
    unittest.main()
