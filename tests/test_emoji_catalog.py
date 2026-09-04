import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.emoji_catalog import eligible_dex_record, is_catalog_emoji
from cogs.emoji_dex import sync_dex_artwork
from cogs.emoji_tools import (BROWSE_TARGET, EmojiTools, OwnerEmojiBrowser, ReferencePage,
                              reference_entries, search_entries)
from cogs.ui_emojis import UIEmojiManager, deployed_emoji_name


def interaction(user=42):
    return SimpleNamespace(user=SimpleNamespace(id=user), namespace=SimpleNamespace(category="all"),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_gameplay_ranks_remain_and_cp_and_unselected_specials_are_excluded(self):
        for name, rank in (("bee", "common"), ("chick", "uncommon"), ("dog", "rare"),
                           ("elephant", "epic"), ("dragon", "mythical"), ("gsquid", "legendary"),
                           ("gfish", "gem"), ("dboar", "fabled"), ("hedgebot", "bot"),
                           ("hsquid", "hidden"), ("glitchparrot", "distorted"), ("2024nov_juan", "special")):
            self.assertTrue(is_catalog_emoji("pet_" + name), name)
            self.assertTrue(eligible_dex_record(SimpleNamespace(animal_key=name, rank=rank)), name)
        for name, rank in (("pbird", "patreon"), ("custom_owner", "custom_patreon"),
                           ("2021halloween", "special"), ("gfish", "custom_patreon")):
            self.assertFalse(eligible_dex_record(SimpleNamespace(animal_key=name, rank=rank)))
        self.assertFalse(is_catalog_emoji("rank_custom_patreon"))

    async def test_autocomplete_offers_browse_and_opens_owner_pages(self):
        manager = UIEmojiManager(SimpleNamespace())
        manager.store = SimpleNamespace(all=lambda: {})
        cog = EmojiTools(SimpleNamespace(ui_emoji_manager=manager))
        cog.owner_id = 42
        event = interaction()
        choices = await cog.replacement_target_choices(event, "")
        self.assertEqual(choices[0].value, BROWSE_TARGET)
        self.assertLessEqual(len(choices), 25)
        await EmojiTools.emoji_replace.callback(cog, event, target=BROWSE_TARGET)
        view = event.followup.send.call_args.kwargs["view"]
        self.assertIsInstance(view, OwnerEmojiBrowser)
        self.assertEqual(view.query, "")
        seen = set()
        for page in range((len(reference_entries()) + 19) // 20):
            view.page = page
            view.rebuild()
            seen.update(entry.key for entry in view.visible_entries())
            self.assertIn(f"Page {page+1}/", view.jump_page.label)
        self.assertEqual(seen, {entry.key for entry in reference_entries()})

    async def test_page_jump_validates_bounds_and_owner(self):
        cog = EmojiTools(SimpleNamespace())
        cog.owner_id = 42
        view = OwnerEmojiBrowser(cog, 42, {})
        view.refresh = AsyncMock()
        modal = ReferencePage(view)
        modal.number._value = "2"
        await modal.on_submit(interaction())
        self.assertEqual(view.page, 1)
        view.refresh.assert_awaited_once()
        view.refresh.reset_mock()
        for value in ("0", "99999", "abc"):
            modal.number._value = value
            await modal.on_submit(interaction())
        modal.number._value = "3"
        await modal.on_submit(interaction(user=99))
        view.refresh.assert_not_awaited()
        self.assertEqual(view.page, 1)

    async def test_excluded_dex_records_are_never_downloaded_or_uploaded(self):
        records = [SimpleNamespace(animal_key="cp_example", rank="custom_patreon"),
                   SimpleNamespace(animal_key="2021halloween", rank="special")]
        bot = SimpleNamespace(animal_dex_store=SimpleNamespace(all_records=lambda: records),
                              fetch_application_emojis=AsyncMock(return_value=[]))
        manager = SimpleNamespace(ensure_synced=AsyncMock(), install_dex_asset=AsyncMock())
        with patch("cogs.emoji_dex.dex_source_url", side_effect=AssertionError("Should not inspect excluded sources")):
            report = await sync_dex_artwork(bot, manager)
        self.assertEqual(report["skipped_outside_catalog"], 2)
        manager.install_dex_asset.assert_not_awaited()

    async def test_retired_icons_reuse_existing_ids_but_are_not_reuploaded(self):
        key = "pet_cp_example"
        remote = SimpleNamespace(name=deployed_emoji_name(key), id=123, animated=False)
        bot = SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[remote]),
                              create_application_emoji=AsyncMock())
        manager = UIEmojiManager(bot)
        manager.store = SimpleNamespace(all=lambda: {})
        with patch("cogs.ui_emojis.discover_emoji_assets", return_value={key: Path("unused")}):
            await manager.ensure_synced()
            self.assertEqual(manager.emojis[key].id, 123)
            manager._synced = False
            bot.fetch_application_emojis.return_value = []
            await manager.ensure_synced()
        bot.create_application_emoji.assert_not_awaited()
