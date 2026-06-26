"""Application-owned emoji registry for OwO Boss Helper UI assets.

PNG files remain in the repository as source assets. On startup, the bot lists its
application-owned emojis and creates any missing names through Discord's application
emoji API. Other cogs use this registry and gracefully fall back to Unicode if an
asset is missing or Discord rejects an upload.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands
from PIL import Image

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = PROJECT_ROOT / "assets" / "ui_emojis"
MAX_EMOJI_BYTES = 256 * 1024

EMOJI_FILES: dict[str, str] = {
    "ticket_available": "ticket_available.png",
    "ticket_used": "ticket_used.png",
    "boss_appeared": "boss_appeared.png",
    "boss_escaped": "boss_escaped.png",
    "boss_defeated": "boss_defeated.png",
}


def prepare_emoji_image(path: Path) -> bytes:
    """Normalize source artwork to a centered 128×128 transparent PNG."""
    raw = path.read_bytes()
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGBA")
            image.thumbnail((128, 128), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
            canvas.alpha_composite(
                image,
                ((128 - image.width) // 2, (128 - image.height) // 2),
            )
            output = io.BytesIO()
            canvas.save(output, format="PNG", optimize=True, compress_level=9)
            normalized = output.getvalue()
            return normalized if len(normalized) <= MAX_EMOJI_BYTES else raw
    except (OSError, ValueError):
        return raw


class UIEmojiManager(commands.Cog):
    """Load and expose application-owned emojis by stable source-file name."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.emojis: dict[str, discord.PartialEmoji] = {}
        self._sync_lock = asyncio.Lock()
        self._synced = False
        setattr(bot, "ui_emoji_manager", self)

    async def cog_unload(self) -> None:
        if getattr(self.bot, "ui_emoji_manager", None) is self:
            delattr(self.bot, "ui_emoji_manager")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.ensure_synced()

    @staticmethod
    def _to_partial(emoji: Any) -> discord.PartialEmoji:
        return discord.PartialEmoji(
            name=str(getattr(emoji, "name", "emoji")),
            id=int(getattr(emoji, "id")),
            animated=bool(getattr(emoji, "animated", False)),
        )

    async def ensure_synced(self) -> None:
        if self._synced:
            return
        async with self._sync_lock:
            if self._synced:
                return

            try:
                existing = await self.bot.fetch_application_emojis()
            except (discord.HTTPException, discord.Forbidden, discord.MissingApplicationID) as exc:
                logger.warning(
                    "Could not list application emojis; Unicode fallbacks will be used: %s",
                    exc,
                )
                return

            by_name = {str(emoji.name): emoji for emoji in existing}
            created = 0
            reused = 0
            missing_assets: list[str] = []
            failed: list[str] = []

            for name, filename in EMOJI_FILES.items():
                current = by_name.get(name)
                if current is not None:
                    self.emojis[name] = self._to_partial(current)
                    reused += 1
                    continue

                path = ASSET_DIR / filename
                if not path.is_file():
                    missing_assets.append(filename)
                    continue

                image = prepare_emoji_image(path)
                if len(image) > MAX_EMOJI_BYTES:
                    logger.warning(
                        "Application emoji asset %s is too large (%s bytes; max %s)",
                        path,
                        len(image),
                        MAX_EMOJI_BYTES,
                    )
                    failed.append(name)
                    continue

                try:
                    emoji = await self.bot.create_application_emoji(
                        name=name,
                        image=image,
                    )
                except (discord.HTTPException, discord.Forbidden, discord.MissingApplicationID) as exc:
                    logger.warning("Could not create application emoji %s: %s", name, exc)
                    failed.append(name)
                    continue

                self.emojis[name] = self._to_partial(emoji)
                created += 1

            self._synced = True
            logger.info(
                "Application emoji registry ready: %s reused, %s created, %s missing assets, %s failed",
                reused,
                created,
                len(missing_assets),
                len(failed),
            )
            if missing_assets:
                logger.warning(
                    "Missing UI emoji source files in %s: %s",
                    ASSET_DIR,
                    ", ".join(missing_assets),
                )

    def text(self, name: str, fallback: str) -> str:
        emoji = self.emojis.get(name)
        return str(emoji) if emoji is not None else fallback

    def button(self, name: str, fallback: str) -> discord.PartialEmoji | str:
        return self.emojis.get(name, fallback)


def get_ui_emoji_manager(bot: commands.Bot) -> UIEmojiManager | None:
    manager = getattr(bot, "ui_emoji_manager", None)
    return manager if isinstance(manager, UIEmojiManager) else None


async def ensure_ui_emojis(bot: commands.Bot) -> None:
    manager = get_ui_emoji_manager(bot)
    if manager is not None:
        await manager.ensure_synced()


def ui_emoji_text(bot: commands.Bot, name: str, fallback: str) -> str:
    manager = get_ui_emoji_manager(bot)
    return manager.text(name, fallback) if manager is not None else fallback


def ui_emoji_button(
    bot: commands.Bot,
    name: str,
    fallback: str,
) -> discord.PartialEmoji | str:
    manager = get_ui_emoji_manager(bot)
    return manager.button(name, fallback) if manager is not None else fallback


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UIEmojiManager(bot))
