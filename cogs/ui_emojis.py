"""Application-owned emoji registry for OwO Boss Helper UI assets.

PNG files remain in the repository as source assets. On startup, the bot lists its
application-owned emojis and creates any missing names through Discord's application
emoji API. Other cogs use this registry and gracefully fall back to Unicode if an
asset is missing or Discord rejects an upload.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands
from PIL import Image

from .emoji_assets import EmojiOverrideStore, emoji_label, normalize_upload, override_name, versioned_name

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = PROJECT_ROOT / "assets" / "ui_emojis"
GAME_ASSET_DIR = PROJECT_ROOT / "assets" / "game_emojis"
MAX_EMOJI_BYTES = 256 * 1024
MAX_APPLICATION_EMOJIS = 2000
MAX_EMOJI_NAME_LENGTH = 32
GAME_EMOJI_ASSET_REVISION = 3
SUPPORTED_ASSET_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})
DEX_ARTWORK: dict[str, tuple] = {}

EMOJI_FILES: dict[str, str] = {
    "ticket_available": "ticket_available.png",
    "ticket_used": "ticket_used.png",
    "boss_appeared": "boss_appeared.png",
    "boss_escaped": "boss_escaped.png",
    "boss_defeated": "boss_defeated.png",
    "neon_calculate": "neon_calculate.png",
    "clown": "clown.png",
}

GAME_EMOJI_CATEGORIES: dict[str, str] = {
    "pet": "animals",
    "weapon": "weapons",
    "passive": "passives",
    "rank": "ranks",
    "stat": "stats",
}


def discover_emoji_assets() -> dict[str, Path]:
    """Return every application emoji name and its source file."""
    assets = {name: ASSET_DIR / filename for name, filename in EMOJI_FILES.items()}
    for emoji_prefix, directory_name in GAME_EMOJI_CATEGORIES.items():
        directory = GAME_ASSET_DIR / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.casefold() in SUPPORTED_ASSET_SUFFIXES:
                name = f"{emoji_prefix}_{path.stem.casefold()}"
                if len(name) > MAX_EMOJI_NAME_LENGTH or not re.fullmatch(r"[a-z0-9_]+", name):
                    logger.warning("Skipped Discord-unsafe application emoji name: %s", name)
                    continue
                if name in assets:
                    logger.warning("Skipped duplicate application emoji name: %s", name)
                    continue
                assets[name] = path
    for key in DEX_ARTWORK:
        assets.setdefault(key, PROJECT_ROOT / "emoji_dex_assets" / key)
    return assets


@lru_cache(maxsize=1)
def emoji_asset_keys() -> frozenset[str]:
    return frozenset(discover_emoji_assets())


@lru_cache(maxsize=1)
def emoji_alias_keys() -> dict[str, str]:
    aliases = {emoji_label(key).casefold(): key for key in emoji_asset_keys()}
    # Keep guides written before the passive-prefix correction usable.
    aliases.update({"bs_" + key.removeprefix("passive_"): key
                    for key in emoji_asset_keys() if key.startswith("passive_")})
    return aliases


def clear_emoji_catalog_cache() -> None:
    emoji_asset_keys.cache_clear()
    emoji_alias_keys.cache_clear()


def default_emoji_image(key: str) -> bytes:
    return DEX_ARTWORK[key][0] if key in DEX_ARTWORK else prepare_emoji_image(discover_emoji_assets()[key])


def deployed_emoji_name(logical_name: str, revision: int = GAME_EMOJI_ASSET_REVISION) -> str:
    if logical_name in DEX_ARTWORK:
        digest = hashlib.sha256(DEX_ARTWORK[logical_name][0]).hexdigest()[:12]
        return versioned_name(logical_name, "d" + digest)
    return versioned_name(logical_name, f"v{revision}")


def legacy_emoji_name(logical_name: str, revision: int = GAME_EMOJI_ASSET_REVISION) -> str:
    """Return the Discord-side name while keeping stable logical lookups."""
    if logical_name in EMOJI_FILES:
        return logical_name
    prefix = f"v{revision}_"
    candidate = f"{prefix}{logical_name}"
    if len(candidate) <= MAX_EMOJI_NAME_LENGTH:
        return candidate
    digest = hashlib.sha1(logical_name.encode("utf-8")).hexdigest()[:6]
    available = MAX_EMOJI_NAME_LENGTH - len(prefix) - len(digest) - 1
    return f"{prefix}{logical_name[:available]}_{digest}"


def prepare_emoji_image(path: Path) -> bytes:
    """Normalize source artwork to a centered 128×128 transparent PNG."""
    raw = path.read_bytes()
    try:
        with Image.open(io.BytesIO(raw)) as source:
            # Discord supports animated WebP/GIF application emojis. Preserve
            # small catalog animations instead of flattening their first frame.
            if (
                bool(getattr(source, "is_animated", False))
                and source.width <= 128
                and source.height <= 128
                and len(raw) <= MAX_EMOJI_BYTES
            ):
                return raw
            image = source.convert("RGBA")
            alpha_bounds = image.getchannel("A").getbbox()
            if alpha_bounds:
                image = image.crop(alpha_bounds)
            scale = min(128 / image.width, 128 / image.height)
            target_size = (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            )
            resampling = (
                Image.Resampling.NEAREST
                if scale >= 1
                else Image.Resampling.LANCZOS
            )
            image = image.resize(target_size, resampling)
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
        self.store = EmojiOverrideStore(PROJECT_ROOT / "emoji_overrides.db")
        setattr(bot, "ui_emoji_manager", self)

    async def cog_load(self) -> None:
        await asyncio.to_thread(self.store.initialize)
        DEX_ARTWORK.clear()
        DEX_ARTWORK.update(await asyncio.to_thread(self.store.dex_all))
        clear_emoji_catalog_cache()

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
            overrides = await asyncio.to_thread(self.store.all)
            created = 0
            reused = 0
            missing_assets: list[str] = []
            failed: list[str] = []

            emoji_assets = discover_emoji_assets()
            # Keep current guides usable while the enlarged catalog uploads.
            for name in emoji_assets:
                previous = by_name.get(legacy_emoji_name(name, revision=2))
                if previous is not None:
                    self.emojis[name] = self._to_partial(previous)
            for name, path in emoji_assets.items():
                override = overrides.get(name)
                remote_name = override_name(name, override.image) if override else deployed_emoji_name(name)
                fallback = by_name.get(deployed_emoji_name(name))
                if fallback is not None:
                    self.emojis[name] = self._to_partial(fallback)
                current = by_name.get(remote_name)
                if current is None:
                    old_name = override.name if override else legacy_emoji_name(name)
                    # The previous release used BS_ for passives. Prefer its
                    # active artwork over older packaged versions; retain IDs.
                    previous_prefix_name = "BS_" + remote_name[3:] if name.startswith("passive_") else old_name
                    current = by_name.get(old_name) if override else by_name.get(previous_prefix_name)
                    if current is None:
                        current = by_name.get(old_name)
                    if current is not None:
                        # Rename in place: no new IDs, no artwork loss, no quota cost.
                        try:
                            renamed = await current.edit(name=remote_name)
                            if override:
                                await asyncio.to_thread(self.store.rename, name, override.name, remote_name)
                            current = renamed
                            logger.info("Normalized application emoji %s without changing ID %s", name, current.id)
                        except Exception:
                            logger.exception("Could not normalize emoji name for %s; keeping its existing ID", name)
                            failed.append(name)
                if current is not None:
                    self.emojis[name] = self._to_partial(current)
                    reused += 1
                    continue

                if not override and name not in DEX_ARTWORK and not path.is_file():
                    missing_assets.append(str(path.relative_to(PROJECT_ROOT)))
                    continue

                if len(by_name) + created >= MAX_APPLICATION_EMOJIS:
                    logger.warning(
                        "Application emoji capacity reached at %s; could not create %s",
                        MAX_APPLICATION_EMOJIS,
                        remote_name,
                    )
                    failed.append(name)
                    continue

                image = override.image if override else await asyncio.to_thread(default_emoji_image, name)
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
                        name=remote_name,
                        image=image,
                    )
                except (discord.HTTPException, discord.Forbidden, discord.MissingApplicationID) as exc:
                    logger.warning("Could not create application emoji %s: %s", remote_name, exc)
                    failed.append(name)
                    continue

                self.emojis[name] = self._to_partial(emoji)
                created += 1

            self._synced = not failed and not missing_assets
            logger.info(
                "Application emoji registry ready: %s configured, %s reused, %s created, %s missing assets, %s failed",
                len(emoji_assets),
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

    async def current_revision(self, key: str) -> str:
        return await asyncio.to_thread(self.store.revision, key, deployed_emoji_name(key))

    async def install_dex_asset(self, key: str, raw: bytes, display_name: str, aliases_json: str, source_url: str, inventory: list | None = None) -> None:
        if not re.fullmatch(r"pet_[a-z0-9_]{1,59}", key):
            raise ValueError("Animal name cannot be represented safely as a guide emoji key.")
        image = await asyncio.to_thread(normalize_upload, raw)
        async with self._sync_lock:
            override = (await asyncio.to_thread(self.store.all)).get(key)
            emoji = None
            if override is None:
                name = versioned_name(key, "d" + hashlib.sha256(image).hexdigest()[:12])
                existing = inventory if inventory is not None else await self.bot.fetch_application_emojis()
                emoji = next((item for item in existing if item.name == name), None)
                if emoji is None:
                    if len(existing) >= MAX_APPLICATION_EMOJIS:
                        raise ValueError("Application emoji capacity is full; current artwork was preserved.")
                    emoji = await self.bot.create_application_emoji(name=name, image=image)
                    existing.append(emoji)
            await asyncio.to_thread(self.store.save_dex, key, image, display_name, aliases_json, source_url)
            DEX_ARTWORK[key] = (image, display_name, aliases_json, source_url)
            clear_emoji_catalog_cache()
            if emoji:
                self.emojis[key] = self._to_partial(emoji)

    async def replace_asset(
        self, key: str, image: bytes | None, actor_id: int, expected: str,
    ) -> discord.PartialEmoji:
        """Create first, commit the override atomically, then switch live lookups.

        Old application emojis are deliberately retained for existing messages.
        A failed upload or database transaction cannot erase the current mapping.
        """
        if key not in emoji_asset_keys():
            raise ValueError("Unknown emoji target. Choose one from /guide-emojis.")
        if image is not None:
            image = await asyncio.to_thread(normalize_upload, image)
        async with self._sync_lock:
            if await self.current_revision(key) != expected:
                raise ValueError("This emoji changed after your preview. Open a new preview before confirming.")
            remote_name = override_name(key, image) if image is not None else deployed_emoji_name(key)
            existing = await self.bot.fetch_application_emojis()
            emoji = next((item for item in existing if item.name == remote_name), None)
            if emoji is None:
                if len(existing) >= MAX_APPLICATION_EMOJIS:
                    raise ValueError("Application emoji capacity is full. Nothing was replaced; old emojis were retained.")
                payload = image if image is not None else await asyncio.to_thread(
                    default_emoji_image, key,
                )
                if len(payload) > MAX_EMOJI_BYTES:
                    raise ValueError("The prepared image exceeds Discord's 256 KiB limit.")
                emoji = await self.bot.create_application_emoji(name=remote_name, image=payload)
            if image is None:
                await asyncio.to_thread(self.store.reset, key, actor_id)
            else:
                await asyncio.to_thread(self.store.save, key, remote_name, image, emoji.id, actor_id)
            self.emojis[key] = self._to_partial(emoji)
            logger.info("Application emoji %s %s by owner %s; active ID %s",
                        key, "reset" if image is None else "replaced", actor_id, emoji.id)
            return self.emojis[key]

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
