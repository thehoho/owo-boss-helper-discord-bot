from __future__ import annotations

import asyncio
import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands
from PIL import Image

from cogs.emoji_assets import EmojiOverrideStore, MAX_UPLOAD_BYTES, custom_emoji_url, normalize_upload, override_name
from cogs.emoji_tools import EmojiBrowser, EmojiConfirm, EmojiTools, reference_entries, resolve_target, search_entries
from cogs.team_guides import guide_variable_emoji_key, render_guide_markdown
from cogs.ui_emojis import UIEmojiManager, deployed_emoji_name, legacy_emoji_name, discover_emoji_assets, emoji_asset_keys, prepare_emoji_image


def png(color="red", size=(32, 16)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def remote(name, emoji_id=12345):
    return SimpleNamespace(name=name, id=emoji_id, animated=False)


def interaction(user_id=42):
    return SimpleNamespace(user=SimpleNamespace(id=user_id),
                           response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
                           followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())


class UploadTests(unittest.TestCase):
    def test_padding_is_removed_and_full_canvas_used(self):
        source = Image.new("RGBA", (128, 128))
        source.paste(Image.new("RGBA", (16, 8), "red"), (40, 60))
        raw = io.BytesIO()
        source.save(raw, format="PNG")
        result = normalize_upload(raw.getvalue())
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.size, (128, 128))
            self.assertEqual(image.getchannel("A").getbbox(), (0, 32, 128, 96))
        self.assertEqual(normalize_upload(result), result)

    def test_high_resolution_images_are_downsampled(self):
        with Image.open(io.BytesIO(normalize_upload(png(size=(1000, 800))))) as image:
            self.assertEqual(image.size, (128, 128))

    def test_invalid_sources_rejected(self):
        for raw in (b"", b"not an image", b"x" * (MAX_UPLOAD_BYTES + 1), png(color=(0, 0, 0, 0)),
                    png(size=(2049, 2049)), png()[:30]):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                normalize_upload(raw)

    def test_animation_is_preserved_or_rejected_never_flattened(self):
        for size in ((32, 32), (129, 129)):
            raw = io.BytesIO()
            Image.new("RGBA", size, "red").save(raw, format="GIF", save_all=True,
                append_images=[Image.new("RGBA", size, "blue")], duration=100, loop=0)
            if size[0] <= 128:
                self.assertEqual(normalize_upload(raw.getvalue()), raw.getvalue())
            else:
                with self.assertRaisesRegex(ValueError, "Animation"):
                    normalize_upload(raw.getvalue())

    def test_only_discord_markup_can_be_downloaded(self):
        self.assertEqual(custom_emoji_url("<:sword:123456789012345678>"),
                         "https://cdn.discordapp.com/emojis/123456789012345678.png?size=128&quality=lossless")
        self.assertIn(".gif?", custom_emoji_url("<a:sword:123456789012345678>"))
        for value in ("https://localhost/a.png", "file:///etc/passwd", "�", "<:x:1>", "<:sword:123456789012345678> trailing"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                custom_emoji_url(value)

    def test_content_names_are_stable_and_safe(self):
        key = max(emoji_asset_keys(), key=len)
        name = override_name(key, png())
        self.assertLessEqual(len(name), 32)
        self.assertRegex(name, r"^[A-Za-z0-9_]+$")
        self.assertEqual(name, override_name(key, png()))
        self.assertNotEqual(name, override_name(key, png("blue")))
        self.assertNotEqual(name, override_name("clown", png()))

    def test_static_catalog_now_fills_128_pixel_canvas(self):
        for path in (Path("assets/game_emojis/weapons/axe.webp"), Path("assets/game_emojis/stats/hp.png")):
            with Image.open(io.BytesIO(prepare_emoji_image(path))) as image:
                left, top, right, bottom = image.convert("RGBA").getchannel("A").getbbox()
                self.assertEqual(max(right - left, bottom - top), 128)
        self.assertEqual(deployed_emoji_name("weapon_sword"), "W_sword_v3")
        self.assertEqual(deployed_emoji_name("clown"), "UI_clown_v3")


class StoreTests(unittest.TestCase):
    def test_override_and_artwork_survive_reopening_and_reset_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.db"
            store = EmojiOverrideStore(path)
            store.initialize()
            store.save("clown", "test", png(), 123, 42)
            reopened = EmojiOverrideStore(path)
            record = reopened.all()["clown"]
            self.assertEqual((record.name, record.image, record.actor_id), ("test", png(), 42))
            reopened.reset("clown", 42)
            self.assertEqual(reopened.all(), {})
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT action FROM emoji_override_audit ORDER BY id").fetchall(), [("replace",), ("reset",)])


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bot = SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[]),
                                   create_application_emoji=AsyncMock(), is_owner=AsyncMock(return_value=False))
        self.manager = UIEmojiManager(self.bot)
        self.manager.store = EmojiOverrideStore(Path(self.directory.name) / "overrides.db")
        await self.manager.cog_load()
        self.key = "weapon_sword"
        self.base = deployed_emoji_name(self.key)
        self.old = discord.PartialEmoji(name=self.base, id=1)
        self.manager.emojis[self.key] = self.old
        async def create(**kwargs):
            return remote(kwargs["name"], 2)
        self.bot.create_application_emoji.side_effect = create

    async def replace(self, raw=None):
        return await self.manager.replace_asset(self.key, raw or png(), 42, self.base)

    async def test_replacement_is_persisted_and_guide_syntax_follows_it(self):
        emoji = await self.replace()
        self.assertEqual(self.manager.store.all()[self.key].emoji_id, 2)
        self.assertEqual(render_guide_markdown(self.bot, "{sword} {weapon_sword}"), f"{emoji} {emoji}")
        self.bot.create_application_emoji.assert_awaited_once()

    async def test_upload_failure_preserves_previous_mapping_and_database(self):
        self.bot.create_application_emoji.side_effect = RuntimeError("Discord unavailable")
        with self.assertRaises(RuntimeError):
            await self.replace()
        self.assertEqual(self.manager.emojis[self.key], self.old)
        self.assertEqual(self.manager.store.all(), {})

    async def test_database_failure_does_not_switch_live_mapping(self):
        with patch.object(self.manager.store, "save", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.replace()
        self.assertEqual(self.manager.emojis[self.key], self.old)

    async def test_capacity_failure_does_not_delete_old_emoji(self):
        self.bot.fetch_application_emojis.return_value = [remote(f"full_{i}", i) for i in range(2000)]
        with self.assertRaisesRegex(ValueError, "capacity"):
            await self.replace()
        self.bot.create_application_emoji.assert_not_awaited()
        self.assertEqual(self.manager.emojis[self.key], self.old)

    async def test_concurrent_previews_cannot_overwrite_each_other(self):
        results = await asyncio.gather(self.replace(png("red")), self.replace(png("blue")), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, ValueError) for value in results), 1)
        self.bot.create_application_emoji.assert_awaited_once()

    async def test_identical_upload_reuses_emoji_and_reset_uses_default(self):
        uploaded = await self.replace()
        expected = await self.manager.current_revision(self.key)
        self.bot.fetch_application_emojis.return_value = [remote(uploaded.name, uploaded.id), remote(self.base, 1)]
        await self.manager.replace_asset(self.key, png(), 42, expected)
        self.bot.create_application_emoji.assert_awaited_once()
        expected = await self.manager.current_revision(self.key)
        reset = await self.manager.replace_asset(self.key, None, 42, expected)
        self.assertEqual(reset.id, 1)
        self.assertEqual(self.manager.store.all(), {})

    async def test_reset_invalidates_previews_even_when_artwork_returns_to_default(self):
        uploaded = await self.replace()
        self.bot.fetch_application_emojis.return_value = [remote(uploaded.name, 2), remote(self.base, 1)]
        await self.manager.replace_asset(self.key, None, 42, await self.manager.current_revision(self.key))
        with self.assertRaisesRegex(ValueError, "changed after"):
            await self.replace()

    async def test_restart_reuses_saved_override_and_recovers_missing_remote(self):
        uploaded = await self.replace()
        assets = {self.key: discover_emoji_assets()[self.key]}
        self.bot.fetch_application_emojis.return_value = [remote(uploaded.name, uploaded.id)]
        with patch("cogs.ui_emojis.discover_emoji_assets", return_value=assets):
            await self.manager.ensure_synced()
            self.assertEqual(self.manager.emojis[self.key].id, uploaded.id)
            self.bot.create_application_emoji.assert_awaited_once()
            self.manager._synced = False
            self.bot.fetch_application_emojis.return_value = []
            await self.manager.ensure_synced()
            self.assertEqual(self.bot.create_application_emoji.await_count, 2)
            self.assertEqual(self.bot.create_application_emoji.call_args.kwargs["image"],
                             self.manager.store.all()[self.key].image)

    async def test_startup_upload_failure_keeps_old_catalog_usable(self):
        old_name = legacy_emoji_name(self.key, revision=2)
        self.bot.fetch_application_emojis.return_value = [remote(old_name, 77)]
        self.bot.create_application_emoji.side_effect = discord.HTTPException(SimpleNamespace(status=500, reason="error"), "error")
        with patch("cogs.ui_emojis.discover_emoji_assets", return_value={self.key: discover_emoji_assets()[self.key]}):
            await self.manager.ensure_synced()
        self.assertEqual(self.manager.emojis[self.key].id, 77)
        self.assertFalse(self.manager._synced)


class InterfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_reference_variable_resolves_without_collisions(self):
        entries = reference_entries()
        self.assertEqual({entry.key for entry in entries}, set(emoji_asset_keys()))
        for entry in entries:
            self.assertEqual(guide_variable_emoji_key(entry.variable[1:-1]), entry.key, entry)
            self.assertEqual(guide_variable_emoji_key(entry.key), entry.key, entry)
        self.assertEqual(resolve_target("{clown}"), "clown")
        self.assertEqual(resolve_target("passive_snail"), "passive_snail")
        self.assertEqual(resolve_target("snail"), "pet_snail")
        with self.assertRaises(ValueError):
            resolve_target("../../.env")
        self.assertTrue(any(entry.key == "weapon_sword" for entry in search_entries("greatsword", "weapon")))
        self.assertEqual(search_entries("notarealicon"), ())

    async def test_all_browser_pages_fit_discord_limits(self):
        bot = SimpleNamespace()
        for category in ("all", "pet", "weapon", "passive", "rank", "stat", "ui"):
            browser = EmojiBrowser(bot, 42, category=category)
            for page in range(max(1, (len(search_entries(category=category)) + 19) // 20)):
                browser.page = page
                browser.rebuild()
                embed = browser.embed()
                self.assertLessEqual(len(embed), 6000)
                self.assertLessEqual(len(embed.description), 4096)
                for field in embed.fields:
                    self.assertLessEqual(len(field.value), 1024)
                for child in browser.children:
                    if isinstance(child, discord.ui.Select):
                        self.assertLessEqual(len(child.options), 25)
                        self.assertTrue(all(len(option.label) <= 100 for option in child.options))
                        self.assertEqual(sum(option.default for option in child.options), 1)
        empty = EmojiBrowser(bot, 42, query="notarealicon")
        self.assertIn("No icons", empty.embed().description)
        self.assertTrue(empty.next_page.disabled)
        self.assertFalse(await empty.interaction_check(interaction(99)))
        self.assertTrue(await empty.interaction_check(interaction(42)))

    async def test_commands_register_without_network(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        async with bot:
            await bot.add_cog(EmojiTools(bot))
            names = {command.name for command in bot.tree.get_commands()}
            self.assertEqual(names, {"guide-emojis", "emoji-replace", "emoji-reset", "emoji-dex-sync"})
            for command in bot.tree.get_commands():
                self.assertEqual(command.to_dict(bot.tree)["name"], command.name)

    async def test_owner_guard_runs_before_reading_attachments(self):
        cog = EmojiTools(SimpleNamespace(is_owner=AsyncMock(return_value=False)))
        cog.owner_id = 42
        source = SimpleNamespace(size=50, read=AsyncMock(return_value=png()))
        denied = interaction(99)
        await EmojiTools.emoji_replace.callback(cog, denied, "sword", image=source)
        source.read.assert_not_awaited()
        denied.response.send_message.assert_awaited_once()
        self.assertFalse(await cog.is_owner(SimpleNamespace(id=99)))
        self.assertTrue(await cog.is_owner(SimpleNamespace(id=42)))

    async def test_confirmation_owner_cancel_and_double_click_safety(self):
        manager = UIEmojiManager(SimpleNamespace())
        manager.replace_asset = AsyncMock(return_value=discord.PartialEmoji(name="new", id=999))
        cog = EmojiTools(SimpleNamespace(ui_emoji_manager=manager))
        cog.owner_id = 42
        view = EmojiConfirm(cog, 42, "clown", png(), "clown")
        await view.confirm.callback(interaction(99))
        manager.replace_asset.assert_not_awaited()
        await view.confirm.callback(interaction(42))
        await view.confirm.callback(interaction(42))
        manager.replace_asset.assert_awaited_once()
        self.assertTrue(view.done)
        manager.replace_asset.reset_mock()
        cancelled = EmojiConfirm(cog, 42, "clown", png(), "clown")
        await cancelled.cancel.callback(interaction(42))
        await cancelled.confirm.callback(interaction(42))
        manager.replace_asset.assert_not_awaited()

    async def test_confirmation_failure_closes_preview_without_success_message(self):
        manager = UIEmojiManager(SimpleNamespace())
        manager.replace_asset = AsyncMock(side_effect=ValueError("stale preview"))
        cog = EmojiTools(SimpleNamespace(ui_emoji_manager=manager))
        cog.owner_id = 42
        view = EmojiConfirm(cog, 42, "clown", png(), "clown")
        request = interaction()
        await view.confirm.callback(request)
        self.assertEqual(request.edit_original_response.call_args.kwargs["content"], "stale preview")
        self.assertTrue(view.done)

    async def test_preview_does_not_upload_before_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = SimpleNamespace(create_application_emoji=AsyncMock())
            manager = UIEmojiManager(bot)
            manager.store = EmojiOverrideStore(Path(directory) / "overrides.db")
            await manager.cog_load()
            cog = EmojiTools(bot)
            cog.owner_id = 42
            request = interaction()
            await cog.preview(request, "clown", png())
            bot.create_application_emoji.assert_not_awaited()
            sent = request.followup.send.call_args.kwargs
            self.assertIsInstance(sent["view"], EmojiConfirm)
            self.assertEqual(sent["view"].key, "clown")
            self.assertTrue(sent["ephemeral"])
            sent["file"].close()

    def test_backup_includes_override_artwork_database(self):
        self.assertIn("emoji_overrides.db", Path("deploy/backup.sh").read_text())
