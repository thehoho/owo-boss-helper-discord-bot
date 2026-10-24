from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from cogs.helper_prefix import parse_helper_command_argument
from cogs.topgg_votes import (
    TOPGG_VOTE_URL,
    VOTE_COMMAND_ALIASES,
    TopGGVotes,
    VoteStore,
)


class VoteStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_votes_are_deduplicated_counted_and_reminded_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = VoteStore(Path(directory) / "votes.db")
            await store.initialize()
            voted_at = int(time.time())
            expires_at = voted_at + 43_200
            inserted = await store.record_vote(
                user_id=42,
                voted_at=voted_at,
                expires_at=expires_at,
                weight=2,
            )
            duplicate = await store.record_vote(
                user_id=42,
                voted_at=voted_at,
                expires_at=expires_at,
                weight=2,
            )
            self.assertTrue(inserted)
            self.assertFalse(duplicate)

            summary = await store.set_reminders(42, True)
            self.assertEqual(summary.votes, 1)
            self.assertEqual(summary.points, 2)
            self.assertTrue(summary.reminders_enabled)
            self.assertEqual(await store.due_reminders(now=expires_at - 1), [])

            due = await store.due_reminders(now=expires_at)
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].user_id, 42)
            await store.mark_reminded(due[0].event_id, at=expires_at + 1)
            self.assertEqual(await store.due_reminders(now=expires_at + 5_000), [])

    async def test_missed_expiries_collapse_into_one_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = VoteStore(Path(directory) / "votes.db")
            await store.initialize()
            now = int(time.time())
            await store.set_reminders(42, True)
            for offset in (86_400, 43_200):
                await store.record_vote(
                    user_id=42,
                    voted_at=now - offset - 43_200,
                    expires_at=now - offset,
                    weight=1,
                )
            due = await store.due_reminders(now=now)
            self.assertEqual(len(due), 1)
            await store.mark_due_reminders(42, due[0].expires_at, at=now)
            self.assertEqual(await store.due_reminders(now=now), [])

    async def test_enabling_does_not_send_an_old_expired_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = VoteStore(Path(directory) / "votes.db")
            await store.initialize()
            await store.record_vote(
                user_id=77,
                voted_at=100,
                expires_at=200,
                weight=1,
            )
            await store.set_reminders(77, True)
            self.assertEqual(await store.due_reminders(), [])


class VoteCommandTests(unittest.IsolatedAsyncioTestCase):
    def test_prefix_aliases_follow_the_server_prefix(self) -> None:
        self.assertEqual(
            parse_helper_command_argument("x vote on", "x", VOTE_COMMAND_ALIASES),
            "on",
        )
        self.assertEqual(
            parse_helper_command_argument("xv off", "x", VOTE_COMMAND_ALIASES),
            "off",
        )
        self.assertIsNone(
            parse_helper_command_argument("h vote", "x", VOTE_COMMAND_ALIASES)
        )

    async def test_sync_records_api_votes_and_deduplicates_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
            cog = TopGGVotes(bot)
            cog.store = VoteStore(Path(directory) / "votes.db")
            await cog.store.initialize()
            cog.api_request = AsyncMock(
                return_value={
                    "data": [
                        {
                            "user_id": "topgg-user",
                            "platform_id": "123456789",
                            "created_at": "2026-10-24T08:00:00Z",
                            "expires_at": "2026-10-24T20:00:00Z",
                            "weight": 1,
                        }
                    ],
                    "cursor": None,
                }
            )
            try:
                self.assertEqual(await cog.sync_votes_once(), 1)
                self.assertEqual(await cog.sync_votes_once(), 0)
                summary = await cog.store.summary(123456789)
                self.assertEqual(summary.votes, 1)
                self.assertEqual(summary.points, 1)
            finally:
                await bot.close()

    async def test_embed_makes_voting_optional_and_opt_out_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
            cog = TopGGVotes(bot)
            cog.store = VoteStore(Path(directory) / "votes.db")
            await cog.store.initialize()
            try:
                summary = await cog.store.set_reminders(42, True)
                embed = cog.build_vote_embed(summary, "h")
                rendered = "\n".join(
                    [embed.description or ""] + [field.value for field in embed.fields]
                )
                self.assertEqual(embed.url, TOPGG_VOTE_URL)
                self.assertIn("completely optional", rendered)
                self.assertIn("Ignoring that DM causes no repeat messages", rendered)
                self.assertIn("Turn off", rendered)
                self.assertIn("h vote off", embed.footer.text or "")
            finally:
                await bot.close()

    async def test_cog_registers_vote_slash_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            import cogs.topgg_votes as module

            original = module.DATABASE_FILE
            module.DATABASE_FILE = Path(directory) / "votes.db"
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
            try:
                await bot.add_cog(module.TopGGVotes(bot))
                self.assertIsNotNone(bot.tree.get_command("vote"))
            finally:
                module.DATABASE_FILE = original
                await bot.close()


if __name__ == "__main__":
    unittest.main()
