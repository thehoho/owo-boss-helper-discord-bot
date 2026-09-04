import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image

from cogs.emoji_assets import EmojiOverrideStore, override_name
from cogs.emoji_catalog import canonical_emoji_key, is_catalog_emoji
from cogs.emoji_dex import sync_dex_artwork
from cogs.emoji_tools import reference_entries, resolve_target
from cogs.ui_emojis import (DEX_ARTWORK, UIEmojiManager, clear_emoji_catalog_cache,
                            default_emoji_image, deployed_emoji_name)
from scripts.prune_duplicate_animal_emojis import plan_duplicates


def artwork(color="red"):
    output = io.BytesIO()
    Image.new("RGBA", (128, 128), color).save(output, format="PNG")
    return output.getvalue()


class DedupeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bot = SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[]),
                                   create_application_emoji=AsyncMock())
        self.manager = UIEmojiManager(self.bot)
        self.manager.store = EmojiOverrideStore(Path(self.directory.name) / "emoji.db")
        await self.manager.cog_load()
        self.manager.store.save_dex("pet_gfish", artwork(), "Fish", '["gfish"]', "source")
        await self.manager.cog_load()
        reference_entries.cache_clear()

    async def asyncTearDown(self):
        DEX_ARTWORK.clear(); clear_emoji_catalog_cache(); reference_entries.cache_clear()

    async def test_aliases_reuse_high_quality_id_without_recreating_v3(self):
        high = SimpleNamespace(name=deployed_emoji_name("pet_gfish"), id=123, animated=False)
        self.bot.fetch_application_emojis.return_value = [high]
        with patch("cogs.ui_emojis.discover_emoji_assets", return_value={"pet_fish": Path("unused"), "pet_gfish": Path("unused")}):
            await self.manager.ensure_synced()
        self.bot.create_application_emoji.assert_not_awaited()
        self.assertEqual(self.manager.emojis["pet_fish"].id, 123)
        self.assertEqual(self.manager.emojis["pet_gfish"].id, 123)
        self.assertEqual(default_emoji_image("pet_fish"), artwork())
        self.assertEqual(deployed_emoji_name("pet_fish"), high.name)

    def test_picker_deduplicates_but_aliases_still_resolve(self):
        keys = {e.key for e in reference_entries()}
        self.assertIn("pet_fish", keys)
        self.assertNotIn("pet_gfish", keys)
        self.assertEqual(resolve_target("AN_gfish"), "pet_fish")
        self.assertEqual(canonical_emoji_key("pet_hsquid"), "pet_hsquid")
        self.assertEqual(canonical_emoji_key("pet_gsquid"), "pet_squid")
        self.assertEqual(canonical_emoji_key("pet_pdragon"), "pet_pdragon")
        self.assertFalse(is_catalog_emoji("pet_hfish"))
        self.assertFalse(is_catalog_emoji("pet_dfish"))

    async def test_ranked_manual_override_is_preserved_then_group_reset_is_atomic(self):
        manual = artwork("blue")
        self.manager.store.save("pet_gfish", override_name("pet_gfish", manual), manual, 456, 42)
        high = SimpleNamespace(name=deployed_emoji_name("pet_fish"), id=123, animated=False)
        uploaded = SimpleNamespace(name=override_name("pet_gfish", manual), id=456, animated=False)
        self.bot.fetch_application_emojis.return_value = [high, uploaded]
        with patch("cogs.ui_emojis.discover_emoji_assets", return_value={"pet_fish": Path("unused"), "pet_gfish": Path("unused")}):
            await self.manager.ensure_synced()
        self.assertEqual(self.manager.emojis["pet_fish"].id, 456)
        revision = await self.manager.current_revision("pet_fish")
        await self.manager.replace_asset("pet_fish", None, 42, revision)
        self.assertEqual(self.manager.emojis["pet_fish"].id, 123)
        self.assertEqual(self.manager.emojis["pet_gfish"].id, 123)
        self.assertEqual(self.manager.store.all(), {})
        self.bot.create_application_emoji.assert_not_awaited()

    async def test_dex_import_cannot_override_manual_canonical_animal(self):
        self.manager.store.save("pet_fish", "manual", artwork("blue"), 456, 42)
        self.manager._set_emoji("pet_fish", discord.PartialEmoji(name="manual", id=456))
        await self.manager.install_dex_asset("pet_gfish", artwork(), "Fish", '["gfish"]', "source")
        self.bot.create_application_emoji.assert_not_awaited()
        self.assertEqual(self.manager.emojis["pet_fish"].id, 456)
        self.assertEqual(self.manager.emojis["pet_gfish"].id, 456)

    def test_cleanup_plan_protects_sole_icons_weapons_and_manual_ids(self):
        low = SimpleNamespace(name="AN_fish_v3", id=1, animated=False)
        high = SimpleNamespace(name=deployed_emoji_name("pet_gfish"), id=2, animated=False)
        sole = SimpleNamespace(name="AN_dragon_v3", id=3, animated=False)
        weapon = SimpleNamespace(name="W_sword_v3", id=4, animated=False)
        plan, retained = plan_duplicates([low, high, sole, weapon], {})
        self.assertEqual([item["id"] for item in plan], [1])
        self.assertIn(3, [item["id"] for item in retained])
        plan, _ = plan_duplicates([low, sole, weapon], {})
        self.assertEqual(plan, [])
        self.manager.store.save("pet_fish", "AN_fish_v3", artwork(), 1, 42)
        plan, _ = plan_duplicates([low, high, sole, weapon], self.manager.store.all())
        self.assertEqual(plan, [])

    async def test_missing_source_report_recognizes_saved_ranked_artwork(self):
        self.bot.animal_dex_store = SimpleNamespace(all_records=lambda: [])
        self.manager.ensure_synced = AsyncMock()
        report = await sync_dex_artwork(self.bot, self.manager)
        self.assertNotIn("pet_fish", report["missing"])
        self.assertNotIn("pet_gfish", report["missing"])
