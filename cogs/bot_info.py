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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from .message_utils import safe_reply

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "bot_stats.db"
TEAM_DATABASE_FILE = PROJECT_ROOT / "team_templates.db"
TICKET_DATABASE_FILE = PROJECT_ROOT / "boss_tickets.db"
LOG_FILE = PROJECT_ROOT / "logs" / "bot.log"

BOT_VERSION = "0.11.5-beta"
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


def compact_command(content: str) -> str:
    return re.sub(r"\s+", "", content or "").lower()


def classify_message_usage(content: str) -> str | None:
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
        with self._connect() as connection:
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

    async def usage_totals(self) -> list[tuple[str, intóß9¶‰žËkºwµçUÅÕ¥É•‘}µ¥ÍÍ¥¹œ¤€¬€ˆ¨¨¸ˆ4(€€€€€€€€€€€€¤4(€€€€€€€€€€€½±½È€ô€ÁáÐÈÐÔ4(4(€€€€€€€•µ‰•€ô‘¥Í½É¹µ‰• 4(€€€€€€€€€€€Ñ¥Ñ±”ô‹Â~R8¡…¹¹•°¥…¹½ÍÑ¥Ìˆ°4(€€€€€€€€€€€‘•ÍÉ¥ÁÑ¥½¸ô‰q¸ˆ¹©½¥¸¡‘•Ñ…¥±Ì¤°4(€€€€€€€€€€€½±½Èõ½±½È°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€¹…µ”ô‰™™•Ñ¥Ù”Á•Éµ¥ÍÍ¥½¹Ìˆ°4(€€€€€€€€€€€Ù…±Õ”ô‰q¸ˆ¹©½¥¸¡±¥¹•Ì¤°4(€€€€€€€€€€€¥¹±¥¹”õ…±Í”°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹…‘‘}™¥•±¡¹…µ”ô‰I•ÍÕ±Ðˆ°Ù…±Õ”õ½¹±ÕÍ¥½¸°¥¹±¥¹”õ…±Í”¤4(€€€€€€€•µ‰•¹Í•Ñ}™½½Ñ•È 4(€€€€€€€€€€€Ñ•áÐô‰IÕ¸Ñ¡¥Ì½µµ…¹¥¹Í¥‘”Ñ¡”¡…¹¹•°½ÈÑ¡É•…å½ÔÝ…¹ÐÑ¼Ñ•ÍÐ¸ˆ4(€€€€€€€€¤4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹É•ÍÁ½¹Í”¹Í•¹‘}µ•ÍÍ…”¡•µ‰•õ•µ‰•°•Á¡•µ•É…°õQÉÕ”¤4(€€€€€€€±½•È¹¥¹™¼ 4(€€€€€€€€€€€€‰¡…¹¹•°‘¥…¹½ÍÑ¥ÌÉ•ÅÕ•ÍÑ•‰ä€•Ì¥¸Õ¥±€•Ì¡…¹¹•°€•Ììµ¥ÍÍ¥¹œô•Ìˆ°4(€€€€€€€€€€€¥¹Ñ•É…Ñ¥½¸¹ÕÍ•È¹¥°4(€€€€€€€€€€€Õ¥±¹¥°4(€€€€€€€€€€€¡…¹¹•°¹¥°4(€€€€€€€€€€€É•ÅÕ¥É•‘}µ¥ÍÍ¥¹œ°4(€€€€€€€€¤4(4(€€€…ÁÁ}½µµ…¹‘Ì¹½µµ…¹ 4(€€€€€€€¹…µ”ô‰‰½ÐµÍÑ…ÑÌˆ°4(€€€€€€€‘•ÍÉ¥ÁÑ¥½¸ô‰•Ù•±½Á•Èµ½¹±ä½Á•É…Ñ¥½¹…°ÍÑ…Ñ¥ÍÑ¥Ì™½ÈÑ¡”‰½Ð¸ˆ°4(€€€€¤4(€€€…Íå¹Œ‘•˜‘•Ù•±½Á•É}ÍÑ…ÑÌ¡Í•±˜°¥¹Ñ•É…Ñ¥½¸è‘¥Í½É¹%¹Ñ•É…Ñ¥½¸¤€´ø9½¹”è4(€€€€€€€¥˜…Ý…¥ÐÍ•±˜¹É•©•Ñ}¹½¹}½Ý¹•È¡¥¹Ñ•É…Ñ¥½¸¤è4(€€€€€€€€€€€É•ÑÕÉ¸4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹É•ÍÁ½¹Í”¹‘•™•È¡•Á¡•µ•É…°õQÉÕ”¤4(€€€€€€€…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹Íå¹}Õ¥±‘Ì¡Í•±˜¹‰½Ð¹Õ¥±‘Ì¤4(4(€€€€€€€É•½É‘Ì€ô…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹±¥ÍÑ}Õ¥±‘Ì ¤4(€€€€€€€µ•ÑÉ¥Ì€ô…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹ÕÍ…•}Ñ½Ñ…±Ì ¤4(€€€€€€€…Ñ¥Ù•}É•½É‘Ì€ômÉ•½É™½ÈÉ•½É¥¸É•½É‘Ì¥˜É•½É¹…Ñ¥Ù•t4(€€€€€€€¥¹…Ñ¥Ù•}É•½É‘Ì€ômÉ•½É™½ÈÉ•½É¥¸É•½É‘Ì¥˜¹½ÐÉ•½É¹…Ñ¥Ù•t4(€€€€€€€Ñ½Ñ…±}µ•µ‰•ÉÌ€ôÍÕ´¡¥¹Ð¡Õ¥±¹µ•µ‰•É}½Õ¹Ð½È€À¤™½ÈÕ¥±¥¸Í•±˜¹‰½Ð¹Õ¥±‘Ì¤4(€€€€€€€Ñ½Ñ…±}¡…¹¹•±Ì€ôÍÕ´¡±•¸¡Õ¥±¹¡…¹¹•±Ì¤™½ÈÕ¥±¥¸Í•±˜¹‰½Ð¹Õ¥±‘Ì¤4(4(€€€€€€€Ñ•µÁ±…Ñ•}½Õ¹Ð€ôÅÕ•Éå}‘…Ñ…‰…Í” 4(€€€€€€€€€€€Q5}Q	M}%1°€‰M1P=U9P ¨¤I=4Ñ•…µ}Ñ•µÁ±…Ñ•Ìˆ4(€€€€€€€€¤4(€€€€€€€Ñ•µÁ±…Ñ•}ÕÍ•ÉÌ€ôÅÕ•Éå}‘…Ñ…‰…Í” 4(€€€€€€€€€€€Q5}Q	M}%1°€‰M1P=U9P¡%MQ%9PÕÍ•É}¥¤I=4Ñ•…µ}Ñ•µÁ±…Ñ•Ìˆ4(€€€€€€€€¤4(€€€€€€€Ñ¥­•Ñ}•¹ÑÉ¥•Ì€ôÅÕ•Éå}‘…Ñ…‰…Í” 4(€€€€€€€€€€€Q%-Q}Q	M}%1°€‰M1P=U9P ¨¤I=4Ñ¥­•Ñ}ÍÑ…ÑÕÌˆ4(€€€€€€€€¤4(€€€€€€€Ñ¥­•Ñ}Õ¥±‘Ì€ôÅÕ•Éå}‘…Ñ…‰…Í” 4(€€€€€€€€€€€Q%-Q}Q	M}%1°4(€€€€€€€€€€€€‰M1P=U9P ¨¤I=4Ñ¥­•Ñ}Õ¥±‘}½¹™¥œˆ°4(€€€€€€€€¤4(€€€€€€€¹¥­¹…µ•}µ…É­•É}Õ¥±‘Ì€ôÅÕ•Éå}‘…Ñ…‰…Í” 4(€€€€€€€€€€€Q%-Q}Q	M}%1°4(€€€€€€€€€€€€‰M1P=U9P ¨¤I=4Ñ¥­•Ñ}¹¥­¹…µ•}½¹™¥œ]!I•¹…‰±•€ô€Äˆ°4(€€€€€€€€¤4(€€€€€€€¹¥­¹…µ•}½ÁÑ}½ÕÑÌ€ôÅÕ•Éå}‘…Ñ…‰…Í” 4(€€€€€€€€€€€Q%-Q}Q	M}%1°4(€€€€€€€€€€€€‰M1P=U9P ¨¤I=4Ñ¥­•Ñ}¹¥­¹…µ•}ÁÉ•™•É•¹•Ì]!I•¹…‰±•€ô€Àˆ°4(€€€€€€€€¤4(4(€€€€€€€µ•ÑÉ¥}±…‰•±Ì€ôì4(€€€€€€€€€€€€‰‰½ÍÍ}•¹•É…Ñ½É}É•ÅÕ•ÍÑÌˆè€‰	½ÍÌ•¹•É…Ñ½Èˆ°4(€€€€€€€€€€€€‰Ñ¥­•Ñ}¡•­Ìˆè€‰Q¥­•Ð¡•­Ìˆ°4(€€€€€€€€€€€€‰Ñ•…µ}¡•±Á•É}½µµ…¹‘Ìˆè€‰Q•…´¡•±Á•Èˆ°4(€€€€€€€€€€€€‰½½±‘½Ý¹}¡•­Ìˆè€‰½½±‘½Ý¸¡•­Ìˆ°4(€€€€€€€€€€€€‰Ñ¥­•Ñ}±¥ÍÑ}Ù¥•ÝÌˆè€‰Q¥­•Ðµ±¥ÍÐÙ¥•ÝÌˆ°4(€€€€€€€€€€€€‰Ñ¥­•Ñ}µ…¹…•µ•¹Ðˆè€‰Q¥­•Ðµ…¹…•µ•¹Ðˆ°4(€€€€€€€€€€€€‰Ñ¥­•Ñ}±½½­ÕÁÌˆè€‰Q¥­•Ð±½½­ÕÁÌˆ°4(€€€€€€€€€€€€‰¡•±Á}Ù¥•ÝÌˆè€‰!•±ÀÙ¥•ÝÌˆ°4(€€€€€€€€€€€€‰…‰½ÕÑ}Ù¥•ÝÌˆè€‰‰½ÕÐÙ¥•ÝÌˆ°4(€€€€€€€ô4(€€€€€€€µ•ÑÉ¥}±¥¹•Ì€ômt4(€€€€€€€™½Èµ•ÑÉ¥Œ°½Õ¹Ð¥¸µ•ÑÉ¥ÍlèÄÁtè4(€€€€€€€€€€€±…‰•°€ôµ•ÑÉ¥}±…‰•±Ì¹•Ð¡µ•ÑÉ¥Œ°µ•ÑÉ¥Œ¹É•Á±…” ‰Í±…Í¡|ˆ°€ˆ¼ˆ¤¹É•Á±…” ‰|ˆ°€ˆ€ˆ¤¤4(€€€€€€€€€€€µ•ÑÉ¥}±¥¹•Ì¹…ÁÁ•¹¡˜ˆ¨©í±…‰•±ôè¨¨í½Õ¹Ðè±ôˆ¤4(4(€€€€€€€ÍÑ½É…•}Í¥é”€ôÍÕ´ 4(€€€€€€€€€€€™¥±•}Í¥é”¡Á…Ñ ¤4(€€€€€€€€€€€™½ÈÁ…Ñ ¥¸€ 4(€€€€€€€€€€€€€€€Q	M}%1°4(€€€€€€€€€€€€€€€Q5}Q	M}%1°4(€€€€€€€€€€€€€€€Q%-Q}Q	M}%1°4(€€€€€€€€€€€€€€€1=}%1°4(€€€€€€€€€€€€¤4(€€€€€€€€¤4(4(€€€€€€€•µ‰•€ô‘¥Í½É¹µ‰• 4(€€€€€€€€€€€Ñ¥Ñ±”ô‹Â~N(=Ý<	½ÍÌ!•±Á•ÈƒŠP•Ù•±½Á•ÈMÑ…ÑÌˆ°4(€€€€€€€€€€€½±½ÈôÁàÔàØÕÈ°4(€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀõ‘¥Í½É¹ÕÑ¥±Ì¹ÕÑ¹½Ü ¤°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€¹…µ”ô‰I•… ˆ°4(€€€€€€€€€€€Ù…±Õ”ô 4(€€€€€€€€€€€€€€€˜ˆ¨©Ñ¥Ù”Í•ÉÙ•ÉÌè¨¨í±•¸¡…Ñ¥Ù•}É•½É‘Ì¤è±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©!¥ÍÑ½É¥…°Í•ÉÙ•ÉÌè¨¨í±•¸¡É•½É‘Ì¤è±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©I•µ½Ù•Í•ÉÙ•ÉÌè¨¨í±•¸¡¥¹…Ñ¥Ù•}É•½É‘Ì¤è±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©ÁÁÉ½à¸µ•µ‰•ÉÌè¨¨íÑ½Ñ…±}µ•µ‰•ÉÌè±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©Y¥Í¥‰±”¡…¹¹•±Ìè¨¨íÑ½Ñ…±}¡…¹¹•±Ìè±ôˆ4(€€€€€€€€€€€€¤°4(€€€€€€€€€€€¥¹±¥¹”õQÉÕ”°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€¹…µ”ô‰M…Ù•‘…Ñ„ˆ°4(€€€€€€€€€€€Ù…±Õ”ô 4(€€€€€€€€€€€€€€€˜ˆ¨©Q•…´Ñ•µÁ±…Ñ•Ìè¨¨íÑ•µÁ±…Ñ•}½Õ¹Ðè±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©Q•µÁ±…Ñ”ÕÍ•ÉÌè¨¨íÑ•µÁ±…Ñ•}ÕÍ•ÉÌè±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©Q¥­•Ð•¹ÑÉ¥•Ìè¨¨íÑ¥­•Ñ}•¹ÑÉ¥•Ìè±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©Q¥­•Ð‰½…É‘Ìè¨¨íÑ¥­•Ñ}Õ¥±‘Ìè±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©9¥­¹…µ”µ…É­•ÉÌè¨¨í¹¥­¹…µ•}µ…É­•É}Õ¥±‘Ìè±ôÍ•ÉÙ•È¡Ì¥q¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©A•ÉÍ½¹…°µ…É­•È½ÁÐµ½ÕÑÌè¨¨í¹¥­¹…µ•}½ÁÑ}½ÕÑÌè±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©1½…°ÑÉ…­•Í¥é”è¨¨í¡Õµ…¹}‰åÑ•Ì¡ÍÑ½É…•}Í¥é”¥ôˆ4(€€€€€€€€€€€€¤°4(€€€€€€€€€€€¥¹±¥¹”õQÉÕ”°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€¹…µ”ô‰IÕ¹Ñ¥µ”ˆ°4(€€€€€€€€€€€Ù…±Õ”ô 4(€€€€€€€€€€€€€€€˜ˆ¨©Y•ÉÍ¥½¸è¨¨í	=Q}YIM%=9õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©UÁÑ¥µ”è¨¨í¡Õµ…¹}‘ÕÉ…Ñ¥½¸¡Ñ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€´Í•±˜¹ÍÑ…ÉÑ•‘}µ½¹½Ñ½¹¥Œ¥õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©1…Ñ•¹äè¨¨íÉ½Õ¹¡Í•±˜¹‰½Ð¹±…Ñ•¹ä€¨€ÄÀÀÀ¥ôµÍq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©AåÑ¡½¸è¨¨íÁ±…Ñ™½É´¹ÁåÑ¡½¹}Ù•ÉÍ¥½¸ ¥õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©‘¥Í½É¹Áäè¨¨í‘¥Í½É¹}}Ù•ÉÍ¥½¹}}ôˆ4(€€€€€€€€€€€€¤°4(€€€€€€€€€€€¥¹±¥¹”õQÉÕ”°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€¹…µ”ô‰UÍ…”É•½É‘•Í¥¹”ØÀ¸à¸Àˆ°4(€€€€€€€€€€€Ù…±Õ”ô‰q¸ˆ¹©½¥¸¡µ•ÑÉ¥}±¥¹•Ì¤¥˜µ•ÑÉ¥}±¥¹•Ì•±Í”€‰9¼ÑÉ…­•ÕÍ…”å•Ð¸ˆ°4(€€€€€€€€€€€¥¹±¥¹”õ…±Í”°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹Í•Ñ}™½½Ñ•È¡Ñ•áÐô‰=Ý¹•Èµ½¹±äƒŠˆMÑ½É•±½…±±ä¥¸‰½Ñ}ÍÑ…ÑÌ¹‘ˆˆ¤4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹™½±±½ÝÕÀ¹Í•¹¡•µ‰•õ•µ‰•°•Á¡•µ•É…°õQÉÕ”¤4(4(€€€‘•˜‰Õ¥±‘}Í•ÉÙ•ÉÍ}•µ‰•¡Í•±˜°É•½É‘Ìè±¥ÍÑmÕ¥±‘I•½É‘t°Á…”è¥¹Ð¤€´ø‘¥Í½É¹µ‰•è4(€€€€€€€Á…•}½Õ¹Ð€ôµ…à Ä°€¡±•¸¡É•½É‘Ì¤€¬MIYIM}AI}A€´€Ä¤€¼¼MIYIM}AI}A¤4(€€€€€€€Í•±•Ñ•‘}Á…”€ôµ…à À°µ¥¸¡Á…”°Á…•}½Õ¹Ð€´€Ä¤¤4(€€€€€€€ÍÑ…ÉÐ€ôÍ•±•Ñ•‘}Á…”€¨MIYIM}AI}A4(€€€€€€€Í•±•Ñ•€ôÉ•½É‘ÍmÍÑ…ÉÐéÍÑ…ÉÐ€¬MIYIM}AI}At4(4(€€€€€€€±¥¹•Ìè±¥ÍÑmÍÑÉt€ômt4(€€€€€€€™½È¥¹‘•à°É•½É¥¸•¹Õµ•É…Ñ”¡Í•±•Ñ•°ÍÑ…ÉÐõÍÑ…ÉÐ€¬€Ä¤è4(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€‹Â~~ˆˆ¥˜É•½É¹…Ñ¥Ù”•±Í”€‹Šj¬ˆ4(€€€€€€€€€€€±…ÍÑ}ÕÍ•€ô€ 4(€€€€€€€€€€€€€€€˜ˆñÐéíÉ•½É¹±…ÍÑ}ÕÍ•‘}…ÑôéHøˆ¥˜É•½É¹±…ÍÑ}ÕÍ•‘}…Ð•±Í”€‰¹¼ÑÉ…­•ÕÍ”ˆ4(€€€€€€€€€€€€¤4(€€€€€€€€€€€½Ý¹•È€ô˜ˆñíÉ•½É¹½Ý¹•É}¥‘ôø€¡íÉ•½É¹½Ý¹•É}¥‘õ€¤ˆ¥˜É•½É¹½Ý¹•É}¥•±Í”€‰Õ¹­¹½Ý¸ˆ4(€€€€€€€€€€€Õ¥±€ôÍ•±˜¹‰½Ð¹•Ñ}Õ¥±¡É•½É¹Õ¥±‘}¥¤4(€€€€€€€€€€€Ù…¹¥Ñä€ô€ˆˆ4(€€€€€€€€€€€¥˜Õ¥±¥Ì¹½Ð9½¹”è4(€€€€€€€€€€€€€€€½‘”€ô•Ñ…ÑÑÈ¡Õ¥±°€‰Ù…¹¥Ñå}ÕÉ±}½‘”ˆ°9½¹”¤4(€€€€€€€€€€€€€€€¥˜½‘”è4(€€€€€€€€€€€€€€€€€€€Ù…¹¥Ñä€ô˜ˆƒŠˆÙ…¹¥Ñäè‘¥Í½É¹œ½í½‘•õ€ˆ4(€€€€€€€€€€€±¥¹•Ì¹…ÁÁ•¹ 4(€€€€€€€€€€€€€€€˜‰íÍÑ…ÑÕÍô€¨©í¥¹‘•áô¸í‘¥Í½É¹ÕÑ¥±Ì¹•Í…Á•}µ…É­‘½Ý¸¡É•½É¹Õ¥±‘}¹…µ”¥ô¨©q¸ˆ4(€€€€€€€€€€€€€€€˜‰íÉ•½É¹Õ¥±‘}¥‘õ€ƒŠˆ½Ý¹•Èèí½Ý¹•Éõq¸ˆ4(€€€€€€€€€€€€€€€˜‰íÉ•½É¹µ•µ‰•É}½Õ¹Ðè±ôµ•µ‰•ÉÌƒŠˆíÉ•½É¹¡…¹¹•±}½Õ¹Ðè±ô¡…¹¹•±ÌƒŠˆ€ˆ4(€€€€€€€€€€€€€€€˜‰íÉ•½É¹ÕÍ…•}½Õ¹Ðè±ôÕÍ•ÌƒŠˆí±…ÍÑ}ÕÍ•‘õíÙ…¹¥Ñåôˆ4(€€€€€€€€€€€€¤4(4(€€€€€€€•µ‰•€ô‘¥Í½É¹µ‰• 4(€€€€€€€€€€€Ñ¥Ñ±”õ˜‹Â~2@	½ÐM•ÉÙ•ÉÌƒŠPA…”íÍ•±•Ñ•‘}Á…”€¬€Åô½íÁ…•}½Õ¹Ñôˆ°4(€€€€€€€€€€€‘•ÍÉ¥ÁÑ¥½¸ô‰q¹q¸ˆ¹©½¥¸¡±¥¹•Ì¤¥˜±¥¹•Ì•±Í”€‰9¼Í•ÉÙ•ÈÉ•½É‘Ìå•Ð¸ˆ°4(€€€€€€€€€€€½±½ÈôÁàÔàØÕÈ°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹Í•Ñ}™½½Ñ•È 4(€€€€€€€€€€€Ñ•áÐô 4(€€€€€€€€€€€€€€€˜‰íÍÕ´ Ä™½È¥Ñ•´¥¸É•½É‘Ì¥˜¥Ñ•´¹…Ñ¥Ù”¥ô…Ñ¥Ù”ƒŠˆí±•¸¡É•½É‘Ì¥ô¡¥ÍÑ½É¥…°ƒŠˆ€ˆ4(€€€€€€€€€€€€€€€€‰UÍ”€½‰½ÐµÍ•ÉÙ•ÈÍ•ÉÙ•É}¥èñ¥ø™½È‘•Ñ…¥±Ì¸ˆ4(€€€€€€€€€€€€¤4(€€€€€€€€¤4(€€€€€€€É•ÑÕÉ¸•µ‰•4(4(€€€…Íå¹Œ‘•˜‰Õ¥±‘}Í•ÉÙ•É}‘•Ñ…¥±}•µ‰•¡Í•±˜°É•½ÉèÕ¥±‘I•½É¤€´ø‘¥Í½É¹µ‰•è4(€€€€€€€Õ¥±€ôÍ•±˜¹‰½Ð¹•Ñ}Õ¥±¡É•½É¹Õ¥±‘}¥¤4(€€€€€€€½Ý¹•È€ô…Ý…¥ÐÍ•±˜¹ÕÍ•É}±…‰•°¡É•½É¹½Ý¹•É}¥¤4(€€€€€€€µ•ÑÉ¥Ì€ô…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹Õ¥±‘}ÕÍ…•}Ñ½Ñ…±Ì¡É•½É¹Õ¥±‘}¥¤4(4(€€€€€€€ÍÑ…ÑÕÌ€ô€‰…Ñ¥Ù”ˆ¥˜É•½É¹…Ñ¥Ù”•±Í”€‰É•µ½Ù•ˆ4(€€€€€€€•µ‰•€ô‘¥Í½É¹µ‰• 4(€€€€€€€€€€€Ñ¥Ñ±”õ˜‹Â~R8M•ÉÙ•È•Ñ…¥°ƒŠPí‘¥Í½É¹ÕÑ¥±Ì¹•Í…Á•}µ…É­‘½Ý¸¡É•½É¹Õ¥±‘}¹…µ”¥ôˆ°4(€€€€€€€€€€€½±½Èô ÁàÔÝÈàÜ¥˜É•½É¹…Ñ¥Ù”•±Í”€ÁàÜÐÝá¤°4(€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀõ‘¥Í½É¹ÕÑ¥±Ì¹ÕÑ¹½Ü ¤°4(€€€€€€€€¤4(€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€¹…µ”ô‰M•ÉÙ•Èˆ°4(€€€€€€€€€€€Ù…±Õ”ô 4(€€€€€€€€€€€€€€€˜ˆ¨©MÑ…ÑÕÌè¨¨íÍÑ…ÑÕÍõq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©%è¨¨íÉ•½É¹Õ¥±‘}¥‘õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©=Ý¹•Èè¨¨í½Ý¹•Éõq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©5•µ‰•ÉÌè¨¨íÉ•½É¹µ•µ‰•É}½Õ¹Ðè±õq¸ˆ4(€€€€€€€€€€€€€€€˜ˆ¨©¡…¹¹•±Ìè¨¨íÉ•½É¹¡…¹¹•±}½Õ¹Ðè±ôˆ4(€€€€€€€€€€€€¤°4(€€€€€€€€€€€¥¹±¥¹”õ…±Í”°4(€€€€€€€€¤4(4(€€€€€€€±…ÍÑ}ÕÍ•‘}Ñ•áÐ€ô˜ˆñÐéíÉ•½É¹±…ÍÑ}ÕÍ•‘}…ÑôéHøˆ¥˜É•½É¹±…ÍÑ}ÕÍ•‘}…Ð•±Í”€‰¹¼ÑÉ…­•ÕÍ”ˆ4(€€€€€€€ÕÍ…•}±¥¹•Ì€ôl4(€€€€€€€€€€€˜ˆ¨©Q½Ñ…°ÑÉ…­•ÕÍ•Ìè¨¨íÉ•½É¹ÕÍ…•}½Õ¹Ðè±ôˆ°4(€€€€€€€€€€€˜ˆ¨©1…ÍÐÑÉ…­•ÕÍ”è¨¨í±…ÍÑ}ÕÍ•‘}Ñ•áÑôˆ°4(€€€€€€€€€€€˜ˆ¨©)½¥¹•è¨¨€ñÐéíÉ•½É¹©½¥¹•‘}…ÑôéHøˆ°4(€€€€€€€€€€€˜ˆ¨©1…ÍÐÍ••¸è¨¨€ñÐéíÉ•½É¹±…ÍÑ}Í••¹}…ÑôéHøˆ°4(€€€€€€€t4(€€€€€€€¥˜É•½É¹±•™Ñ}…Ðè4(€€€€€€€€€€€ÕÍ…•}±¥¹•Ì¹…ÁÁ•¹¡˜ˆ¨©1•™Ðè¨¨€ñÐéíÉ•½É¹±•™Ñ}…ÑôéHøˆ¤4(€€€€€€€•µ‰•¹…‘‘}™¥•±¡¹…µ”ô‰Ñ¥Ù¥Ñäˆ°Ù…±Õ”ô‰q¸ˆ¹©½¥¸¡ÕÍ…•}±¥¹•Ì¤°¥¹±¥¹”õ…±Í”¤4(4(€€€€€€€µ•ÑÉ¥}±…‰•±Ì€ôì4(€€€€€€€€€€€€‰‰½ÍÍ}•¹•É…Ñ½É}É•ÅÕ•ÍÑÌˆè€‰	½ÍÌ•¹•É…Ñ½Èˆ°4(€€€€€€€€€€€€‰Ñ¥­•Ñ}¡•­Ìˆè€‰Q¥­•Ð¡•­Ìˆ°4(€€€€€€€€€€€€‰Ñ•…µ}¡•±Á•É}½µµ…¹‘Ìˆè€‰Q•…´¡•±Á•Èˆ°4(€€€€€€€€€€€€‰½½±‘½Ý¹}¡•­Ìˆè€‰½½±‘½Ý¸¡•­Ìˆ°4(€€€€€€€€€€€€‰Ñ¥­•Ñ}±¥ÍÑ}Ù¥•ÝÌˆè€‰Q¥­•Ðµ±¥ÍÐÙ¥•ÝÌˆ°4(€€€€€€€€€€€€‰Ñ¥­•Ñ}µ…¹…•µ•¹Ðˆè€‰Q¥­•Ðµ…¹…•µ•¹Ðˆ°4(€€€€€€€€€€€€‰Ñ¥­•Ñ}±½½­ÕÁÌˆè€‰Q¥­•Ð±½½­ÕÁÌˆ°4(€€€€€€€€€€€€‰¡•±Á}Ù¥•ÝÌˆè€‰!•±ÀÙ¥•ÝÌˆ°4(€€€€€€€€€€€€‰…‰½ÕÑ}Ù¥•ÝÌˆè€‰‰½ÕÐÙ¥•ÝÌˆ°4(€€€€€€€ô4(€€€€€€€¥˜µ•ÑÉ¥Ìè4(€€€€€€€€€€€µ•ÑÉ¥}±¥¹•Ì€ômt4(€€€€€€€€€€€™½Èµ•ÑÉ¥Œ°½Õ¹Ð°±…ÍÑ}ÕÍ•‘}…Ð¥¸µ•ÑÉ¥ÍlèÄÁtè4(€€€€€€€€€€€€€€€±…‰•°€ôµ•ÑÉ¥}±…‰•±Ì¹•Ð¡µ•ÑÉ¥Œ°µ•ÑÉ¥Œ¹É•Á±…” ‰Í±…Í¡|ˆ°€ˆ¼ˆ¤¹É•Á±…” ‰|ˆ°€ˆ€ˆ¤¤4(€€€€€€€€€€€€€€€µ•ÑÉ¥}±¥¹•Ì¹…ÁÁ•¹¡˜ˆ¨©í±…‰•±ôè¨¨í½Õ¹Ðè±ôƒŠˆ€ñÐéí±…ÍÑ}ÕÍ•‘}…ÑôéHøˆ¤4(€€€€€€€€€€€•µ‰•¹…‘‘}™¥•±¡¹…µ”ô‰UÍ…”‰É•…­‘½Ý¸ˆ°Ù…±Õ”ô‰q¸ˆ¹©½¥¸¡µ•ÑÉ¥}±¥¹•Ì¤°¥¹±¥¹”õ…±Í”¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€•µ‰•¹…‘‘}™¥•±¡¹…µ”ô‰UÍ…”‰É•…­‘½Ý¸ˆ°Ù…±Õ”ô‰9¼Á•Èµ½µµ…¹ÕÍ…”É•½É‘•å•Ð¸ˆ°¥¹±¥¹”õ…±Í”¤4(4(€€€€€€€¥˜Õ¥±¥Ì¹½Ð9½¹”…¹Õ¥±¹µ”¥Ì¹½Ð9½¹”è4(€€€€€€€€€€€Á•Éµ¥ÍÍ¥½¹Ì€ôÕ¥±¹µ”¹Õ¥±‘}Á•Éµ¥ÍÍ¥½¹Ì4(€€€€€€€€€€€¡•­Ì€ôl4(€€€€€€€€€€€€€€€€ ‰5…¹…”Õ¥±ˆ°‰½½°¡Á•Éµ¥ÍÍ¥½¹Ì¹µ…¹…•}Õ¥±¤¤°4(€€€€€€€€€€€€€€€€ ‰5…¹…”5•ÍÍ…•Ìˆ°‰½½°¡Á•Éµ¥ÍÍ¥½¹Ì¹µ…¹…•}µ•ÍÍ…•Ì¤¤°4(€€€€€€€€€€€€€€€€ ‰‘I•…Ñ¥½¹Ìˆ°‰½½°¡Á•Éµ¥ÍÍ¥½¹Ì¹…‘‘}É•…Ñ¥½¹Ì¤¤°4(€€€€€€€€€€€€€€€€ ‰µ‰•1¥¹­Ìˆ°‰½½°¡Á•Éµ¥ÍÍ¥½¹Ì¹•µ‰•‘}±¥¹­Ì¤¤°4(€€€€€€€€€€€€€€€€ ‰I•…5•ÍÍ…”!¥ÍÑ½Éäˆ°‰½½°¡Á•Éµ¥ÍÍ¥½¹Ì¹É•…‘}µ•ÍÍ…•}¡¥ÍÑ½Éä¤¤°4(€€€€€€€€€€€€€€€€ ‰M•¹5•ÍÍ…•Ìˆ°‰½½°¡Á•Éµ¥ÍÍ¥½¹Ì¹Í•¹‘}µ•ÍÍ…•Ì¤¤°4(€€€€€€€€€€€t4(€€€€€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€€€€€¹…µ”ô‰ÕÉÉ•¹Ð‰½ÐÁ•Éµ¥ÍÍ¥½¹Ìˆ°4(€€€€€€€€€€€€€€€Ù…±Õ”ô‰q¸ˆ¹©½¥¸  ‹Šrˆ¥˜½¬•±Í”€‹Šv0ˆ¤€¬˜ˆí±…‰•±ôˆ™½È±…‰•°°½¬¥¸¡•­Ì¤°4(€€€€€€€€€€€€€€€¥¹±¥¹”õ…±Í”°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€¥˜•Ñ…ÑÑÈ¡Õ¥±°€‰Ù…¹¥Ñå}ÕÉ±}½‘”ˆ°9½¹”¤è4(€€€€€€€€€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€€€€€€€€€¹…µ”ô‰AÕ‰±¥Œ¥¹Ù¥Ñ”ˆ°4(€€€€€€€€€€€€€€€€€€€Ù…±Õ”õ˜‰Y…¹¥Ñäè‘¥Í½É¹œ½íÕ¥±¹Ù…¹¥Ñå}ÕÉ±}½‘•õ€ˆ°4(€€€€€€€€€€€€€€€€€€€¥¹±¥¹”õ…±Í”°4(€€€€€€€€€€€€€€€€¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€•µ‰•¹…‘‘}™¥•± 4(€€€€€€€€€€€€€€€¹…µ”ô‰1¥Ù”…•ÍÌˆ°4(€€€€€€€€€€€€€€€Ù…±Õ”ô‰Q¡”‰½Ð¥Ì¹¼±½¹•È¥¸Ñ¡¥ÌÍ•ÉÙ•È°Í¼½¹±äÍÑ½É•¡¥ÍÑ½Éä¥Ì…Ù…¥±…‰±”¸ˆ°4(€€€€€€€€€€€€€€€¥¹±¥¹”õ…±Í”°4(€€€€€€€€€€€€¤4(4(€€€€€€€•µ‰•¹Í•Ñ}™½½Ñ•È¡Ñ•áÐô‰=Ý¹•Èµ½¹±äƒŠˆUÍ”€½‰½ÐµÍ•ÉÙ•ÈÍ•ÉÙ•É}¥èñ¥ø™½ÈÑ¡¥Ì‘•Ñ…¥°Ù¥•Ü¸ˆ¤4(€€€€€€€É•ÑÕÉ¸•µ‰•4(4(€€€…ÁÁ}½µµ…¹‘Ì¹½µµ…¹ 4(€€€€€€€¹…µ”ô‰‰½Ðµ‘…¥±äµÉ•Á½ÉÐµÑ•ÍÐˆ°4(€€€€€€€‘•ÍÉ¥ÁÑ¥½¸ô‰•Ù•±½Á•Èµ½¹±äèÍ•¹Ñ¡”‘…¥±ä½Ý¹•ÈÉ•Á½ÉÐ¹½Ü¸ˆ°4(€€€€¤4(€€€…Íå¹Œ‘•˜‘•Ù•±½Á•É}‘…¥±å}É•Á½ÉÑ}Ñ•ÍÐ¡Í•±˜°¥¹Ñ•É…Ñ¥½¸è‘¥Í½É¹%¹Ñ•É…Ñ¥½¸¤€´ø9½¹”è4(€€€€€€€¥˜…Ý…¥ÐÍ•±˜¹É•©•Ñ}¹½¹}½Ý¹•È¡¥¹Ñ•É…Ñ¥½¸¤è4(€€€€€€€€€€€É•ÑÕÉ¸4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹É•ÍÁ½¹Í”¹‘•™•È¡•Á¡•µ•É…°õQÉÕ”¤4(€€€€€€€…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹Íå¹}Õ¥±‘Ì¡Í•±˜¹‰½Ð¹Õ¥±‘Ì¤4(€€€€€€€É•½É‘Ì€ô…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹±¥ÍÑ}Õ¥±‘Ì ¤4(€€€€€€€µ•ÑÉ¥Ì€ô…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹ÕÍ…•}Ñ½Ñ…±Ì ¤4(€€€€€€€É•Á½ÉÑ}‘…Ñ”€ôÑ¥µ”¹ÍÑÉ™Ñ¥µ” ˆ•d´•´´•ˆ°Ñ¥µ”¹µÑ¥µ” ¤¤4(€€€€€€€•µ‰•€ôÍ•±˜¹‰Õ¥±‘}‘…¥±å}½Ý¹•É}É•Á½ÉÑ}•µ‰•¡É•½É‘Ì°µ•ÑÉ¥Ì°É•Á½ÉÑ}‘…Ñ”¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€½Ý¹•È€ôÍ•±˜¹‰½Ð¹•Ñ}ÕÍ•È¡Í•±˜¹½Ý¹•É}¥¤½È…Ý…¥ÐÍ•±˜¹‰½Ð¹™•Ñ¡}ÕÍ•È¡Í•±˜¹½Ý¹•É}¥¤4(€€€€€€€€€€€…Ý…¥Ð½Ý¹•È¹Í•¹¡•µ‰•õ•µ‰•¤4(€€€€€€€•á•ÁÐ€¡‘¥Í½É¹½É‰¥‘‘•¸°‘¥Í½É¹9½Ñ½Õ¹°‘¥Í½É¹!QQAá•ÁÑ¥½¸¤…Ì•áŒè4(€€€€€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹™½±±½ÝÕÀ¹Í•¹¡˜‰½Õ±¹½Ð4Ñ¡”É•Á½ÉÐèí•áõ€ˆ°•Á¡•µ•É…°õQÉÕ”¤4(€€€€€€€€€€€É•ÑÕÉ¸4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹™½±±½ÝÕÀ¹Í•¹ ‹Šr…¥±ä½Ý¹•ÈÉ•Á½ÉÐÑ•ÍÐÍ•¹ÐÑ¼å½ÕÈ4¸ˆ°•Á¡•µ•É…°õQÉÕ”¤4(4(€€€…ÁÁ}½µµ…¹‘Ì¹½µµ…¹ 4(€€€€€€€¹…µ”ô‰‰½ÐµÍ•ÉÙ•Èˆ°4(€€€€€€€‘•ÍÉ¥ÁÑ¥½¸ô‰•Ù•±½Á•Èµ½¹±ä‘•Ñ…¥±•Ù¥•Ü™½È½¹”Í•ÉÙ•È¸ˆ°4(€€€€¤4(€€€…ÁÁ}½µµ…¹‘Ì¹‘•ÍÉ¥‰”¡Í•ÉÙ•É}¥ô‰¥Í½ÉÍ•ÉÙ•È%™É½´€½‰½ÐµÍ•ÉÙ•ÉÌˆ¤4(€€€…Íå¹Œ‘•˜‘•Ù•±½Á•É}Í•ÉÙ•É}‘•Ñ…¥° 4(€€€€€€€Í•±˜°¥¹Ñ•É…Ñ¥½¸è‘¥Í½É¹%¹Ñ•É…Ñ¥½¸°Í•ÉÙ•É}¥èÍÑÈ4(€€€€¤€´ø9½¹”è4(€€€€€€€¥˜…Ý…¥ÐÍ•±˜¹É•©•Ñ}¹½¹}½Ý¹•È¡¥¹Ñ•É…Ñ¥½¸¤è4(€€€€€€€€€€€É•ÑÕÉ¸4(€€€€€€€±•…¹•€ôÉ”¹ÍÕˆ¡È‰mxÀ´åtˆ°€ˆˆ°Í•ÉÙ•É}¥½È€ˆˆ¤4(€€€€€€€¥˜¹½Ð±•…¹•è4(€€€€€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹É•ÍÁ½¹Í”¹Í•¹‘}µ•ÍÍ…” 4(€€€€€€€€€€€€€€€€‰M•¹„Í•ÉÙ•È%™É½´€½‰½ÐµÍ•ÉÙ•ÉÍ€°™½È•á…µÁ±”€½‰½ÐµÍ•ÉÙ•ÈÍ•ÉÙ•É}¥èÄÈÌ¸¸¹€¸ˆ°4(€€€€€€€€€€€€€€€•Á¡•µ•É…°õQÉÕ”°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€É•ÑÕÉ¸4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹É•ÍÁ½¹Í”¹‘•™•È¡•Á¡•µ•É…°õQÉÕ”¤4(€€€€€€€…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹Íå¹}Õ¥±‘Ì¡Í•±˜¹‰½Ð¹Õ¥±‘Ì¤4(€€€€€€€É•½É€ô…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹•Ñ}Õ¥±‘}É•½É¡¥¹Ð¡±•…¹•¤¤4(€€€€€€€¥˜É•½É¥Ì9½¹”è4(€€€€€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹™½±±½ÝÕÀ¹Í•¹ 4(€€€€€€€€€€€€€€€˜‰$‘¼¹½Ð¡…Ù”„ÍÑ½É•Í•ÉÙ•ÈÉ•½É™½Èí±•…¹•‘õ€¸ˆ°4(€€€€€€€€€€€€€€€•Á¡•µ•É…°õQÉÕ”°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€É•ÑÕÉ¸4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹™½±±½ÝÕÀ¹Í•¹ 4(€€€€€€€€€€€•µ‰•õ…Ý…¥ÐÍ•±˜¹‰Õ¥±‘}Í•ÉÙ•É}‘•Ñ…¥±}•µ‰•¡É•½É¤°4(€€€€€€€€€€€•Á¡•µ•É…°õQÉÕ”°4(€€€€€€€€€€€…±±½Ý•‘}µ•¹Ñ¥½¹Ìõ‘¥Í½É¹±±½Ý•‘5•¹Ñ¥½¹Ì¹¹½¹” ¤°4(€€€€€€€€¤4(4(€€€…ÁÁ}½µµ…¹‘Ì¹½µµ…¹ 4(€€€€€€€¹…µ”ô‰‰½ÐµÍ•ÉÙ•ÉÌˆ°4(€€€€€€€‘•ÍÉ¥ÁÑ¥½¸ô‰•Ù•±½Á•Èµ½¹±ä±¥ÍÐ½˜Í•ÉÙ•ÉÌÕÍ¥¹œÑ¡”‰½Ð¸ˆ°4(€€€€¤4(€€€…ÁÁ}½µµ…¹‘Ì¹‘•ÍÉ¥‰”¡Á…”ô‰A…”¹Õµ‰•Èˆ¤4(€€€…Íå¹Œ‘•˜‘•Ù•±½Á•É}Í•ÉÙ•ÉÌ 4(€€€€€€€Í•±˜°4(€€€€€€€¥¹Ñ•É…Ñ¥½¸è‘¥Í½É¹%¹Ñ•É…Ñ¥½¸°4(€€€€€€€Á…”è…ÁÁ}½µµ…¹‘Ì¹I…¹•m¥¹Ð°€Ä°€ääåt€ô€Ä°4(€€€€¤€´ø9½¹”è4(€€€€€€€¥˜…Ý…¥ÐÍ•±˜¹É•©•Ñ}¹½¹}½Ý¹•È¡¥¹Ñ•É…Ñ¥½¸¤è4(€€€€€€€€€€€É•ÑÕÉ¸4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹É•ÍÁ½¹Í”¹‘•™•È¡•Á¡•µ•É…°õQÉÕ”¤4(€€€€€€€…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹Íå¹}Õ¥±‘Ì¡Í•±˜¹‰½Ð¹Õ¥±‘Ì¤4(€€€€€€€É•½É‘Ì€ô…Ý…¥ÐÍ•±˜¹ÍÑ½É”¹±¥ÍÑ}Õ¥±‘Ì ¤4(€€€€€€€Í•±•Ñ•‘}Á…”€ôµ…à À°µ¥¸¡Á…”€´€Ä°µ…à À°€¡±•¸¡É•½É‘Ì¤€¬MIYIM}AI}A€´€Ä¤€¼¼MIYIM}AI}A€´€Ä¤¤¤4(€€€€€€€…Ý…¥Ð¥¹Ñ•É…Ñ¥½¸¹™½±±½ÝÕÀ¹Í•¹ 4(€€€€€€€€€€€•µ‰•õÍ•±˜¹‰Õ¥±‘}Í•ÉÙ•ÉÍ}•µ‰•¡É•½É‘Ì°Í•±•Ñ•‘}Á…”¤°4(€€€€€€€€€€€Ù¥•ÜõM•ÉÙ•É1¥ÍÑY¥•Ü¡Í•±˜°¥¹Ñ•É…Ñ¥½¸¹ÕÍ•È¹¥°É•½É‘Ì°Á…”õÍ•±•Ñ•‘}Á…”¤°4(€€€€€€€€€€€•Á¡•µ•É…°õQÉÕ”°4(€€€€€€€€€€€…±±½Ý•‘}µ•¹Ñ¥½¹Ìõ‘¥Í½É¹±±½Ý•‘5•¹Ñ¥½¹Ì¹¹½¹” ¤°4(€€€€€€€€¤4(4(4)…Íå¹Œ‘•˜Í•ÑÕÀ¡‰½Ðè½µµ…¹‘Ì¹	½Ð¤€´ø9½¹”è4(€€€…Ý…¥Ð‰½Ð¹…‘‘}½œ¡	½Ñ%¹™¼¡‰½Ð¤¤4(