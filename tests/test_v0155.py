from __future__ import annotations

import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from cogs.boss_generator import BossGenerator
from cogs.bot_info import BOT_VERSION

from cogs.ui_emojis import (
    MAX_STICKY_EXTERNAL_EMOJIS,
    UIEmojiManager,
    portable_sticky_emoji_name,
)


def png() -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (32, 32), "purple").save(output, format="PNG")
    return output.getvalue()


class PortableStickyEmojiTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def manager(inventory: list):
        async def create_application_emoji(*, name: str, image: bytes):
            emoji = SimpleNamespace(
                name=name,
                id=999999999999999999 - len(inventory),
                animated=False,
            )
            inventory.append(emoji)
            return emoji

        bot = SimpleNamespace(
            fetch_application_emojis=AsyncMock(side_effect=lambda: list(inventory)),
            create_application_emoji=AsyncMock(side_effect=create_application_emoji),
        )
        manager = UIEmojiManager.__new__(UIEmojiManager)
        manager.bot = bot
        manager._sync_lock = asyncio.Lock()
        return manager, bot

    def test_portable_name_is_safe_stable_and_keyed_only_by_source_id(self) -> None:
        first = portable_sticky_emoji_name(
            "<:long_original_name:123456789012345678>"
        )
        renamed = portable_sticky_emoji_name(
            "<:renamed:123456789012345678>"
        )
        self.assertEqual(first, "SK_123456789012345678")
        self.assertEqual(first, renamed)
        self.assertLessEqual(len(first), 32)

    async def test_external_emoji_is_imported_once_and_reused_everywhere(self) -> None:
        inventory: list = []
        manager, bot = self.manager(inventory)
        source_id = 123456789012345678
        note = f"Use <:sword:{source_id}> + <:renamed:{source_id}>"
        with patch(
            "cogs.ui_emojis.download_portable_sticky_emoji",
            new=AsyncMock(return_value=png()),
        ) as download:
            rendered = await manager.make_custom_emojis_portable(
                note,
                SimpleNamespace(emojis=[]),
            )
            rendered_again = await manager.make_custom_emojis_portable(
                f"Again <:another_name:{source_id}>",
                SimpleNamespace(emojis=[]),
            )

        self.assertNotIn(str(source_id) + ">", rendered)
        self.assertEqual(rendered.count("<:SK_123456789012345678:"), 2)
        self.assertIn("<:SK_123456789012345678:", rendered_again)
        self.assertEqual(bot.create_application_emoji.await_count, 1)
        self.assertEqual(download.await_count, 1)

    async def test_usable_guild_local_emoji_is_left_unchanged(self) -> None:
        inventory: list = []
        manager, bot = self.manager(inventory)
        source_id = 123456789012345678
        markup = f"<:local:{source_id}>"
        guild_emoji = SimpleNamespace(
            id=source_id,
            available=True,
            is_usable=lambda: True,
        )
        rendered = await manager.make_custom_emojis_portable(
            markup,
            SimpleNamespace(emojis=[guild_emoji]),
        )
        self.assertEqual(rendered, markup)
        bot.fetch_application_emojis.assert_not_awaited()
        bot.create_application_emoji.assert_not_awaited()

    async def test_plain_text_and_unicode_need_no_emoji_api_call(self) -> None:
        manager, bot = self.manager([])
        note = "Use sword ⚔️ and 🐸"
        self.assertEqual(await manager.make_custom_emojis_portable(note), note)
        bot.fetch_application_emojis.assert_not_awaited()

    async def test_per_note_external_emoji_limit_is_enforced_before_upload(self) -> None:
        manager, bot = self.manager([])
        note = " ".join(
            f"<:e{i}:{10000000000000000 + i}>"
            for i in range(MAX_STICKY_EXTERNAL_EMOJIS + 1)
        )
        with self.assertRaisesRegex(ValueError, "at most"):
            await manager.make_custom_emojis_portable(
                note,
                SimpleNamespace(emojis=[]),
            )
        bot.fetch_application_emojis.assert_not_awaited()
        bot.create_application_emoji.assert_not_awaited()


class PortableStickyPublicSurfaceTests(unittest.TestCase):
    def test_version_and_help_document_portable_sticky_emojis(self) -> None:
        self.assertEqual(BOT_VERSION, "0.15.5-beta")
        cog = BossGenerator.__new__(BossGenerator)
        cog.ui_emoji = lambda _name, fallback: fallback
        embed = BossGenerator.build_help_embed(cog, "h", "o")
        text = "\n".join(field.value for field in embed.fields)
        self.assertIn("`h sticky`", text)
        self.assertIn("portable app emojis", text)
        self.assertIn("20 unique external emojis", text)
        self.assertIn("existing sticky stays unchanged", text)


if __name__ == "__main__":
    unittest.main()