from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image

from cogs.emoji_assets import EmojiOverrideStore, emoji_label, normalize_upload, override_name
from cogs.emoji_dex import dex_source_url, sync_dex_artwork
from cogs.emoji_tools import EmojiTools, OwnerEmojiBrowser, reference_entries, resolve_target, search_entries
from cogs.team_guides import animal_emoji_key, guide_variable_emoji_key, render_guide_markdown
from cogs.ui_emojis import (DEX_ARTWORK, UIEmojiManager, clear_emoji_catalog_cache,
                           default_emoji_image, deployed_emoji_name, discover_emoji_assets, emoji_asset_keys)


def artwork(color="red"):
    output = io.BytesIO()
    Image.new("RGBA", (128, 128), color).save(output, format="PNG")
    return output.getvalue()


def request(user=42, category="all"):
    return SimpleNamespace(user=SimpleNamespace(id=user), namespace=SimpleNamespace(category=category),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))


class PickerTests(unittest.IsolatedAsyncioTestCase):
    def test_normalized_aliases_preserve_old_syntax(self):
        for new, old, key in (("PS_crit", "crit", "passive_crit"), ("W_sword", "sword", "weapon_sword"),
                              ("ST_hp", "hp_stat", "stat_hp"), ("AN_fish", "fish", "pet_fish")):
            self.assertEqual(guide_variable_emoji_key(new), key)
            self.assertEqual(guide_variable_emoji_key(old), key)
            self.assertEqual(resolve_target(new), key)
            self.assertEqual(emoji_label(key), new)

    async def test_all_passives_use_ps_and_accept_previous_bs_aliases(self):
        cog = EmojiTools(SimpleNamespace())
        for entry in search_entries(category="passive"):
            label = emoji_label(entry.key)
            self.assertTrue(label.startswith("PS_"))
            self.assertEqual(entry.variable, "{" + label + "}")
            self.assertEqual(resolve_target("BS_" + label[3:]), entry.key)
            self.assertEqual(guide_variable_emoji_key("BS_" + label[3:]), entry.key)
            choices = await cog.target_choices(request(category="passive"), label)
            self.assertIn(label, [choice.value for choice in choices])

    async def test_all_weapons_are_reachable_in_owner_pages_and_autocomplete(self):
        cog = EmojiTools(SimpleNamespace())
        cog.owner_id = 42
        browser = OwnerEmojiBrowser(cog, 42, {}, category="weapon")
        seen = {entry.key for entry in browser.visible_entries()}
        browser.page = 1
        browser.rebuild()
        seen.update(entry.key for entry in browser.visible_entries())
        self.assertEqual(len(seen), 29)
        self.assertEqual(seen, {entry.key for entry in search_entries(category="weapon")})
        for key in seen:
            choices = await cog.target_choices(request(category="weapon"), emoji_label(key))
            self.assertIn(emoji_label(key), [choice.value for choice in choices])
        self.assertEqual(len(search_entries(category="stat")), 6)
        self.assertTrue(all(choice.value.startswith("ST_") for choice in await cog.target_choices(request(category="stat"), "")))

    async def test_no_arguments_opens_full_picker_with_status(self):
        manager = SimpleNamespace(store=SimpleNamespace(all=AsyncMock()))
        real = UIEmojiManager(SimpleNamespace())
        real.store = SimpleNamespace(all=lambda: {"passive_crit": object()})
        cog = EmojiTools(SimpleNamespace(ui_emoji_manager=real))
        cog.owner_id = 42
        event = request()
        await EmojiTools.emoji_replace.callback(cog, event)
        view = event.followup.send.call_args.kwargs["view"]
        self.assertIsInstance(view, OwnerEmojiBrowser)
        self.assertIn("1/312", view.embed().fields[-1].value)
        outsider = request(user=99)
        self.assertFalse(await view.interaction_check(outsider))

    def test_dex_downloads_are_restricted_to_saved_discord_emoji_sources(self):
        record = SimpleNamespace(source="owo", image_url="https://cdn.discordapp.com/emojis/123456789012345678.webp",
                                 emoji_id=None, emoji_animated=False)
        self.assertIn("/emojis/123456789012345678.webp?", dex_source_url(record))
        record.emoji_animated = True
        self.assertIn(".gif?", dex_source_url(record))
        for url in ("http://cdn.discordapp.com/emojis/123456789012345678.png",
                    "https://cdn.discordapp.com.evil.test/emojis/123456789012345678.png",
                    "https://cdn.discordapp.com/attachments/private/file.png"):
            record.image_url = url
            self.assertIsNone(dex_source_url(record))
        record.emoji_id = 123456789012345678
        self.assertTrue(dex_source_url(record).startswith("https://cdn.discordapp.com/emojis/"))
        record.source = "owo_wiki_seed"
        self.assertIsNone(dex_source_url(record))


class DexAndMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bot = SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[]),
            create_application_emoji=AsyncMock(side_effect=lambda **kw: SimpleNamespace(id=555, name=kw["name"], animated=False)))
        self.manager = UIEmojiManager(self.bot)
        self.manager.store = EmojiOverrideStore(Path(self.directory.name) / "overrides.db")
        await self.manager.cog_load()
        reference_entries.cache_clear()

    async def asyncTearDown(self):
        DEX_ARTWORK.clear()
        clear_emoji_catalog_cache()
        reference_entries.cache_clear()

    async def test_saved_custom_animal_is_registered_and_survives_restart(self):
        await self.manager.install_dex_asset("pet_custom_test", artwork(), "Custom Test", '["custom_test"]', "source")
        self.assertIn("pet_custom_test", emoji_asset_keys())
        self.assertEqual(resolve_target("AN_custom_test"), "pet_custom_test")
        self.assertEqual(animal_emoji_key("Custom Test"), "pet_custom_test")
        self.assertEqual(animal_emoji_key("custom_test"), "pet_custom_test")
        self.assertEqual(guide_variable_emoji_key("custom_test"), "pet_custom_test")
        self.assertIn("555", render_guide_markdown(self.bot, "{AN_custom_test}"))
        saved = default_emoji_image("pet_custom_test")
        await self.manager.cog_load()
        self.assertEqual(default_emoji_image("pet_custom_test"), saved)
        self.assertTrue(deployed_emoji_name("pet_custom_test").startswith("AN_custom_test_"))
        self.assertIn("pet_custom_test", {entry.key for entry in reference_entries()})

    async def test_manual_animal_replacement_wins_over_dex_artwork(self):
        self.manager.store.save("pet_fish", "manual", artwork("blue"), 10, 42)
        self.manager.emojis["pet_fish"] = discord.PartialEmoji(name="manual", id=10)
        await self.manager.install_dex_asset("pet_fish", artwork(), "Fish", '["fish"]', "source")
        self.bot.create_application_emoji.assert_not_awaited()
        self.assertEqual(self.manager.emojis["pet_fish"].id, 10)
        self.assertEqual(self.manager.store.all()["pet_fish"].image, artwork("blue"))
        self.assertEqual(default_emoji_image("pet_fish"), normalize_upload(artwork()))

    async def test_failed_upload_or_persistence_does_not_register_animal(self):
        self.bot.create_application_emoji.side_effect = RuntimeError("upload failed")
        with self.assertRaises(RuntimeError):
            await self.manager.install_dex_asset("pet_new_test", artwork(), "New Test", "[]", "source")
        self.assertNotIn("pet_new_test", emoji_asset_keys())
        self.assertNotIn("pet_new_test", self.manager.store.dex_all())

    async def test_legacy_override_is_renamed_without_artwork_or_id_changes(self):
        raw = artwork()
        key = "passive_crit"
        self.manager.store.save(key, "ov_old_crit", raw, 123, 42)
        expected = override_name(key, raw)
        remote = SimpleNamespace(name="ov_old_crit", id=123, animated=False,
            edit=AsyncMock(return_value=SimpleNamespace(name=expected, id=123, animated=False)))
        self.bot.fetch_application_emojis.return_value = [remote]
        with patch("cogs.ui_emojis.discover_emoji_assets", return_value={key: Path("unused")}):
            await self.manager.ensure_synced()
        remote.edit.assert_awaited_once_with(name=expected)
        self.bot.create_application_emoji.assert_not_awaited()
        saved = self.manager.store.all()[key]
        self.assertEqual((saved.name, saved.emoji_id, saved.image), (expected, 123, raw))
        self.assertEqual(self.manager.emojis[key].id, 123)

    async def test_legacy_packaged_emoji_renames_in_place(self):
        remote = SimpleNamespace(name="v3_weapon_sword", id=88, animated=False,
            edit=AsyncMock(return_value=SimpleNamespace(name="W_sword_v3", id=88, animated=False)))
        self.bot.fetch_application_emojis.return_value = [remote]
        with patch("cogs.ui_emojis.discover_emoji_assets", return_value={"weapon_sword": Path("unused")}):
            await self.manager.ensure_synced()
        self.assertEqual(self.manager.emojis["weapon_sword"].id, 88)
        self.bot.create_application_emoji.assert_not_awaited()

    async def test_bs_passives_rename_in_place_preserving_manual_artwork(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                key = "passive_crit"
                raw = artwork()
                expected = override_name(key, raw) if manual else deployed_emoji_name(key)
                previous = "BS_" + expected[3:]
                if manual:
                    self.manager.store.save(key, previous, raw, 123, 42)
                remote = SimpleNamespace(name=previous, id=123, animated=False,
                    edit=AsyncMock(return_value=SimpleNamespace(name=expected, id=123, animated=False)))
                self.bot.fetch_application_emojis.return_value = [remote]
                self.manager._synced = False
                with patch("cogs.ui_emojis.discover_emoji_assets", return_value={key: Path("unused")}):
                    await self.manager.ensure_synced()
                remote.edit.assert_awaited_once_with(name=expected)
                self.bot.create_application_emoji.assert_not_awaited()
                self.assertEqual(self.manager.emojis[key].id, 123)
                if manual:
                    saved = self.manager.store.all()[key]
                    self.assertEqual((saved.name, saved.emoji_id, saved.image), (expected, 123, raw))

    async def test_sync_skips_cached_sources_and_reports_missing_and_failed_sources(self):
        cached = SimpleNamespace(animal_key="cached", display_name="Cached", aliases=(), source="owo",
            emoji_id=123456789012345678, image_url="", emoji_animated=False)
        fresh = SimpleNamespace(animal_key="fresh", display_name="Fresh", aliases=(), source="owo",
            emoji_id=123456789012345679, image_url="", emoji_animated=False)
        missing = SimpleNamespace(animal_key="missing", display_name="Missing", aliases=(), source="owo_wiki_seed",
            emoji_id=None, image_url="", emoji_animated=False)
        DEX_ARTWORK["pet_cached"] = (artwork(), "Cached", "[]", dex_source_url(cached))
        self.bot.animal_dex_store = SimpleNamespace(all_records=lambda: (cached, fresh, missing))
        self.manager.ensure_synced = AsyncMock()
        self.manager.install_dex_asset = AsyncMock()

        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            @property
            def content(self): return self
            async def iter_chunked(self, size):
                yield artwork()

        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def get(self, url, **kwargs):
                self.url = url
                return Response()

        with patch("cogs.emoji_dex.aiohttp.ClientSession", return_value=Session()):
            report = await sync_dex_artwork(self.bot, self.manager)
        self.assertEqual((report["imported"], report["reused"]), (1, 1))
        self.assertIn("pet_missing", report["missing"])
        self.manager.install_dex_asset.assert_awaited_once()
        self.assertEqual(self.manager.install_dex_asset.call_args.args[0], "pet_fresh")
        Response.status = 404
        self.manager.install_dex_asset.reset_mock()
        with patch("cogs.emoji_dex.aiohttp.ClientSession", return_value=Session()):
            report = await sync_dex_artwork(self.bot, self.manager)
        self.assertEqual(report["imported"], 0)
        self.assertEqual(len(report["failed"]), 1)
        self.manager.install_dex_asset.assert_not_awaited()
