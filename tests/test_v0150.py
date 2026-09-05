from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import discord
from PIL import Image
from discord.ext import commands

from cogs.boss_notifications import (
    _REWARD_DIGIT_ROWS,
    BossNotificationStore,
    BossNotifications,
    BossRewards,
    BossSnapshot,
    BossSubscription,
    extract_reward_media_url,
    parse_minimum,
    read_boss_rewards,
    reward_matches,
)
from cogs.bot_info import BOT_VERSION


def render_reward_image(
    values: tuple[int, int, int, int],
    doubled_mask: int = 0,
) -> bytes:
    image = Image.new("RGB", (620, 60), (40, 42, 47))
    for index, value in enumerate(values):
        x = 8 + index * 152 + 42
        top = 18 if index == 3 else 19
        for character in str(value):
            rows = _REWARD_DIGIT_ROWS[character]
            for y, row in enumerate(rows):
                for offset, pixel in enumerate(row):
                    if pixel == "#":
                        image.putpixel((x + offset, top + y), (245, 245, 245))
            x += len(rows[0]) + 2
        if doubled_mask & (1 << index):
            badge_left = 8 + index * 152 + 142
            for y in range(4, 14):
                for badge_x in range(badge_left, badge_left + 12):
                    image.putpixel((badge_x, y), (240, 230, 0))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class RewardReaderTests(unittest.TestCase):
    def test_four_values_and_each_x2_position_are_read(self) -> None:
        for doubled_mask in (0, 1, 2, 4, 8, 15):
            with self.subTest(doubled_mask=doubled_mask):
                rewards = read_boss_rewards(
                    render_reward_image((202, 4, 3, 24_437), doubled_mask)
                )
                self.assertIsNotNone(rewards)
                self.assertEqual(
                    (
                        rewards.shards,
                        rewards.weapon_crates,
                        rewards.boss_crates,
                        rewards.xp,
                        rewards.doubled_mask,
                    ),
                    (202, 4, 3, 24_437, doubled_mask),
                )

    def test_reader_fails_closed_on_wrong_layout_or_invalid_values(self) -> None:
        self.assertIsNone(read_boss_rewards(b"not an image"))
        image = Image.new("RGB", (600, 60), (40, 42, 47))
        output = io.BytesIO()
        image.save(output, format="PNG")
        self.assertIsNone(read_boss_rewards(output.getvalue()))
        self.assertIsNone(read_boss_rewards(render_reward_image((202, 4, 3, 1))))

    def test_only_trusted_reward_media_url_is_selected(self) -> None:
        valid = "https://cdn.discordapp.com/attachments/1/2/reward.png?ex=123"
        payload = {
            "components": [
                {"media": {"url": "https://evil.example/reward.png"}},
                {"items": [{"media": {"url": valid}}]},
            ]
        }
        self.assertEqual(extract_reward_media_url(payload), valid)

    def test_minimums_and_or_matching(self) -> None:
        self.assertEqual(parse_minimum("20K"), 20_000)
        self.assertEqual(parse_minimum("2.5m"), 2_500_000)
        self.assertIsNone(parse_minimum("zero"))
        rewards = BossRewards(202, 4, 3, 24_437, doubled_mask=4)
        self.assertTrue(reward_matches(BossSubscription(1, 2, "xp", 20_000, "recurring", 0), rewards))
        self.assertFalse(reward_matches(BossSubscription(1, 2, "boss_crates", 4, "recurring", 0), rewards))
        self.assertTrue(reward_matches(BossSubscription(1, 2, "x2", 1, "recurring", 0), rewards))


class NotificationStoreTests(unittest.TestCase):
    def test_consent_rules_once_scope_snapshots_and_delivery_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = BossNotificationStore(Path(directory) / "notifications.db")
            self.assertFalse(store.has_consent(2))
            store.set_consent(2)
            self.assertTrue(store.has_consent(2))
            recurring = BossSubscription(1, 2, "xp", 20_000, "recurring", 0)
            once = BossSubscription(1, 2, "end", 1, "once", 0)
            store.upsert_subscription(recurring)
            store.upsert_subscription(once)
            self.assertEqual(store.guild_ids(), {1})
            store.claim_pending_once(1, 999)
            rules = store.list_subscriptions(1, 2)
            self.assertEqual({rule.reward_type for rule in rules}, {"xp", "end"})
            self.assertEqual(next(rule for rule in rules if rule.reward_type == "end").boss_key, 999)

            snapshot = BossSnapshot(1, 999, 10, 20, BossRewards(202, 4, 3, 24_437))
            saved, consistent = store.save_snapshot(snapshot)
            self.assertTrue(consistent)
            self.assertEqual(saved.rewards.xp, 24_437)
            store.save_snapshot(BossSnapshot(1, 999, 10, 21, snapshot.rewards))
            self.assertEqual(store.get_snapshot(1, 999).observations, 2)
            _, consistent = store.save_snapshot(
                BossSnapshot(
                    1,
                    999,
                    10,
                    22,
                    BossRewards(202, 4, 3, 24_437, confidence=0.99),
                )
            )
            self.assertTrue(consistent)
            _, consistent = store.save_snapshot(
                BossSnapshot(1, 999, 10, 23, BossRewards(203, 4, 3, 24_437))
            )
            self.assertFalse(consistent)
            self.assertEqual(store.get_snapshot(1, 999).conflicts, 1)

            self.assertFalse(store.delivery_exists(1, 2, 999, "reward"))
            store.mark_delivery(1, 2, 999, "reward", "sent")
            store.mark_delivery(1, 2, 999, "reward", "sent")
            self.assertTrue(store.delivery_exists(1, 2, 999, "reward"))
            self.assertEqual(store.telemetry()["deliveries"], 1)
            store.clear_completed_once(1, 999)
            self.assertEqual([rule.reward_type for rule in store.list_subscriptions(1, 2)], ["xp"])
            store.remove_subscriptions(1, 2, None)
            self.assertFalse(store.has_consent(2))

    def test_stale_one_boss_rules_do_not_leak_into_a_new_boss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = BossNotificationStore(Path(directory) / "notifications.db")
            store.upsert_subscription(BossSubscription(1, 2, "end", 1, "once", 111))
            store.upsert_subscription(BossSubscription(1, 3, "xp", 20_000, "once", 0))
            store.claim_pending_once(1, 222)
            rules = store.list_subscriptions(1)
            self.assertEqual([(rule.user_id, rule.boss_key) for rule in rules], [(3, 222)])


class NotificationSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cog_registers_one_public_slash_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            import cogs.boss_notifications as module

            original = module.DATABASE_FILE
            module.DATABASE_FILE = Path(directory) / "notifications.db"
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
            try:
                cog = BossNotifications(bot)
                await bot.add_cog(cog)
                self.assertIsNotNone(bot.tree.get_command("boss-notify"))
            finally:
                module.DATABASE_FILE = original
                await bot.close()

    def test_release_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.15.0-beta")


if __name__ == "__main__":
    unittest.main()
