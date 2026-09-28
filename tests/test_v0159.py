from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cogs.bot_info import BOT_VERSION
from cogs.ticket_tracker import (
    TicketStore,
    extract_top10_battle_logs,
    is_ticket_command,
)


class TicketCommandAliasTests(unittest.TestCase):
    def test_default_ticket_aliases_work_independently_of_saved_prefix(self) -> None:
        for command, saved_prefix in (
            ("w boss t", "o"),
            ("wboss ticket", "o"),
            ("owo boss ticket", "x"),
            ("owo boss tickets", "x"),
            ("o boss t", "o"),
        ):
            with self.subTest(command=command, saved_prefix=saved_prefix):
                self.assertTrue(is_ticket_command(command, saved_prefix))

    def test_unrelated_prefixes_and_commands_stay_ignored(self) -> None:
        self.assertFalse(is_ticket_command("x boss t", "o"))
        self.assertFalse(is_ticket_command("w boss inventory", "o"))

    def test_single_visible_top_ten_log_is_extracted(self) -> None:
        battle_uuid = "483ee741-5dbf-4aa8-acdc-a8dd9a4e5914"
        text = (
            "Top 10 Damage Dealt\n"
            f"1 43,626 <@975396105665781820> https://owobot.com/battle-log?uuid={battle_uuid}\n"
            "Rewards\n"
        )
        self.assertEqual(extract_top10_battle_logs(text), {975396105665781820: {battle_uuid}})


class FirstSnapshotHitTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_visible_uuid_updates_tracked_user_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TicketStore(Path(directory) / "tickets.db")
            await store.initialize()
            await store.upsert_status(1, 42, "Member", "member", 3)
            battle_uuid = "483ee741-5dbf-4aa8-acdc-a8dd9a4e5914"

            updates, initialized, visible = await store.record_boss_snapshot(
                1, 10, 100, {42: {battle_uuid}}
            )

            self.assertTrue(initialized)
            self.assertEqual(visible, 1)
            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0].previous_tickets, 3)
            self.assertEqual(updates[0].tickets, 2)
            self.assertEqual(updates[0].hits_applied, 1)
            status = await store.get_status(1, 42)
            self.assertIsNotNone(status)
            self.assertEqual(status.tickets, 2)

            repeated, initialized_again, visible_again = await store.record_boss_snapshot(
                1, 10, 100, {42: {battle_uuid}}
            )
            self.assertFalse(initialized_again)
            self.assertEqual(visible_again, 1)
            self.assertEqual(repeated, [])
            status = await store.get_status(1, 42)
            self.assertIsNotNone(status)
            self.assertEqual(status.tickets, 2)

    async def test_first_uuid_does_not_retroactively_charge_untracked_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TicketStore(Path(directory) / "tickets.db")
            await store.initialize()
            battle_uuid = "d623f308-a0dc-41b3-860c-f10ab5258641"

            updates, initialized, visible = await store.record_boss_snapshot(
                1, 10, 100, {99: {battle_uuid}}
            )
            self.assertTrue(initialized)
            self.assertEqual(visible, 1)
            self.assertEqual(updates, [])

            await store.upsert_status(1, 99, "Later", "later", 3)
            repeated, _, _ = await store.record_boss_snapshot(
                1, 10, 100, {99: {battle_uuid}}
            )
            self.assertEqual(repeated, [])
            status = await store.get_status(1, 99)
            self.assertIsNotNone(status)
            self.assertEqual(status.tickets, 3)

    def test_public_version(self) -> None:
        self.assertEqual(BOT_VERSION, "0.15.10-beta")


if __name__ == "__main__":
    unittest.main()