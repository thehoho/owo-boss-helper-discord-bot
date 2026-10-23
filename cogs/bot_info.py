"""Public bot information and owner-only operational statistics.

The public `H about` and `/about` commands identify the developer and explain the
project. Owner-only commands expose server reach and usage without relying on log
files. Server metadata and aggregate usage counters are stored locally in SQLite.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import os
import platform
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from .helper_prefix import (
    HELPER_PREFIX_DEFAULT,
    canonicalize_helper_command,
    get_guild_helper_prefix,
)
from .message_utils import safe_reply

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "bot_stats.db"
TEAM_DATABASE_FILE = PROJECT_ROOT / "team_templates.db"
TICKET_DATABASE_FILE = PROJECT_ROOT / "boss_tickets.db"
XP_DATABASE_FILE = PROJECT_ROOT / "xp_reminders.db"
LOG_FILE = PROJECT_ROOT / "logs" / "bot.log"

BOT_VERSION = "0.16.18-beta"
DEFAULT_DEVELOPER_NAME = "Hassaan"
DEFAULT_GITHUB_URL = "https://github.com/thehoho/owo-boss-helper-discord-bot"
DEFAULT_DESCRIPTION = (
    "OwO Boss Helper makes guild-boss fights easier by generating ordered Neon "
    "commands with live HP, tracking boss cooldowns and tickets, scanning Neon weapon pages, and saving guided "
    "team templates with exact weapon IDs."
)
ABOUT_COMMANDS = {"habout"}
PERIODIC_SYNC_SECONDS = 6 * 60 * 60
SERVERS_PER_PAGE = 10
DAILY_REPORT_CHECK_SECONDS = 15 * 60
DAILY_REPORT_DEFAULT_UTC_HOUR = 0
DAILY_REPORT_DEFAULT_UTC_MINUTE = 5
DAILY_REPORT_METADATA_KEY = "last_daily_owner_report_utc_date"


@dataclass(frozen=True)
class GuildRecord:
    guild_id: int
    guild_name: str
    owner_id: int
    member_count: int
    channel_count: int
    joined_at: int
    last_seen_at: int
    left_at: int | None
    active: bool
    usage_count: int
    last_used_at: int | None
    inviter_id: int = 0
    inviter_name: str = ""
    inviter_checked_at: int = 0


@dataclass(frozen=True)
class GuildBanRecord:
    guild_id: int
    guild_name: str
    banned_at: int
    banned_by: int
    reason: str


def compact_command(content: str) -> str:
    return re.sub(r"\s+", "", content or "").lower()


def classify_message_usage(
    content: str,
    helper_prefix: str = HELPER_PREFIX_DEFAULT,
) -> str | None:
    content = canonicalize_helper_command(content, helper_prefix)
    first_line = next(
        (line.strip() for line in (content or "").splitlines() if line.strip()),
        "",
    )
    if re.match(r"^h\s*help\b", first_line, re.IGNORECASE):
        return "help_views"
    if re.match(r"^hbt(?:\s|$)", first_line, re.IGNORECASE):
        return "ticket_lookups"

    compact = compact_command(content)
    if compact in {"owobossi", "wbossi"}:
        return "boss_generator_requests"
    if compact in {
        "owobosst",
        "owobossticket",
        "owobosstickets",
        "wbosst",
        "wbossticket",
        "wbosstickets",
    }:
        return "ticket_checks"
    if compact in {"hbosscd", "hbosscooldown"}:
        return "cooldown_checks"
    if compact in {"hbosst", "hbosslist", "hbl"}:
        return "ticket_list_views"
    if compact in {"hbosssettings", "hbs"}:
        return "ticket_management"
    if compact == "habout":
        return "about_views"
    if compact in {"hxp", "hexp", "hreminder", "hreminders", "hxpreminder", "hxpreminders"}:
        return "xp_tools"
    if compact.startswith(("ht", "htm", "hteam")):
        return "team_helper_commands"
    return None


def safe_epoch(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def human_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def human_bytes(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def query_database(path: Path, query: str) -> int:
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(path, timeout=5) as connection:
            row = connection.execute(query).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except (sqlite3.Error, OSError):
        return 0


def guild_snapshot(guild: discord.Guild) -> tuple[int, str, int, int, int, int]:
    now = int(time.time())
    joined_at = now
    me = guild.me
    if me is not None and me.joined_at is not None:
        joined_at = int(me.joined_at.timestamp())
    return (
        guild.id,
        guild.name[:200],
        int(guild.owner_id or 0),
        int(guild.member_count or 0),
        len(guild.channels),
        joined_at,
    )


class StatsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    async def initialize(self) -> None:
        async with self.lock:
            await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_registry (
                    guild_id INTEGER PRIMARY KEY,
                    guild_name TEXT NOT NULL,
                    owner_id INTEGER NOT NULL DEFAULT 0,
                    member_count INTEGER NOT NULL DEFAULT 0,
                    channel_count INTEGER NOT NULL DEFAULT 0,
                    joined_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    left_at INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at INTEGER,
                    inviter_id INTEGER NOT NULL DEFAULT 0,
                    inviter_name TEXT NOT NULL DEFAULT '',
                    inviter_checked_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            for _column_name, ddl in (
                ("inviter_id", "ALTER TABLE guild_registry ADD COLUMN inviter_id INTEGER NOT NULL DEFAULT 0"),
                ("inviter_name", "ALTER TABLE guild_registry ADD COLUMN inviter_name TEXT NOT NULL DEFAULT ''"),
                ("inviter_checked_at", "ALTER TABLE guild_registry ADD COLUMN inviter_checked_at INTEGER NOT NULL DEFAULT 0"),
            ):
                try:
                    connection.execute(ddl)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).casefold():
                        raise
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_usage_totals (
                    guild_id INTEGER NOT NULL,
                    metric TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    last_used_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, metric)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_denylist (
                    guild_id INTEGER PRIMARY KEY,
                    guild_name TEXT NOT NULL DEFAULT '',
                    banned_at INTEGER NOT NULL,
                    banned_by INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_totals (
                    metric TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0,
                    last_used_at INTEGER NOT NULL
                )
                """
            )

    async def sync_guilds(self, guilds: Iterable[discord.Guild]) -> None:
        snapshots = [guild_snapshot(guild) for guild in guilds]
        async with self.lock:
            await asyncio.to_thread(self._sync_guilds_sync, snapshots)

    def _sync_guilds_sync(
        self, snapshots: list[tuple[int, str, int, int, int, int]]
    ) -> None:
        now = int(time.time())
        current_ids = {snapshot[0] for snapshot in snapshots}
        with self._connect() as connection:
            for guild_id, name, owner_id, members, channels, joined_at in snapshots:
                connection.execute(
                    """
                    INSERT INTO guild_registry (
                        guild_id, guild_name, owner_id, member_count, channel_count,
                        joined_at, last_seen_at, left_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 1)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        guild_name = excluded.guild_name,
                        owner_id = excluded.owner_id,
                        member_count = excluded.member_count,
                        channel_count = excluded.channel_count,
                        last_seen_at = excluded.last_seen_at,
                        left_at = NULL,
                        active = 1
                    """,
                    (guild_id, name, owner_id, members, channels, joined_at, now),
                )

            if current_ids:
                placeholders = ",".join("?" for _ in current_ids)
                connection.execute(
                    f"""
                    UPDATE guild_registry
                    SET active = 0, left_at = COALESCE(left_at, ?)
                    WHERE active = 1 AND guild_id NOT IN ({placeholders})
                    """,
                    (now, *current_ids),
                )
            else:
                connection.execute(
                    "UPDATE guild_registry SET active = 0, left_at = COALESCE(left_at, ?) WHERE active = 1",
                    (now,),
                )

    async def upsert_guild(self, guild: discord.Guild) -> None:
        await self.sync_guilds([guild])

    async def mark_left(self, guild: discord.Guild) -> None:
        now = int(time.time())
        async with self.lock:
            await asyncio.to_thread(self._mark_left_sync, guild.id, guild.name, now)

    def _mark_left_sync(self, guild_id: int, guild_name: str, now: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO guild_registry (
                    guild_id, guild_name, joined_at, last_seen_at, left_at, active
                ) VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(guild_id) DO UPDATE SET
                    guild_name = excluded.guild_name,
                    last_seen_at = excluded.last_seen_at,
                    left_at = excluded.left_at,
                    active = 0
                """,
                (guild_id, guild_name[:200], now, now, now),
            )

    async def ban_guild(
        self,
        guild_id: int,
        guild_name: str,
        banned_by: int,
        reason: str,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._ban_guild_sync,
                guild_id,
                guild_name,
                banned_by,
                reason,
            )

    def _ban_guild_sync(
        self,
        guild_id: int,
        guild_name: str,
        banned_by: int,
        reason: str,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO guild_denylist (
                    guild_id, guild_name, banned_at, banned_by, reason
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    guild_name = CASE
                        WHEN excluded.guild_name != '' THEN excluded.guild_name
                        ELSE guild_denylist.guild_name
                    END,
                    banned_at = excluded.banned_at,
                    banned_by = excluded.banned_by,
                    reason = excluded.reason
                """,
                (
                    guild_id,
                    guild_name[:200],
                    int(time.time()),
                    banned_by,
                    reason[:500],
                ),
            )

    async def get_guild_ban(self, guild_id: int) -> GuildBanRecord | None:
        async with self.lock:
            return await asyncio.to_thread(self._get_guild_ban_sync, guild_id)

    def _get_guild_ban_sync(self, guild_id: int) -> GuildBanRecord | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT guild_id, guild_name, banned_at, banned_by, reason
                FROM guild_denylist
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        if row is None:
            return None
        return GuildBanRecord(
            guild_id=int(row["guild_id"]),
            guild_name=str(row["guild_name"] or ""),
            banned_at=int(row["banned_at"]),
            banned_by=int(row["banned_by"]),
            reason=str(row["reason"] or ""),
        )

    async def list_guild_bans(self) -> list[GuildBanRecord]:
        async with self.lock:
            return await asyncio.to_thread(self._list_guild_bans_sync)

    def _list_guild_bans_sync(self) -> list[GuildBanRecord]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT guild_id, guild_name, banned_at, banned_by, reason
                FROM guild_denylist
                ORDER BY banned_at DESC, guild_id ASC
                """
            ).fetchall()
        return [
            GuildBanRecord(
                guild_id=int(row["guild_id"]),
                guild_name=str(row["guild_name"] or ""),
                banned_at=int(row["banned_at"]),
                banned_by=int(row["banned_by"]),
                reason=str(row["reason"] or ""),
            )
            for row in rows
        ]

    async def unban_guild(self, guild_id: int) -> bool:
        async with self.lock:
            return await asyncio.to_thread(self._unban_guild_sync, guild_id)

    def _unban_guild_sync(self, guild_id: int) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM guild_denylist WHERE guild_id = ?",
                (guild_id,),
            )
        return cursor.rowcount > 0

    async def note_banned_guild_name(self, guild_id: int, guild_name: str) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._note_banned_guild_name_sync,
                guild_id,
                guild_name,
            )

    def _note_banned_guild_name_sync(self, guild_id: int, guild_name: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE guild_denylist SET guild_name = ? WHERE guild_id = ?",
                (guild_name[:200], guild_id),
            )

    async def record_usage(self, guild_id: int, metric: str) -> None:
        now = int(time.time())
        async with self.lock:
            await asyncio.to_thread(self._record_usage_sync, guild_id, metric, now)

    def _record_usage_sync(self, guild_id: int, metric: str, now: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE guild_registry
                SET usage_count = usage_count + 1, last_used_at = ?
                WHERE guild_id = ?
                """,
                (now, guild_id),
            )
            connection.execute(
                """
                INSERT INTO usage_totals (metric, count, last_used_at)
                VALUES (?, 1, ?)
                ON CONFLICT(metric) DO UPDATE SET
                    count = usage_totals.count + 1,
                    last_used_at = excluded.last_used_at
                """,
                (metric, now),
            )
            connection.execute(
                """
                INSERT INTO guild_usage_totals (guild_id, metric, count, last_used_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(guild_id, metric) DO UPDATE SET
                    count = guild_usage_totals.count + 1,
                    last_used_at = excluded.last_used_at
                """,
                (guild_id, metric, now),
            )

    async def list_guilds(self) -> list[GuildRecord]:
        async with self.lock:
            return await asyncio.to_thread(self._list_guilds_sync)

    def _list_guilds_sync(self) -> list[GuildRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT guild_id, guild_name, owner_id, member_count, channel_count,
                       joined_at, last_seen_at, left_at, active, usage_count, last_used_at,
                       COALESCE(inviter_id, 0) AS inviter_id,
                       COALESCE(inviter_name, '') AS inviter_name,
                       COALESCE(inviter_checked_at, 0) AS inviter_checked_at
                FROM guild_registry
                ORDER BY active DESC, member_count DESC, guild_name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [
            GuildRecord(
                guild_id=int(row["guild_id"]),
                guild_name=str(row["guild_name"]),
                owner_id=int(row["owner_id"]),
                member_count=int(row["member_count"]),
                channel_count=int(row["channel_count"]),
                joined_at=int(row["joined_at"]),
                last_seen_at=int(row["last_seen_at"]),
                left_at=(int(row["left_at"]) if row["left_at"] is not None else None),
                active=bool(row["active"]),
                usage_count=int(row["usage_count"]),
                last_used_at=(
                    int(row["last_used_at"])
                    if row["last_used_at"] is not None
                    else None
                ),
                inviter_id=int(row["inviter_id"] or 0),
                inviter_name=str(row["inviter_name"] or ""),
                inviter_checked_at=int(row["inviter_checked_at"] or 0),
            )
            for row in rows
        ]

    async def usage_totals(self) -> list[tuple[str, int]]:
        async with self.lock:
            return await asyncio.to_thread(self._usage_totals_sync)

    def _usage_totals_sync(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT metric, count FROM usage_totals ORDER BY count DESC, metric ASC"
            ).fetchall()
        return [(str(row["metric"]), int(row["count"])) for row in rows]

    async def guild_usage_totals(self, guild_id: int) -> list[tuple[str, int, int]]:
        async with self.lock:
            return await asyncio.to_thread(self._guild_usage_totals_sync, guild_id)

    def _guild_usage_totals_sync(self, guild_id: int) -> list[tuple[str, int, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT metric, count, last_used_at
                FROM guild_usage_totals
                WHERE guild_id = ?
                ORDER BY count DESC, metric ASC
                """,
                (guild_id,),
            ).fetchall()
        return [
            (str(row["metric"]), int(row["count"]), int(row["last_used_at"]))
            for row in rows
        ]

    async def get_guild_record(self, guild_id: int) -> GuildRecord | None:
        async with self.lock:
            return await asyncio.to_thread(self._get_guild_record_sync, guild_id)

    def _get_guild_record_sync(self, guild_id: int) -> GuildRecord | None:
        matches = [record for record in self._list_guilds_sync() if record.guild_id == guild_id]
        return matches[0] if matches else None

    async def set_inviter(
        self, guild_id: int, inviter_id: int, inviter_name: str, checked_at: int
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._set_inviter_sync, guild_id, inviter_id, inviter_name, checked_at
            )

    def _set_inviter_sync(
        self, guild_id: int, inviter_id: int, inviter_name: str, checked_at: int
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE guild_registry
                SET inviter_id = ?, inviter_name = ?, inviter_checked_at = ?
                WHERE guild_id = ?
                """,
                (inviter_id, inviter_name[:200], checked_at, guild_id),
            )

    async def get_metadata(self, key: str) -> str:
        async with self.lock:
            return await asyncio.to_thread(self._get_metadata_sync, key)

    def _get_metadata_sync(self, key: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM bot_metadata WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row and row["value"] is not None else ""

    async def set_metadata(self, key: str, value: str) -> None:
        async with self.lock:
            await asyncio.to_thread(self._set_metadata_sync, key, value)

    def _set_metadata_sync(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bot_metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, int(time.time())),
            )


class AboutLinks(discord.ui.View):
    def __init__(self, github_url: str, support_url: str) -> None:
        super().__init__(timeout=120)
        if github_url.startswith("https://"):
            self.add_item(discord.ui.Button(label="Source code", url=github_url))
        if support_url.startswith("https://"):
            self.add_item(discord.ui.Button(label="Support server", url=support_url))


class ServerListView(discord.ui.View):
    def __init__(
        self,
        cog: "BotInfo",
        owner_id: int,
        records: list[GuildRecord],
        *,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.records = records
        self.page_count = max(1, (len(records) + SERVERS_PER_PAGE - 1) // SERVERS_PER_PAGE)
        self.page = max(0, min(page, self.page_count - 1))
        self._sync_button_state()

    def _sync_button_state(self) -> None:
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.page_count - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This server list is owner-only.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        target = max(0, self.page - 1)
        await interaction.response.edit_message(
            embed=self.cog.build_servers_embed(self.records, target),
            view=ServerListView(self.cog, self.owner_id, self.records, page=target),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        target = min(self.page_count - 1, self.page + 1)
        await interaction.response.edit_message(
            embed=self.cog.build_servers_embed(self.records, target),
            view=ServerListView(self.cog, self.owner_id, self.records, page=target),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.cog.store.sync_guilds(self.cog.bot.guilds)
        records = await self.cog.store.list_guilds()
        target = min(self.page, max(0, (len(records) + SERVERS_PER_PAGE - 1) // SERVERS_PER_PAGE - 1))
        await interaction.edit_original_response(
            embed=self.cog.build_servers_embed(records, target),
            view=ServerListView(self.cog, self.owner_id, records, page=target),
        )


class GuildLeaveConfirmationView(discord.ui.View):
    """Owner-bound confirmation for deliberately disconnecting from one guild."""

    def __init__(
        self,
        cog: "BotInfo",
        owner_id: int,
        guild: discord.Guild,
    ) -> None:
        super().__init__(timeout=90)
        self.cog = cog
        self.owner_id = owner_id
        self.guild = guild
        self.completed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the configured bot owner can confirm this action.",
            ephemeral=True,
        )
        return False

    def disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(
        label="Leave this server",
        emoji="🚪",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_leave(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.completed:
            await interaction.response.send_message(
                "This server removal has already been handled.",
                ephemeral=True,
            )
            return

        self.completed = True
        self.disable_buttons()
        await interaction.response.defer(ephemeral=True)
        try:
            await self.guild.leave()
            await self.cog.store.mark_left(self.guild)
        except discord.HTTPException as exc:
            self.completed = False
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = False
            logger.exception(
                "Owner-requested leave failed for guild %s (%s)",
                self.guild.name,
                self.guild.id,
            )
            await interaction.edit_original_response(
                content=f"❌ Discord did not let me leave `{self.guild.id}`: `{exc}`",
                embed=None,
                view=self,
            )
            return

        logger.warning(
            "Bot owner %s removed the bot from guild %s (%s)",
            interaction.user.id,
            self.guild.name,
            self.guild.id,
        )
        embed = discord.Embed(
            title="✅ Bot left the server",
            description=(
                f"The bot has left **{discord.utils.escape_markdown(self.guild.name)}** "
                f"(`{self.guild.id}`)."
            ),
            color=0x57F287,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Owner-requested removal • Recorded in bot statistics")
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except discord.HTTPException:
            logger.info(
                "Guild %s was left successfully, but the confirmation message could not be edited",
                self.guild.id,
            )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_leave(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.completed = True
        self.disable_buttons()
        await interaction.response.edit_message(
            content="Cancelled. The bot remains in the server.",
            embed=None,
            view=self,
        )
        self.stop()


class GuildBanConfirmationView(discord.ui.View):
    """Owner-bound confirmation for persistently denying one guild."""

    def __init__(
        self,
        cog: "BotInfo",
        owner_id: int,
        guild_id: int,
        guild_name: str,
        reason: str,
    ) -> None:
        super().__init__(timeout=90)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.reason = reason
        self.completed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the configured bot owner can confirm this action.",
            ephemeral=True,
        )
        return False

    def disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(
        label="Ban and remove",
        emoji="⛔",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_ban(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.completed:
            await interaction.response.send_message(
                "This server ban has already been handled.",
                ephemeral=True,
            )
            return

        self.completed = True
        self.disable_buttons()
        await interaction.response.defer(ephemeral=True)
        await self.cog.store.ban_guild(
            self.guild_id,
            self.guild_name,
            interaction.user.id,
            self.reason,
        )

        guild = self.cog.bot.get_guild(self.guild_id)
        leave_error = ""
        if guild is not None:
            try:
                await self.cog.remove_banned_guild(
                    guild,
                    announce_to_guild=True,
                    notify_owner=False,
                )
            except discord.HTTPException as exc:
                leave_error = str(exc)

        description = (
            f"**{discord.utils.escape_markdown(self.guild_name or 'Unknown server')}** "
            f"(`{self.guild_id}`) is now blocked from using the bot. "
            "Any future invite will be rejected automatically until you unban it."
        )
        if leave_error:
            description += (
                "\n\nThe denylist was saved, but Discord rejected the immediate leave "
                f"request: `{leave_error}`"
            )
        embed = discord.Embed(
            title="⛔ Server banned from the bot",
            description=description,
            color=(0xED4245 if leave_error else 0x57F287),
            timestamp=discord.utils.utcnow(),
        )
        if self.reason:
            embed.add_field(name="Private note", value=self.reason, inline=False)
        embed.set_footer(text="Only the configured bot owner can remove this restriction")
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except discord.HTTPException:
            logger.info(
                "Guild %s was banned successfully, but the confirmation message could not be edited",
                self.guild_id,
            )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_ban(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.completed = True
        self.disable_buttons()
        await interaction.response.edit_message(
            content="Cancelled. This server was not banned.",
            embed=None,
            view=self,
        )
        self.stop()


class BotInfo(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = StatsStore(DATABASE_FILE)
        self.started_monotonic = time.monotonic()
        self.periodic_task: asyncio.Task[None] | None = None
        self.daily_report_task: asyncio.Task[None] | None = None
        self.daily_report_utc_hour = max(0, min(safe_epoch(os.getenv("BOT_DAILY_REPORT_UTC_HOUR"), DAILY_REPORT_DEFAULT_UTC_HOUR), 23))
        self.daily_report_utc_minute = max(0, min(safe_epoch(os.getenv("BOT_DAILY_REPORT_UTC_MINUTE"), DAILY_REPORT_DEFAULT_UTC_MINUTE), 59))
        self.restored = False
        self.denylist_leave_ids: set[int] = set()
        self.owner_id = safe_epoch(os.getenv("BOT_OWNER_ID"), 0)
        self.developer_name = os.getenv(
            "BOT_DEVELOPER_NAME", DEFAULT_DEVELOPER_NAME
        ).strip() or DEFAULT_DEVELOPER_NAME
        self.github_url = os.getenv("BOT_GITHUB_URL", DEFAULT_GITHUB_URL).strip()
        self.support_url = os.getenv("BOT_SUPPORT_URL", "").strip()
        self.description = os.getenv("BOT_DESCRIPTION", DEFAULT_DESCRIPTION).strip()

    async def cog_load(self) -> None:
        await self.store.initialize()
        self.periodic_task = asyncio.create_task(self.periodic_sync())
        self.daily_report_task = asyncio.create_task(self.daily_owner_report_loop())
        if not self.owner_id:
            logger.warning(
                "BOT_OWNER_ID is not configured; owner statistics and join/leave DMs are disabled"
            )
        logger.info("Bot statistics storage ready at %s", DATABASE_FILE)

    async def cog_unload(self) -> None:
        if self.periodic_task is not None:
            self.periodic_task.cancel()
            try:
                await self.periodic_task
            except asyncio.CancelledError:
                pass
        if self.daily_report_task is not None:
            self.daily_report_task.cancel()
            try:
                await self.daily_report_task
            except asyncio.CancelledError:
                pass

    async def periodic_sync(self) -> None:
        try:
            while True:
                await asyncio.sleep(PERIODIC_SYNC_SECONDS)
                bans = await self.store.list_guild_bans()
                banned_ids = {record.guild_id for record in bans}
                allowed_guilds = [
                    guild for guild in self.bot.guilds if guild.id not in banned_ids
                ]
                await self.store.sync_guilds(allowed_guilds)
                logger.info(
                    "Refreshed persistent metadata for %s allowed guild(s)",
                    len(allowed_guilds),
                )
        except asyncio.CancelledError:
            return

    def is_owner(self, user_id: int) -> bool:
        return bool(self.owner_id and user_id == self.owner_id)

    async def reject_non_owner(self, interaction: discord.Interaction) -> bool:
        if self.is_owner(interaction.user.id):
            return False
        message = "This operational command is available only to the bot developer."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return True

    def build_about_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🐾 OwO Boss Helper",
            description=self.description,
            color=0x5865F2,
        )
        embed.add_field(
            name="What it helps with",
            value=(
                "• Ordered Neon boss commands with detected HP\n"
                "• Guild-boss timing, cooldowns, and opt-in reward/end DMs\n"
                "• Exact weapon-ID team templates with confirmed Smart replace\n"
                "• Per-server boss-ticket boards in channels or threads\n"
                "• Community RNG and the TapDeck Lite Android information card"
            ),
            inline=False,
        )
        embed.add_field(name="Developer", value=f"**{self.developer_name}**", inline=True)
        embed.add_field(name="Version", value=f"`{BOT_VERSION}`", inline=True)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(
            name="Get started",
            value="Use `/setup-guide` or this server's configured helper prefix.",
            inline=False,
        )
        embed.set_footer(
            text="Independent community project • Not affiliated with OwO Bot or NeonUtil"
        )
        return embed

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.restored:
            return
        self.restored = True
        bans = await self.store.list_guild_bans()
        bans_by_id = {record.guild_id: record for record in bans}
        for guild in list(self.bot.guilds):
            if guild.id not in bans_by_id:
                continue
            try:
                await self.remove_banned_guild(
                    guild,
                    announce_to_guild=True,
                    notify_owner=True,
                    ban=bans_by_id[guild.id],
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not leave denied guild %s (%s) during startup",
                    guild.name,
                    guild.id,
                )
        allowed_guilds = [
            guild for guild in self.bot.guilds if guild.id not in bans_by_id
        ]
        await self.store.sync_guilds(allowed_guilds)
        logger.info(
            "Recorded %s active allowed guild(s) in bot statistics",
            len(allowed_guilds),
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        ban = await self.store.get_guild_ban(guild.id)
        if ban is not None:
            try:
                await self.remove_banned_guild(
                    guild,
                    announce_to_guild=True,
                    notify_owner=True,
                    ban=ban,
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not reject denied guild %s (%s)",
                    guild.name,
                    guild.id,
                )
            return
        await self.store.upsert_guild(guild)
        logger.info(
            "Bot joined guild %s (%s) with approximately %s members",
            guild.name,
            guild.id,
            guild.member_count or 0,
        )
        await self.notify_owner_about_guild(guild, joined=True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.store.mark_left(guild)
        if guild.id in self.denylist_leave_ids:
            self.denylist_leave_ids.discard(guild.id)
            logger.info("Bot left denied guild %s (%s)", guild.name, guild.id)
            return
        logger.info("Bot left guild %s (%s)", guild.name, guild.id)
        await self.notify_owner_about_guild(guild, joined=False)

    async def send_banned_server_notice(self, guild: discord.Guild) -> bool:
        member = guild.me
        if member is None:
            return False
        channels: list[discord.TextChannel] = []
        if guild.system_channel is not None:
            channels.append(guild.system_channel)
        channels.extend(
            channel for channel in guild.text_channels if channel not in channels
        )
        message = (
            "This bot has been banned from this server by the bot administrator "
            "and will now leave. Contact the bot administrator to request access "
            "again or ask about the restriction."
        )
        if self.support_url.startswith("https://"):
            message += f"\n{self.support_url}"
        embed = discord.Embed(
            title="⛔ Bot access denied",
            description=message,
            color=0xED4245,
        )
        for channel in channels:
            permissions = channel.permissions_for(member)
            if not permissions.send_messages:
                continue
            try:
                if permissions.embed_links:
                    await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    await channel.send(
                        message,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                return True
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                continue
        return False

    async def remove_banned_guild(
        self,
        guild: discord.Guild,
        *,
        announce_to_guild: bool,
        notify_owner: bool,
        ban: GuildBanRecord | None = None,
    ) -> None:
        ban = ban or await self.store.get_guild_ban(guild.id)
        await self.store.note_banned_guild_name(guild.id, guild.name)
        notice_sent = False
        if announce_to_guild:
            notice_sent = await self.send_banned_server_notice(guild)
        self.denylist_leave_ids.add(guild.id)
        try:
            await guild.leave()
        except discord.HTTPException:
            self.denylist_leave_ids.discard(guild.id)
            raise
        await self.store.mark_left(guild)
        logger.warning(
            "Rejected denied guild %s (%s); server_notice=%s",
            guild.name,
            guild.id,
            notice_sent,
        )
        if notify_owner:
            await self.notify_owner_about_blocked_guild(guild, ban, notice_sent)

    async def notify_owner_about_blocked_guild(
        self,
        guild: discord.Guild,
        ban: GuildBanRecord | None,
        notice_sent: bool,
    ) -> None:
        if not self.owner_id:
            return
        try:
            owner = self.bot.get_user(self.owner_id) or await self.bot.fetch_user(
                self.owner_id
            )
            embed = discord.Embed(
                title="⛔ Blocked server invite rejected",
                description=(
                    f"The bot automatically left **{discord.utils.escape_markdown(guild.name)}** "
                    f"(`{guild.id}`) because that server is on your denylist."
                ),
                color=0xED4245,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Approximate members",
                value=str(guild.member_count or 0),
                inline=True,
            )
            embed.add_field(
                name="Server notice",
                value=("sent" if notice_sent else "could not send"),
                inline=True,
            )
            if ban is not None and ban.reason:
                embed.add_field(name="Private note", value=ban.reason, inline=False)
            embed.set_footer(text="Use /bot-unban-server to allow this server again")
            await owner.send(embed=embed)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.warning(
                "Could not DM the developer about rejected guild %s", guild.id
            )

    async def notify_owner_about_guild(
        self,
        guild: discord.Guild,
        *,
        joined: bool,
    ) -> None:
        if not self.owner_id:
            return
        try:
            owner = self.bot.get_user(self.owner_id) or await self.bot.fetch_user(
                self.owner_id
            )
            embed = discord.Embed(
                title=("✅ Bot added to a server" if joined else "➖ Bot removed from a server"),
                color=(0x57F287 if joined else 0xED4245),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Server",
                value=f"**{guild.name}**\n`{guild.id}`",
                inline=False,
            )
            embed.add_field(
                name="Approximate members",
                value=str(guild.member_count or 0),
                inline=True,
            )
            embed.add_field(
                name="Current active servers",
                value=str(len(self.bot.guilds)),
                inline=True,
            )
            await owner.send(embed=embed)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.warning("Could not DM the developer about guild %s", guild.id)

    async def detect_inviter_from_audit_log(
        self, guild: discord.Guild
    ) -> discord.abc.User | None:
        """Return the likely user who added the bot, when Discord exposes it."""
        bot_user = self.bot.user
        bot_member = guild.me
        if bot_user is None or bot_member is None:
            return None
        permissions = getattr(bot_member, "guild_permissions", None)
        if permissions is None or not bool(getattr(permissions, "view_audit_log", False)):
            return None
        try:
            async for entry in guild.audit_logs(
                limit=8, action=discord.AuditLogAction.bot_add
            ):
                target = getattr(entry, "target", None)
                if int(getattr(target, "id", 0) or 0) != bot_user.id:
                    continue
                created_at = getattr(entry, "created_at", None)
                if created_at is not None:
                    age = abs((discord.utils.utcnow() - created_at).total_seconds())
                    if age > 15 * 60:
                        continue
                return entry.user
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None

    async def user_label(self, user_id: int) -> str:
        if not user_id:
            return "unknown"
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                user = None
        if user is None:
            return f"<@{user_id}> (`{user_id}`)"
        display = discord.utils.escape_markdown(str(user))
        return f"**{display}** (`{user_id}`)"

    def daily_report_due_date(self) -> str:
        now = time.time()
        current = time.gmtime(now)
        target = calendar.timegm(
            (
                current.tm_year,
                current.tm_mon,
                current.tm_mday,
                self.daily_report_utc_hour,
                self.daily_report_utc_minute,
                0,
                0,
                0,
                0,
            )
        )
        if now < target:
            return ""
        return time.strftime("%Y-%m-%d", current)

    async def daily_owner_report_loop(self) -> None:
        try:
            await asyncio.sleep(30)
            while True:
                await self.send_daily_owner_report_if_due()
                await asyncio.sleep(DAILY_REPORT_CHECK_SECONDS)
        except asyncio.CancelledError:
            return

    async def send_daily_owner_report_if_due(self) -> None:
        if not self.owner_id:
            return
        report_date = self.daily_report_due_date()
        if not report_date:
            return
        last_sent = await self.store.get_metadata(DAILY_REPORT_METADATA_KEY)
        if last_sent == report_date:
            return
        await self.store.sync_guilds(self.bot.guilds)
        records = await self.store.list_guilds()
        metrics = await self.store.usage_totals()
        embed = self.build_daily_owner_report_embed(records, metrics, report_date)
        try:
            owner = self.bot.get_user(self.owner_id) or await self.bot.fetch_user(self.owner_id)
            await owner.send(embed=embed)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            logger.warning("Could not send daily owner report for %s: %s", report_date, exc)
            return
        await self.store.set_metadata(DAILY_REPORT_METADATA_KEY, report_date)
        logger.info("Sent daily owner report for %s", report_date)

    def build_daily_owner_report_embed(
        self, records: list[GuildRecord], metrics: list[tuple[str, int]], report_date: str
    ) -> discord.Embed:
        now = int(time.time())
        active_records = [record for record in records if record.active]
        inactive_records = [record for record in records if not record.active]
        new_records = [record for record in active_records if record.joined_at >= now - 86400]
        removed_records = [record for record in inactive_records if record.left_at and record.left_at >= now - 86400]
        no_use_records = [record for record in active_records if record.usage_count <= 0]
        low_use_records = [record for record in active_records if 0 < record.usage_count <= 2]
        stale_records = [
            record
            for record in active_records
            if record.usage_count <= 2 and record.joined_at <= now - 2 * 86400
        ]
        total_members = sum(record.member_count for record in active_records)
        total_channels = sum(record.channel_count for record in active_records)

        embed = discord.Embed(
            title="📬 OwO Boss Helper — Daily Owner Report",
            description=(
                f"Report for `{report_date}`. Scheduled around "
                f"`{self.daily_report_utc_hour:02d}:{self.daily_report_utc_minute:02d} UTC`."
            ),
            color=0x5865F2,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Reach",
            value=(
                f"**Active servers:** {len(active_records):,}\n"
                f"**Historical servers:** {len(records):,}\n"
                f"**Removed servers:** {len(inactive_records):,}\n"
                f"**Approx. members:** {total_members:,}\n"
                f"**Visible channels:** {total_channels:,}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Last 24 hours",
            value=(
                f"**New servers:** {len(new_records):,}\n"
                f"**Removed servers:** {len(removed_records):,}\n"
                f"**No tracked use:** {len(no_use_records):,}\n"
                f"**Low-use servers:** {len(low_use_records):,}"
            ),
            inline=True,
        )
        metric_labels = {
            "boss_generator_requests": "Boss generator",
            "ticket_checks": "Ticket checks",
            "team_helper_commands": "Team helper",
            "cooldown_checks": "Cooldown checks",
            "ticket_list_views": "Ticket-list views",
            "ticket_management": "Ticket management",
            "ticket_lookups": "Ticket lookups",
            "help_views": "Help views",
            "about_views": "About views",
            "xp_tools": "XP tools",
        }
        if metrics:
            metric_lines = []
            for metric, count in metrics[:8]:
                label = metric_labels.get(metric, metric.replace("slash_", "/").replace("_", " "))
                metric_lines.append(f"**{label}:** {count:,}")
            embed.add_field(name="Top global usage", value="\n".join(metric_lines), inline=False)

        top_servers = sorted(active_records, key=lambda item: item.usage_count, reverse=True)[:5]
        if top_servers:
            embed.add_field(
                name="Top servers by tracked use",
                value="\n".join(
                    f"**{discord.utils.escape_markdown(record.guild_name)}:** {record.usage_count:,} uses"
                    + (f" • <t:{record.last_used_at}:R>" if record.last_used_at else "")
                    for record in top_servers
                ),
                inline=False,
            )

        recent_servers = sorted(
            [record for record in active_records if record.last_used_at],
            key=lambda item: int(item.last_used_at or 0),
            reverse=True,
        )[:5]
        if recent_servers:
            embed.add_field(
                name="Recently active servers",
                value="\n".join(
                    f"**{discord.utils.escape_markdown(record.guild_name)}:** <t:{int(record.last_used_at or 0)}:R>"
                    for record in recent_servers
                ),
                inline=False,
            )

        if stale_records:
            embed.add_field(
                name="Needs review",
                value="\n".join(
                    f"`{record.guild_id}` • **{discord.utils.escape_markdown(record.guild_name)}** • "
                    f"{record.member_count:,} members • {record.usage_count:,} uses"
                    for record in stale_records[:5]
                ),
                inline=False,
            )

        embed.set_footer(text="Owner-only daily DM • Use /bot-servers and /bot-server for details.")
        return embed

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        helper_prefix = await get_guild_helper_prefix(message.guild.id)
        canonical = canonicalize_helper_command(message.content or "", helper_prefix)
        metric = classify_message_usage(message.content or "", helper_prefix)
        if metric is not None:
            await self.store.record_usage(message.guild.id, metric)
        if compact_command(canonical) in ABOUT_COMMANDS:
            await safe_reply(
                message,
                embed=self.build_about_embed(),
                view=AboutLinks(self.github_url, self.support_url),
                mention_author=False,
            )

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command,
    ) -> None:
        if interaction.guild_id is not None:
            await self.store.record_usage(
                interaction.guild_id, f"slash_{command.qualified_name.replace(' ', '_')}"
            )

    @app_commands.command(name="about", description="About OwO Boss Helper and its developer.")
    async def about(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=self.build_about_embed(),
            view=AboutLinks(self.github_url, self.support_url),
        )

    @app_commands.command(
        name="channel-diagnostics",
        description="Check the helper's effective permissions in this channel.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def channel_diagnostics(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or channel is None:
            await interaction.response.send_message(
                "This command only works inside a server channel.",
                ephemeral=True,
            )
            return

        bot_user = self.bot.user
        bot_member = guild.me
        if bot_member is None and bot_user is not None:
            bot_member = guild.get_member(bot_user.id)
        permissions_for = getattr(channel, "permissions_for", None)
        if bot_member is None or not callable(permissions_for):
            await interaction.response.send_message(
                "I could not resolve my effective permissions in this channel.",
                ephemeral=True,
            )
            return

        permissions = permissions_for(bot_member)
        is_thread = isinstance(channel, discord.Thread)
        checks: list[tuple[str, bool, bool]] = [
            ("View Channel", bool(permissions.view_channel), True),
            ("Send Messages", bool(permissions.send_messages), True),
            ("Read Message History", bool(permissions.read_message_history), True),
            ("Embed Links", bool(permissions.embed_links), True),
            ("Add Reactions", bool(permissions.add_reactions), False),
            (
                "Send Messages in Threads",
                bool(getattr(permissions, "send_messages_in_threads", False)),
                is_thread,
            ),
            ("Manage Messages", bool(permissions.manage_messages), False),
            ("Manage Nicknames", bool(permissions.manage_nicknames), False),
        ]
        lines: list[str] = []
        required_missing: list[str] = []
        for label, allowed, required in checks:
            marker = "✅" if allowed else "❌"
            suffix = " — required here" if required else " — optional"
            lines.append(f"{marker} **{label}**{suffix}")
            if required and not allowed:
                required_missing.append(label)

        parent = getattr(channel, "parent", None)
        details = [
            f"**Channel:** {getattr(channel, 'mention', f'`{channel.id}`')}",
            f"**Channel ID:** `{channel.id}`",
            f"**Type:** `{type(channel).__name__}`",
        ]
        if parent is not None:
            details.append(
                f"**Parent:** {getattr(parent, 'mention', getattr(parent, 'name', parent.id))} "
                f"(`{parent.id}`)"
            )
        if is_thread:
            details.append(f"**Archived:** `{bool(channel.archived)}`")
            details.append(f"**Locked:** `{bool(channel.locked)}`")

        reply_ready = not required_missing
        if reply_ready:
            conclusion = (
                "✅ The core text-command permissions look correct. If Discord rejects "
                "a message reply anyway, v0.10.4 falls back to a normal channel message "
                "and records the reply error in the bot log."
            )
            color = 0x57F287
        else:
            conclusion = (
                "❌ Prefix responses can fail here because these required permissions "
                "are missing: **" + ", ".join(required_missing) + "**."
            )
            color = 0xED4245

        embed = discord.Embed(
            title="🔎 Channel Diagnostics",
            description="\n".join(details),
            color=color,
        )
        embed.add_field(
            name="Effective permissions",
            value="\n".join(lines),
            inline=False,
        )
        embed.add_field(name="Result", value=conclusion, inline=False)
        embed.set_footer(
            text="Run this command inside the channel or thread you want to test."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(
            "Channel diagnostics requested by %s in guild %s channel %s; missing=%s",
            interaction.user.id,
            guild.id,
            channel.id,
            required_missing,
        )

    @app_commands.command(
        name="bot-stats",
        description="Developer-only operational statistics for the bot.",
    )
    async def developer_stats(self, interaction: discord.Interaction) -> None:
        if await self.reject_non_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await self.store.sync_guilds(self.bot.guilds)

        records = await self.store.list_guilds()
        metrics = await self.store.usage_totals()
        active_records = [record for record in records if record.active]
        inactive_records = [record for record in records if not record.active]
        total_members = sum(int(guild.member_count or 0) for guild in self.bot.guilds)
        total_channels = sum(len(guild.channels) for guild in self.bot.guilds)

        template_count = query_database(
            TEAM_DATABASE_FILE, "SELECT COUNT(*) FROM team_templates"
        )
        template_users = query_database(
            TEAM_DATABASE_FILE, "SELECT COUNT(DISTINCT user_id) FROM team_templates"
        )
        ticket_entries = query_database(
            TICKET_DATABASE_FILE, "SELECT COUNT(*) FROM ticket_status"
        )
        ticket_guilds = query_database(
            TICKET_DATABASE_FILE,
            "SELECT COUNT(*) FROM ticket_guild_config",
        )
        nickname_marker_guilds = query_database(
            TICKET_DATABASE_FILE,
            "SELECT COUNT(*) FROM ticket_nickname_config WHERE enabled = 1",
        )
        nickname_opt_outs = query_database(
            TICKET_DATABASE_FILE,
            "SELECT COUNT(*) FROM ticket_nickname_preferences WHERE enabled = 0",
        )

        metric_labels = {
            "boss_generator_requests": "Boss generator",
            "ticket_checks": "Ticket checks",
            "team_helper_commands": "Team helper",
            "cooldown_checks": "Cooldown checks",
            "ticket_list_views": "Ticket-list views",
            "ticket_management": "Ticket management",
            "ticket_lookups": "Ticket lookups",
            "help_views": "Help views",
            "about_views": "About views",
            "xp_tools": "XP tools",
        }
        metric_lines = []
        for metric, count in metrics[:10]:
            label = metric_labels.get(metric, metric.replace("slash_", "/").replace("_", " "))
            metric_lines.append(f"**{label}:** {count:,}")

        storage_size = sum(
            file_size(path)
            for path in (
                DATABASE_FILE,
                TEAM_DATABASE_FILE,
                TICKET_DATABASE_FILE,
                XP_DATABASE_FILE,
                LOG_FILE,
            )
        )

        embed = discord.Embed(
            title="📊 OwO Boss Helper — Developer Stats",
            color=0x5865F2,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Reach",
            value=(
                f"**Active servers:** {len(active_records):,}\n"
                f"**Historical servers:** {len(records):,}\n"
                f"**Removed servers:** {len(inactive_records):,}\n"
                f"**Approx. members:** {total_members:,}\n"
                f"**Visible channels:** {total_channels:,}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Saved data",
            value=(
                f"**Team templates:** {template_count:,}\n"
                f"**Template users:** {template_users:,}\n"
                f"**Ticket entries:** {ticket_entries:,}\n"
                f"**Ticket boards:** {ticket_guilds:,}\n"
                f"**Nickname markers:** {nickname_marker_guilds:,} server(s)\n"
                f"**Personal marker opt-outs:** {nickname_opt_outs:,}\n"
                f"**Local tracked size:** {human_bytes(storage_size)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Runtime",
            value=(
                f"**Version:** `{BOT_VERSION}`\n"
                f"**Uptime:** {human_duration(time.monotonic() - self.started_monotonic)}\n"
                f"**Latency:** {round(self.bot.latency * 1000)} ms\n"
                f"**Python:** {platform.python_version()}\n"
                f"**discord.py:** {discord.__version__}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Usage recorded since v0.8.0",
            value="\n".join(metric_lines) if metric_lines else "No tracked usage yet.",
            inline=False,
        )
        embed.set_footer(text="Owner-only • Stored locally in bot_stats.db")
        await interaction.followup.send(embed=embed, ephemeral=True)

    def build_servers_embed(self, records: list[GuildRecord], page: int) -> discord.Embed:
        page_count = max(1, (len(records) + SERVERS_PER_PAGE - 1) // SERVERS_PER_PAGE)
        selected_page = max(0, min(page, page_count - 1))
        start = selected_page * SERVERS_PER_PAGE
        selected = records[start:start + SERVERS_PER_PAGE]

        lines: list[str] = []
        for index, record in enumerate(selected, start=start + 1):
            status = "🟢" if record.active else "⚫"
            last_used = (
                f"<t:{record.last_used_at}:R>" if record.last_used_at else "no tracked use"
            )
            owner = f"<@{record.owner_id}> (`{record.owner_id}`)" if record.owner_id else "unknown"
            guild = self.bot.get_guild(record.guild_id)
            vanity = ""
            if guild is not None:
                code = getattr(guild, "vanity_url_code", None)
                if code:
                    vanity = f" • vanity: `discord.gg/{code}`"
            lines.append(
                f"{status} **{index}. {discord.utils.escape_markdown(record.guild_name)}**\n"
                f"`{record.guild_id}` • owner: {owner}\n"
                f"{record.member_count:,} members • {record.channel_count:,} channels • "
                f"{record.usage_count:,} uses • {last_used}{vanity}"
            )

        embed = discord.Embed(
            title=f"🌐 Bot Servers — Page {selected_page + 1}/{page_count}",
            description="\n\n".join(lines) if lines else "No server records yet.",
            color=0x5865F2,
        )
        embed.set_footer(
            text=(
                f"{sum(1 for item in records if item.active)} active • {len(records)} historical • "
                "Use /bot-server server_id:<id> for details."
            )
        )
        return embed

    async def build_server_detail_embed(self, record: GuildRecord) -> discord.Embed:
        guild = self.bot.get_guild(record.guild_id)
        owner = await self.user_label(record.owner_id)
        metrics = await self.store.guild_usage_totals(record.guild_id)

        status = "active" if record.active else "removed"
        embed = discord.Embed(
            title=f"🔎 Server Detail — {discord.utils.escape_markdown(record.guild_name)}",
            color=(0x57F287 if record.active else 0x747F8D),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Server",
            value=(
                f"**Status:** `{status}`\n"
                f"**ID:** `{record.guild_id}`\n"
                f"**Owner:** {owner}\n"
                f"**Members:** {record.member_count:,}\n"
                f"**Channels:** {record.channel_count:,}"
            ),
            inline=False,
        )

        last_used_text = f"<t:{record.last_used_at}:R>" if record.last_used_at else "no tracked use"
        usage_lines = [
            f"**Total tracked uses:** {record.usage_count:,}",
            f"**Last tracked use:** {last_used_text}",
            f"**Joined:** <t:{record.joined_at}:R>",
            f"**Last seen:** <t:{record.last_seen_at}:R>",
        ]
        if record.left_at:
            usage_lines.append(f"**Left:** <t:{record.left_at}:R>")
        embed.add_field(name="Activity", value="\n".join(usage_lines), inline=False)

        metric_labels = {
            "boss_generator_requests": "Boss generator",
            "ticket_checks": "Ticket checks",
            "team_helper_commands": "Team helper",
            "cooldown_checks": "Cooldown checks",
            "ticket_list_views": "Ticket-list views",
            "ticket_management": "Ticket management",
            "ticket_lookups": "Ticket lookups",
            "help_views": "Help views",
            "about_views": "About views",
            "xp_tools": "XP tools",
        }
        if metrics:
            metric_lines = []
            for metric, count, last_used_at in metrics[:10]:
                label = metric_labels.get(metric, metric.replace("slash_", "/").replace("_", " "))
                metric_lines.append(f"**{label}:** {count:,} • <t:{last_used_at}:R>")
            embed.add_field(name="Usage breakdown", value="\n".join(metric_lines), inline=False)
        else:
            embed.add_field(name="Usage breakdown", value="No per-command usage recorded yet.", inline=False)

        if guild is not None and guild.me is not None:
            permissions = guild.me.guild_permissions
            checks = [
                ("Manage Guild", bool(permissions.manage_guild)),
                ("Manage Messages", bool(permissions.manage_messages)),
                ("Add Reactions", bool(permissions.add_reactions)),
                ("Embed Links", bool(permissions.embed_links)),
                ("Read Message History", bool(permissions.read_message_history)),
                ("Send Messages", bool(permissions.send_messages)),
            ]
            embed.add_field(
                name="Current bot permissions",
                value="\n".join(("✅" if ok else "❌") + f" {label}" for label, ok in checks),
                inline=False,
            )
            if getattr(guild, "vanity_url_code", None):
                embed.add_field(
                    name="Public invite",
                    value=f"Vanity: `discord.gg/{guild.vanity_url_code}`",
                    inline=False,
                )
        else:
            embed.add_field(
                name="Live access",
                value="The bot is no longer in this server, so only stored history is available.",
                inline=False,
            )

        embed.set_footer(text="Owner-only • Use /bot-server server_id:<id> for this detail view.")
        return embed

    @app_commands.command(
        name="bot-daily-report-test",
        description="Developer-only: send the daily owner report now.",
    )
    async def developer_daily_report_test(self, interaction: discord.Interaction) -> None:
        if await self.reject_non_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await self.store.sync_guilds(self.bot.guilds)
        records = await self.store.list_guilds()
        metrics = await self.store.usage_totals()
        report_date = time.strftime("%Y-%m-%d", time.gmtime())
        embed = self.build_daily_owner_report_embed(records, metrics, report_date)
        try:
            owner = self.bot.get_user(self.owner_id) or await self.bot.fetch_user(self.owner_id)
            await owner.send(embed=embed)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            await interaction.followup.send(f"Could not DM the report: `{exc}`", ephemeral=True)
            return
        await interaction.followup.send("✅ Daily owner report test sent to your DM.", ephemeral=True)

    @app_commands.command(
        name="bot-server",
        description="Developer-only detailed view for one server.",
    )
    @app_commands.describe(server_id="Discord server ID from /bot-servers")
    async def developer_server_detail(
        self, interaction: discord.Interaction, server_id: str
    ) -> None:
        if await self.reject_non_owner(interaction):
            return
        cleaned = re.sub(r"[^0-9]", "", server_id or "")
        if not cleaned:
            await interaction.response.send_message(
                "Send a server ID from `/bot-servers`, for example `/bot-server server_id:123...`.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.store.sync_guilds(self.bot.guilds)
        record = await self.store.get_guild_record(int(cleaned))
        if record is None:
            await interaction.followup.send(
                f"I do not have a stored server record for `{cleaned}`.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=await self.build_server_detail_embed(record),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="bot-servers",
        description="Developer-only list of servers using the bot.",
    )
    @app_commands.describe(page="Page number")
    async def developer_servers(
        self,
        interaction: discord.Interaction,
        page: app_commands.Range[int, 1, 999] = 1,
    ) -> None:
        if await self.reject_non_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await self.store.sync_guilds(self.bot.guilds)
        records = await self.store.list_guilds()
        selected_page = max(0, min(page - 1, max(0, (len(records) + SERVERS_PER_PAGE - 1) // SERVERS_PER_PAGE - 1)))
        await interaction.followup.send(
            embed=self.build_servers_embed(records, selected_page),
            view=ServerListView(self, interaction.user.id, records, page=selected_page),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="bot-leave-server",
        description="Developer-only: make the bot leave one server.",
    )
    @app_commands.describe(server_id="Discord server ID from /bot-servers")
    async def developer_leave_server(
        self,
        interaction: discord.Interaction,
        server_id: str,
    ) -> None:
        if await self.reject_non_owner(interaction):
            return

        cleaned = (server_id or "").strip()
        if not cleaned.isdigit() or not 15 <= len(cleaned) <= 22:
            await interaction.response.send_message(
                "Enter the exact numeric server ID from `/bot-servers`.",
                ephemeral=True,
            )
            return

        guild = self.bot.get_guild(int(cleaned))
        if guild is None:
            record = await self.store.get_guild_record(int(cleaned))
            if record is not None and not record.active:
                message = (
                    f"The bot is already absent from **{discord.utils.escape_markdown(record.guild_name)}** "
                    f"(`{cleaned}`)."
                )
            else:
                message = f"The bot is not currently connected to a server with ID `{cleaned}`."
            await interaction.response.send_message(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        owner = await self.user_label(int(guild.owner_id or 0))
        embed = discord.Embed(
            title="⚠️ Confirm server removal",
            description=(
                "This immediately disconnects the bot from the server. Rejoining later "
                "requires someone in that server to invite it again."
            ),
            color=0xED4245,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Server",
            value=(
                f"**{discord.utils.escape_markdown(guild.name)}**\n"
                f"**ID:** `{guild.id}`\n"
                f"**Approx. members:** {int(guild.member_count or 0):,}\n"
                f"**Owner:** {owner}"
            ),
            inline=False,
        )
        if interaction.guild_id == guild.id:
            embed.add_field(
                name="Current location",
                value="You are running this inside the target server. The final confirmation may disappear after the bot leaves.",
                inline=False,
            )
        embed.set_footer(text="Owner-only • No action occurs until the red button is pressed")
        await interaction.response.send_message(
            embed=embed,
            view=GuildLeaveConfirmationView(self, interaction.user.id, guild),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="bot-ban-server",
        description="Developer-only: deny a server access to the bot.",
    )
    @app_commands.describe(
        server_id="Exact Discord server ID",
        reason="Optional private note visible only in owner tools",
    )
    async def developer_ban_server(
        self,
        interaction: discord.Interaction,
        server_id: str,
        reason: str | None = None,
    ) -> None:
        if await self.reject_non_owner(interaction):
            return

        cleaned = (server_id or "").strip()
        if not cleaned.isdigit() or not 15 <= len(cleaned) <= 22:
            await interaction.response.send_message(
                "Enter the exact numeric Discord server ID.",
                ephemeral=True,
            )
            return

        guild_id = int(cleaned)
        existing = await self.store.get_guild_ban(guild_id)
        if existing is not None:
            name = existing.guild_name or "Unknown server"
            await interaction.response.send_message(
                (
                    f"**{discord.utils.escape_markdown(name)}** (`{guild_id}`) is already "
                    "banned. Use `/bot-unban-server` if you want to allow it again."
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        guild = self.bot.get_guild(guild_id)
        record = await self.store.get_guild_record(guild_id)
        guild_name = (
            guild.name
            if guild is not None
            else (record.guild_name if record is not None else "Unknown server")
        )
        member_count = (
            int(guild.member_count or 0)
            if guild is not None
            else (record.member_count if record is not None else 0)
        )
        private_reason = (reason or "").strip()[:500]

        embed = discord.Embed(
            title="⛔ Confirm permanent server ban",
            description=(
                "The bot will leave this server now if connected. Every future invite "
                "from this server will receive an access-denied notice and the bot will "
                "leave automatically until you unban the server."
            ),
            color=0xED4245,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Server",
            value=(
                f"**{discord.utils.escape_markdown(guild_name)}**\n"
                f"**ID:** `{guild_id}`\n"
                f"**Approx. members:** {member_count:,}\n"
                f"**Currently connected:** {'yes' if guild is not None else 'no'}"
            ),
            inline=False,
        )
        if private_reason:
            embed.add_field(
                name="Private note",
                value=private_reason,
                inline=False,
            )
        embed.set_footer(
            text="Owner-only • The restriction remains until /bot-unban-server is used"
        )
        await interaction.response.send_message(
            embed=embed,
            view=GuildBanConfirmationView(
                self,
                interaction.user.id,
                guild_id,
                guild_name,
                private_reason,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="bot-unban-server",
        description="Developer-only: allow a denied server to invite the bot again.",
    )
    @app_commands.describe(server_id="Exact Discord server ID")
    async def developer_unban_server(
        self,
        interaction: discord.Interaction,
        server_id: str,
    ) -> None:
        if await self.reject_non_owner(interaction):
            return

        cleaned = (server_id or "").strip()
        if not cleaned.isdigit() or not 15 <= len(cleaned) <= 22:
            await interaction.response.send_message(
                "Enter the exact numeric Discord server ID.",
                ephemeral=True,
            )
            return
        guild_id = int(cleaned)
        ban = await self.store.get_guild_ban(guild_id)
        if ban is None:
            await interaction.response.send_message(
                f"Server `{guild_id}` is not on the denylist.",
                ephemeral=True,
            )
            return
        await self.store.unban_guild(guild_id)
        name = ban.guild_name or "Unknown server"
        await interaction.response.send_message(
            (
                f"✅ **{discord.utils.escape_markdown(name)}** (`{guild_id}`) is no longer "
                "banned. This only permits a future invite; it does not re-add the bot."
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        logger.warning(
            "Bot owner %s removed guild %s (%s) from the denylist",
            interaction.user.id,
            name,
            guild_id,
        )

    @app_commands.command(
        name="bot-banned-servers",
        description="Developer-only: list servers denied access to the bot.",
    )
    async def developer_banned_servers(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if await self.reject_non_owner(interaction):
            return

        bans = await self.store.list_guild_bans()
        lines = []
        for ban in bans[:20]:
            name = discord.utils.escape_markdown(ban.guild_name or "Unknown server")
            private_note = discord.utils.escape_markdown(ban.reason[:100])
            note = f" • {private_note}" if private_note else ""
            lines.append(
                f"⛔ **{name}**\n`{ban.guild_id}` • <t:{ban.banned_at}:R>{note}"
            )
        if len(bans) > 20:
            lines.append(f"…and {len(bans) - 20} more.")
        embed = discord.Embed(
            title="⛔ Banned servers",
            description=(
                "\n\n".join(lines)
                if lines
                else "No servers are currently denied access."
            ),
            color=0xED4245,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(
            text=f"{len(bans)} blocked • Only the configured bot owner can unban"
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotInfo(bot))
