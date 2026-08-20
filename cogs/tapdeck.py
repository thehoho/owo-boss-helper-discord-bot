"""Public information card for the separate TapDeck Lite Android project."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .helper_prefix import get_guild_helper_prefix, parse_helper_command_argument

logger = logging.getLogger(__name__)

TAPDECK_REPOSITORY_URL = "https://github.com/thehoho/TapDeck-Lite"
TAPDECK_LATEST_RELEASE_URL = f"{TAPDECK_REPOSITORY_URL}/releases/latest"
TAPDECK_LATEST_RELEASE_API_URL = (
    "https://api.github.com/repos/thehoho/TapDeck-Lite/releases/latest"
)
TAPDECK_PRIVACY_URL = "https://thehoho.github.io/TapDeck-Lite/privacy"
TAPDECK_RELEASE_CACHE_SECONDS = 24 * 60 * 60
TAPDECK_RELEASE_RETRY_SECONDS = 15 * 60
TAPDECK_PREFIX_ALIASES = {
    "h grind",
    "hgrind",
    "h tapdeck",
    "htapdeck",
}


class TapDeckReleaseError(ValueError):
    """Raised when GitHub's latest-release payload is incomplete or unsafe."""


@dataclass(frozen=True)
class TapDeckRelease:
    tag_name: str
    release_url: str
    apk_url: str


def _is_trusted_github_url(value: str, path_prefix: str) -> bool:
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme.casefold() == "https"
        and parsed.hostname is not None
        and parsed.hostname.casefold() == "github.com"
        and parsed.port is None
        and parsed.path.casefold().startswith(path_prefix.casefold())
    )


def parse_latest_release_payload(payload: Any) -> TapDeckRelease:
    """Extract the latest trusted APK link from GitHub's public release payload."""
    if not isinstance(payload, dict):
        raise TapDeckReleaseError("GitHub returned an invalid release document.")

    tag_name = payload.get("tag_name")
    release_url = payload.get("html_url")
    assets = payload.get("assets")
    if not isinstance(tag_name, str) or not tag_name or len(tag_name) > 40:
        raise TapDeckReleaseError("GitHub's release tag is missing or invalid.")
    if any(character.isspace() or ord(character) < 32 for character in tag_name):
        raise TapDeckReleaseError("GitHub's release tag contains unsafe characters.")
    if not isinstance(release_url, str) or not _is_trusted_github_url(
        release_url,
        "/thehoho/TapDeck-Lite/releases/tag/",
    ):
        raise TapDeckReleaseError("GitHub's release page URL is missing or invalid.")
    if not isinstance(assets, list):
        raise TapDeckReleaseError("GitHub's release asset list is missing.")

    apk_candidates: list[tuple[bool, str, str]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        download_url = asset.get("browser_download_url")
        if not isinstance(name, str) or not name.casefold().endswith(".apk"):
            continue
        if not isinstance(download_url, str) or not _is_trusted_github_url(
            download_url,
            "/thehoho/TapDeck-Lite/releases/download/",
        ):
            continue
        preferred_name = name.casefold().startswith("tapdeck-lite-")
        apk_candidates.append((not preferred_name, name.casefold(), download_url))

    if not apk_candidates:
        raise TapDeckReleaseError("The latest GitHub release has no trusted APK asset.")
    apk_candidates.sort()
    return TapDeckRelease(
        tag_name=tag_name,
        release_url=release_url,
        apk_url=apk_candidates[0][2],
    )


class TapDeckReleaseResolver:
    """Resolve GitHub's latest APK at most daily, with a short failure backoff."""

    def __init__(self) -> None:
        self._cached_release: TapDeckRelease | None = None
        self._cached_at = 0.0
        self._last_attempt_at = 0.0
        self._lock = asyncio.Lock()

    async def resolve(self) -> TapDeckRelease | None:
        now = time.monotonic()
        if (
            self._cached_release is not None
            and now - self._cached_at < TAPDECK_RELEASE_CACHE_SECONDS
        ):
            return self._cached_release
        if now - self._last_attempt_at < TAPDECK_RELEASE_RETRY_SECONDS:
            return self._cached_release

        async with self._lock:
            now = time.monotonic()
            if (
                self._cached_release is not None
                and now - self._cached_at < TAPDECK_RELEASE_CACHE_SECONDS
            ):
                return self._cached_release
            if now - self._last_attempt_at < TAPDECK_RELEASE_RETRY_SECONDS:
                return self._cached_release
            self._last_attempt_at = now

            try:
                timeout = aiohttp.ClientTimeout(total=8)
                async with aiohttp.ClientSession(
                    headers={"User-Agent": "OwO-Boss-Helper/TapDeck-release-check"}
                ) as session:
                    async with session.get(
                        TAPDECK_LATEST_RELEASE_API_URL,
                        headers={
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                        timeout=timeout,
                    ) as response:
                        if response.status != 200:
                            raise TapDeckReleaseError(
                                f"GitHub latest-release lookup returned HTTP {response.status}."
                            )
                        payload = await response.json(content_type=None)
                release = parse_latest_release_payload(payload)
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                TapDeckReleaseError,
                TypeError,
                ValueError,
            ) as exc:
                logger.warning("TapDeck latest-release lookup failed: %s", exc)
                return self._cached_release

            self._cached_release = release
            self._cached_at = time.monotonic()
            logger.info(
                "Resolved TapDeck Lite latest release %s from GitHub",
                release.tag_name,
            )
            return release


class TapDeckLinks(discord.ui.View):
    def __init__(self, release: TapDeckRelease | None = None) -> None:
        super().__init__(timeout=None)
        if release is not None:
            self.add_item(
                discord.ui.Button(
                    label=f"Download {release.tag_name} APK",
                    emoji="📱",
                    url=release.apk_url,
                )
            )
            self.add_item(
                discord.ui.Button(
                    label="Release notes",
                    emoji="🗒️",
                    url=release.release_url,
                )
            )
        else:
            self.add_item(
                discord.ui.Button(
                    label="Open latest release",
                    emoji="📱",
                    url=TAPDECK_LATEST_RELEASE_URL,
                )
            )
        self.add_item(
            discord.ui.Button(
                label="Public source",
                emoji="🔍",
                url=TAPDECK_REPOSITORY_URL,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Privacy",
                emoji="🔒",
                url=TAPDECK_PRIVACY_URL,
            )
        )


def build_tapdeck_embed(release: TapDeckRelease | None = None) -> discord.Embed:
    release_url = (
        release.release_url if release is not None else TAPDECK_LATEST_RELEASE_URL
    )
    embed = discord.Embed(
        title="📱 TapDeck Lite, one tap, one command",
        url=release_url,
        description=(
            "A compact Android shortcut keyboard for command-based Discord chats/bots. "
            "Configure up to 20 keys, then one manual tap inserts and sends the "
            "selected command."
        ),
        color=0x70E1B5,
    )
    embed.add_field(
        name="How it helps",
        value=(
            "• Save short or long commands up to 4,000 characters.\n"
            "• Choose **Insert only** or **Insert + send** per key.\n"
            "• Long-press and drag keys to reorder them.\n"
            "• Use **ABC** to return to your normal keyboard."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy and transparency",
        value=(
            "The public source documents **zero Android permissions**, no Internet "
            "capability, no ads/analytics/tracking, disabled cloud backup, and command "
            "storage only in app-private local preferences."
        ),
        inline=False,
    )
    embed.add_field(
        name="Rules and installation",
        value=(
            "The demonstrated **one tap = one command** workflow was reviewed by an "
            "OwO administrator and described as following the rules. Rules can change, "
            "so users remain responsible for following current OwO and Discord rules.\n\n"
            "TapDeck Lite is distributed from GitHub rather than Google Play, so Android "
            "will show normal unknown-source and third-party-keyboard warnings. Review the "
            "public source and install only if you trust the developer."
        ),
        inline=False,
    )
    embed.set_footer(
        text="Independent project by Hassaan • Not affiliated with Discord or OwO Bot"
    )
    return embed


class TapDeckInfo(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.release_resolver = TapDeckReleaseResolver()

    async def latest_release(self) -> TapDeckRelease | None:
        return await self.release_resolver.resolve()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        helper_prefix = await get_guild_helper_prefix(message.guild.id)
        argument = parse_helper_command_argument(
            message.content or "",
            helper_prefix,
            TAPDECK_PREFIX_ALIASES,
        )
        if argument is None:
            return
        if argument:
            await message.reply(
                "This command does not need any options. Use it by itself to open the TapDeck Lite card.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        release = await self.latest_release()
        await message.reply(
            embed=build_tapdeck_embed(release),
            view=TapDeckLinks(release),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        logger.info(
            "TapDeck information requested by user %s in guild %s",
            message.author.id,
            message.guild.id,
        )

    @app_commands.command(name="tapdeck", description="Learn about and download TapDeck Lite for Android.")
    async def tapdeck(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        release = await self.latest_release()
        await interaction.followup.send(
            embed=build_tapdeck_embed(release),
            view=TapDeckLinks(release),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TapDeckInfo(bot))
