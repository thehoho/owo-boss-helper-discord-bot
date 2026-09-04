"""Public guide-icon reference and owner-only, previewed emoji replacements."""

from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

from .emoji_assets import MAX_UPLOAD_BYTES, custom_emoji_url, normalize_upload
from .game_catalog import PASSIVES, RANKS, WEAPONS, special_animals
from .team_guides import guide_variable_emoji_key
from .ui_emojis import (
    discover_emoji_assets, emoji_asset_keys, get_ui_emoji_manager, prepare_emoji_image,
)

logger = logging.getLogger(__name__)
PAGE_SIZE = 20
CATEGORIES = {"all": "All icons", "pet": "Animals", "weapon": "Weapons",
              "passive": "Passives", "rank": "Ranks", "stat": "Stats", "ui": "Other / guide icons"}


@dataclass(frozen=True)
class EmojiReference:
    key: str
    category: str
    name: str
    aliases: tuple[str, ...]
    variable: str


@lru_cache(maxsize=1)
def reference_entries() -> tuple[EmojiReference, ...]:
    catalog = {entry.emoji_key: (entry.name, entry.aliases) for entry in (*WEAPONS, *PASSIVES, *RANKS)}
    for animal in special_animals():
        catalog[f"pet_{animal['emoji_stem']}"] = (str(animal["name"]), tuple(animal["aliases"]))
    result = []
    for key in sorted(emoji_asset_keys()):
        prefix, _, stem = key.partition("_")
        category = prefix if prefix in CATEGORIES and prefix != "all" else "ui"
        name, aliases = catalog.get(key, (stem.replace("_", " ").title() if category != "ui" else key.replace("_", " ").title(), (stem if category != "ui" else key,)))
        usable = tuple(alias for alias in aliases if guide_variable_emoji_key(alias) == key)
        result.append(EmojiReference(key, category, name, aliases, "{" + (usable[0] if usable else key) + "}"))
    return tuple(result)


def search_entries(query: str = "", category: str = "all") -> tuple[EmojiReference, ...]:
    terms = query.strip().casefold().replace("{", "").replace("}", "").split()
    return tuple(entry for entry in reference_entries()
                 if (category == "all" or entry.category == category)
                 and all(term in " ".join((entry.key, entry.name, *entry.aliases)).casefold() for term in terms))


def resolve_target(value: str) -> str:
    token = value.strip().strip("{}").casefold()
    key = token if token in emoji_asset_keys() else guide_variable_emoji_key(token)
    if key not in emoji_asset_keys():
        raise ValueError("Unknown target. Use /guide-emojis to choose an exact target key.")
    return key


class ReferenceSelect(discord.ui.Select):
    def __init__(self, browser: EmojiBrowser) -> None:
        manager = get_ui_emoji_manager(browser.bot)
        entries = browser.visible_entries()
        options = [discord.SelectOption(label=entry.name[:100], value=entry.key,
                   description=f"{entry.variable} | {entry.key}"[:100],
                   emoji=manager.emojis.get(entry.key) if manager else None,
                   default=entry.key == browser.selected) for entry in entries]
        super().__init__(placeholder="Choose an icon for its names and enlarged preview",
                         options=options or [discord.SelectOption(label="No matching icons", value="none")],
                         disabled=not entries, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.selected = self.values[0]
        await self.view.refresh(interaction)


class CategorySelect(discord.ui.Select):
    def __init__(self, browser: EmojiBrowser) -> None:
        super().__init__(placeholder="Category", row=0, options=[
            discord.SelectOption(label=label, value=key, default=key == browser.category)
            for key, label in CATEGORIES.items()
        ])

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.category = self.values[0]
        self.view.page = 0
        self.view.selected = None
        await self.view.refresh(interaction)


class ReferenceSearch(discord.ui.Modal, title="Find a guide icon"):
    query = discord.ui.TextInput(label="Name, alias, or exact key", required=False, max_length=80)

    def __init__(self, browser: EmojiBrowser) -> None:
        super().__init__()
        self.browser = browser
        self.query.default = browser.query

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.browser.interaction_check(interaction):
            return
        self.browser.query = str(self.query)
        self.browser.page = 0
        self.browser.selected = None
        await self.browser.refresh(interaction)


class EmojiBrowser(discord.ui.View):
    def __init__(self, bot: commands.Bot, user_id: int, query: str = "", category: str = "all") -> None:
        super().__init__(timeout=600)
        self.bot, self.user_id, self.query, self.category = bot, user_id, query, category
        self.page = 0
        self.selected: str | None = None
        self.rebuild()

    def visible_entries(self) -> tuple[EmojiReference, ...]:
        entries = search_entries(self.query, self.category)
        self.page = min(self.page, max(0, (len(entries) - 1) // PAGE_SIZE))
        return entries[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]

    def rebuild(self) -> None:
        for child in tuple(self.children):
            if isinstance(child, discord.ui.Select):
                self.remove_item(child)
        visible = self.visible_entries()
        if self.selected not in {entry.key for entry in visible}:
            self.selected = visible[0].key if visible else None
        self.add_item(CategorySelect(self))
        self.add_item(ReferenceSelect(self))
        count = len(search_entries(self.query, self.category))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * PAGE_SIZE >= count

    def embed(self) -> discord.Embed:
        manager = get_ui_emoji_manager(self.bot)
        entries = self.visible_entries()
        lines = []
        for entry in entries:
            icon = manager.text(entry.key, "•") if manager else "•"
            lines.append(f"{icon} **{entry.name}** — `{entry.variable}`")
        embed = discord.Embed(title="Guide emoji reference", color=0x57F287,
                              description="\n".join(lines) or "No icons match. Try another search or category.")
        selected = next((entry for entry in entries if entry.key == self.selected), None)
        if selected:
            aliases = ", ".join(selected.aliases)
            embed.add_field(name=f"Selected: {selected.name}", inline=False,
                            value=f"Guide syntax: `{selected.variable}`\nExact target: `{selected.key}`\nAliases: {aliases[:650]}\nAliases may overlap; the guide syntax above is unambiguous.")
            emoji = manager.emojis.get(selected.key) if manager else None
            if emoji:
                embed.set_image(url=str(emoji.url))
        count = len(search_entries(self.query, self.category))
        embed.set_footer(text=f"{CATEGORIES[self.category]} • Page {self.page + 1}/{max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)} • {count} icons • Inline size is controlled by Discord")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own reference with /guide-emojis.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=2)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self.selected = None
        await self.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        self.selected = None
        await self.refresh(interaction)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, row=2)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ReferenceSearch(self))


async def download_custom_emoji(value: str) -> bytes:
    url = custom_emoji_url(value)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("Discord could not provide this emoji. Attach the original image instead.")
            chunks = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                chunks.extend(chunk)
                if len(chunks) > MAX_UPLOAD_BYTES:
                    raise ValueError("The source emoji exceeds the 2 MiB upload limit.")
            return bytes(chunks)


class EmojiConfirm(discord.ui.View):
    def __init__(self, cog: EmojiTools, user_id: int, key: str, image: bytes | None, expected: str) -> None:
        super().__init__(timeout=180)
        self.cog, self.user_id, self.key, self.image, self.expected = cog, user_id, key, image, expected
        self._lock = asyncio.Lock()
        self.done = False
        self.confirm.label = "Reset to default" if image is None else "Use this emoji"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not await self.cog.is_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner who opened this preview can confirm it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Use this emoji", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer()
        async with self._lock:
            if self.done:
                await interaction.followup.send("This preview has already been handled.", ephemeral=True)
                return
            self.done = True
            try:
                manager = get_ui_emoji_manager(self.cog.bot)
                if manager is None:
                    raise ValueError("The emoji manager is unavailable. Please try again later.")
                emoji = await manager.replace_asset(self.key, self.image, interaction.user.id, self.expected)
                message = f"Saved {emoji} for `{self.key}` globally. Open the guide again to see it. Previously posted messages retain their old emoji."
            except ValueError as exc:
                message = str(exc)
            except Exception:
                logger.exception("Emoji replacement failed for %s", self.key)
                message = "The change could not be saved. The previous mapping remains active. Please open a new preview and try again."
            await interaction.edit_original_response(content=message, embed=None, attachments=[], view=None)
            self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.interaction_check(interaction):
            return
        async with self._lock:
            if self.done:
                await interaction.response.send_message("This preview has already been handled.", ephemeral=True)
                return
            self.done = True
            await interaction.response.edit_message(content="Cancelled. No emoji was changed.", embed=None, attachments=[], view=None)
            self.stop()


class EmojiTools(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        try:
            self.owner_id = int(os.getenv("BOT_OWNER_ID", "0") or 0)
        except ValueError:
            self.owner_id = 0

    async def is_owner(self, user: discord.abc.User) -> bool:
        if self.owner_id:
            return user.id == self.owner_id
        return await self.bot.is_owner(user)

    async def require_owner(self, interaction: discord.Interaction) -> bool:
        if await self.is_owner(interaction.user):
            return True
        await interaction.response.send_message("Only the bot owner can replace application emojis.", ephemeral=True)
        return False

    @app_commands.command(name="guide-emojis", description="Browse guide icons, aliases, syntax, and enlarged previews.")
    @app_commands.choices(category=[app_commands.Choice(name=label, value=key) for key, label in CATEGORIES.items()])
    async def guide_emojis(self, interaction: discord.Interaction, query: str = "", category: str = "all") -> None:
        browser = EmojiBrowser(self.bot, interaction.user.id, query[:80], category)
        await interaction.response.send_message(embed=browser.embed(), view=browser, ephemeral=True)

    async def target_choices(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=f"{entry.name} — {entry.key}"[:100], value=entry.key)
                for entry in search_entries(current)[:25]]

    async def preview(self, interaction: discord.Interaction, key: str, raw: bytes | None) -> None:
        manager = get_ui_emoji_manager(self.bot)
        if manager is None:
            raise ValueError("The emoji manager is unavailable. Please try again later.")
        expected = await manager.current_revision(key)
        if raw is None:
            payload = await asyncio.to_thread(prepare_emoji_image, discover_emoji_assets()[key])
        else:
            payload = await asyncio.to_thread(normalize_upload, raw)
        with Image.open(io.BytesIO(payload)) as image:
            extension = {"GIF": "gif", "WEBP": "webp"}.get(image.format, "png")
        filename = f"emoji-preview.{extension}"
        embed = discord.Embed(title=f"{'Reset' if raw is None else 'Replace'} {key}?",
                              description="Large image: proposed artwork. Thumbnail: current emoji.\nConfirm to use this icon globally in future bot messages. Old emoji IDs are retained so existing messages keep working.\nNo change is made until you confirm.",
                              color=0xFEE75C)
        embed.set_image(url=f"attachment://{filename}")
        current = manager.emojis.get(key)
        if current:
            embed.set_thumbnail(url=str(current.url))
        await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(payload), filename=filename),
                                        view=EmojiConfirm(self, interaction.user.id, key, payload if raw is not None else None, expected),
                                        ephemeral=True)

    @app_commands.command(name="emoji-replace", description="Owner only: preview replacing a bot icon with an image or Discord emoji.")
    @app_commands.describe(target="Choose the exact icon to replace", source="Paste one custom Discord emoji (or attach an image instead)",
                           image="Original artwork, at most 2 MiB; use either image or source")
    @app_commands.autocomplete(target=target_choices)
    async def emoji_replace(self, interaction: discord.Interaction, target: str,
                            source: str | None = None, image: discord.Attachment | None = None) -> None:
        if not await self.require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            key = resolve_target(target)
            if (source is None) == (image is None):
                raise ValueError("Provide exactly one source: a custom Discord emoji OR an attached image.")
            if image is not None:
                if image.size > MAX_UPLOAD_BYTES:
                    raise ValueError("The source image must be at most 2 MiB.")
                raw = await image.read()
            else:
                raw = await download_custom_emoji(source)
            await self.preview(interaction, key, raw)
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError, discord.HTTPException) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "The image could not be downloaded. Attach the original image and try again."
            await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="emoji-reset", description="Owner only: preview restoring a bot icon to its packaged artwork.")
    @app_commands.autocomplete(target=target_choices)
    async def emoji_reset(self, interaction: discord.Interaction, target: str) -> None:
        if not await self.require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.preview(interaction, resolve_target(target), None)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EmojiTools(bot))
