"""Per-guild OwO boss-ticket board with Pacific-midnight resets.

The tracker only records a user's ticket count after that user explicitly runs an
OwO boss-ticket command in a server where the helper is present. It does not infer
usage from battles or from activity in other servers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from .ui_emojis import ensure_ui_emojis, ui_emoji_button, ui_emoji_text

logger = logging.getLogger(__name__)

OWO_BOT_ID = 408785106942164992
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "boss_tickets.db"
PACIFIC = ZoneInfo("America/Los_Angeles")
PENDING_REQUEST_SECONDS = 60
BOARD_PAGE_SIZE = 15
MANAGEMENT_PAGE_SIZE = 25
MAX_TICKETS = 3
NICKNAME_MAX_LENGTH = 32
NICKNAME_SYNC_DELAY_SECONDS = 0.75
IDENTITY_REFRESH_TTL_SECONDS = 10 * 60
IDENTITY_REFRESH_CONCURRENCY = 4
BOARD_REFRESH_DEBOUNCE_SECONDS = 0.65
BOARD_STATUS_CACHE_TTL_SECONDS = 15 * 60
RECENT_MEMBER_TTL_SECONDS = 15 * 60
REACTION_CONTROL_TTL_SECONDS = 6 * 60 * 60
STARTUP_QUEUE_DELAY_SECONDS = 0.20
NICKNAME_SHOW_EMOJI = "🏷️"
NICKNAME_HIDE_EMOJI = "🔕"
TICKET_NICKNAME_MARKERS = {
    3: "🎟🎟🎟",
    2: "🎟🎟▫",
    1: "🎟▫▫",
    0: "▫▫▫",
}
TICKET_NICKNAME_RE = re.compile(
    r"\s*·\s*(?:(?:🎟|▫)\ufe0f?){3}\s*$"
)

TICKET_COMMANDS = {
    "owobosst",
    "owobossticket",
    "owobosstickets",
    "wbosst",
    "wbossticket",
    "wbosstickets",
}

TICKET_LIST_COMMANDS = {
    "hbosslist",
    "hbosst",
    "hbl",
}

TICKET_SETTINGS_COMMANDS = {
    "hbosssettings",
    "hbs",
}

TICKET_NICKNAME_COMMANDS = {
    "hbossnickname",
    "hbn",
    "hticketnickname",
}

TICKET_COUNT_RE = re.compile(
    r"(?<!\d)([0-3])\s*/\s*3(?=[^\n]{0,80}\b(?:boss\s+)?tickets?\b)",
    re.IGNORECASE,
)
ZERO_TICKET_RE = re.compile(
    r"\b(?:you\s+)?(?:ran\s+out\s+of|have\s+no|do\s+not\s+have\s+any|don't\s+have\s+any)\s+(?:boss\s+)?tickets?\b",
    re.IGNORECASE,
)
TICKET_CAPTURE_RETRY_DELAYS = (0.20, 0.65, 1.35, 2.40, 3.75)
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
USER_ID_RE = re.compile(r"^(?:<@!?(\d{15,22})>|(\d{15,22}))$")


@dataclass(frozen=True)
class PendingTicketRequest:
    guild_id: int
    channel_id: int
    user_id: int
    command_message_id: int
    username: str
    account_username: str
    identity_tokens: tuple[str, ...]
    created_at: float


@dataclass(frozen=True)
class TicketReactionControl:
    guild_id: int
    channel_id: int
    message_id: int
    user_id: int
    emoji: str
    created_at: float


@dataclass(frozen=True)
class TicketStatus:
    guild_id: int
    user_id: int
    username: str
    account_username: str
    tickets: int
    updated_at: int
    cycle_date: str


@dataclass(frozen=True)
class BlockedTicketUser:
    guild_id: int
    user_id: int
    username: str
    blocked_at: int
    blocked_by: int


@dataclass(frozen=True)
class TicketManagementEntry:
    user_id: int
    username: str
    tickets: int | None
    updated_at: int | None
    blocked: bool


@dataclass(frozen=True)
class TicketNicknameState:
    guild_id: int
    user_id: int
    base_nickname: str | None
    last_applied_nickname: str
    updated_at: int


@dataclass
class NicknameSyncResult:
    updated: int = 0
    unchanged: int = 0
    restored: int = 0
    missing: int = 0
    skipped_owner: int = 0
    skipped_hierarchy: int = 0
    missing_permission: int = 0
    opted_out: int = 0
    failed: int = 0

    @property
    def total_processed(self) -> int:
        return (
            self.updated
            + self.unchanged
            + self.restored
            + self.missing
            + self.skipped_owner
            + self.skipped_hierarchy
            + self.missing_permission
            + self.opted_out
            + self.failed
        )


def strip_ticket_nickname_marker(value: str | None) -> str | None:
    if not value:
        return None
    stripped = TICKET_NICKNAME_RE.sub("", value).rstrip()
    return stripped or None


def build_ticket_nickname(base_name: str, tickets: int) -> str:
    marker = TICKET_NICKNAME_MARKERS[max(0, min(MAX_TICKETS, tickets))]
    suffix = f" · {marker}"
    allowed = max(1, NICKNAME_MAX_LENGTH - len(suffix))
    clean_base = (base_name or "Member").strip() or "Member"
    truncated = clean_base[:allowed].rstrip() or clean_base[:allowed]
    return f"{truncated}{suffix}"


def normalize_ticket_command(content: str) -> str:
    return re.sub(r"\s+", "", content or "").lower()


def is_ticket_command(content: str) -> bool:
    return normalize_ticket_command(content) in TICKET_COMMANDS


def is_ticket_list_command(content: str) -> bool:
    return normalize_ticket_command(content) in TICKET_LIST_COMMANDS


def is_ticket_settings_command(content: str) -> bool:
    return normalize_ticket_command(content) in TICKET_SETTINGS_COMMANDS


def is_ticket_nickname_command(content: str) -> bool:
    return normalize_ticket_command(content) in TICKET_NICKNAME_COMMANDS


def _walk_text(value: Any, chunks: list[str], seen: set[int]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            chunks.append(value)
        return
    if isinstance(value, (int, float, bool, bytes)):
        return

    object_id = id(value)
    if object_id in seen:
        return
    seen.add(object_id)

    if isinstance(value, dict):
        # Components V2 and application responses can place visible text under
        # fields that differ between message-create and message-edit payloads.
        # Walk every value instead of depending on a small key allow-list.
        for child in value.values():
            _walk_text(child, chunks, seen)
        return

    if isinstance(value, (list, tuple, set)):
        for child in value:
            _walk_text(child, chunks, seen)
        return

    for attribute in (
        "content",
        "title",
        "description",
        "label",
        "value",
        "name",
        "components",
        "children",
        "accessory",
    ):
        try:
            child = getattr(value, attribute, None)
        except Exception:
            continue
        if child is not None:
            _walk_text(child, chunks, seen)

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            _walk_text(to_dict(), chunks, seen)
        except Exception:
            pass


def extract_message_text(message: discord.Message) -> str:
    chunks: list[str] = []
    if message.content:
        chunks.append(message.content)
    system_content = getattr(message, "system_content", "")
    if system_content and system_content != message.content:
        chunks.append(system_content)
    for embed in message.embeds:
        if embed.title:
            chunks.append(embed.title)
        if embed.description:
            chunks.append(embed.description)
        if embed.author and embed.author.name:
            chunks.append(embed.author.name)
        for field in embed.fields:
            chunks.extend((field.name, field.value))
    _walk_text(message.components, chunks, set())
    return "\n".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())


def extract_raw_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    _walk_text(data, chunks, set())
    return "\n".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())


async def fetch_raw_message(
    bot: commands.Bot, channel_id: int, message_id: int
) -> dict[str, Any] | None:
    try:
        route = discord.http.Route(
            "GET",
            "/channels/{channel_id}/messages/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )
        data = await bot.http.request(route)
        return data if isinstance(data, dict) else None
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        logger.warning("Could not fetch OwO ticket response %s: %s", message_id, exc)
        return None


def normalize_ticket_response_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = ZERO_WIDTH_RE.sub("", normalized)
    normalized = normalized.replace("\\/", "/")
    for slash in ("⁄", "∕", "／", "⧸"):
        normalized = normalized.replace(slash, "/")
    # Discord markdown can split the visible count as **3**/**3**. Removing
    # formatting markers makes the parser work on what members actually see.
    normalized = re.sub(r"[*_`~|]", "", normalized)
    return re.sub(r"[\t\r\f\v ]+", " ", normalized)


def parse_ticket_count(text: str) -> int | None:
    normalized = normalize_ticket_response_text(text)

    # OwO does not display "0/3" when a member has no tickets. Its live response
    # is "you ran out of boss tickets", so treat that wording as an explicit zero.
    if ZERO_TICKET_RE.search(normalized):
        return 0

    match = TICKET_COUNT_RE.search(normalized)
    if match is None:
        # Last-resort bounded pattern for Components V2 payloads that split the
        # count across several text-display nodes. It still requires the word
        # "ticket" nearby, preventing unrelated 3/3 values from being accepted.
        fallback = re.search(
            r"(?<!\d)([0-3])\D{0,18}3(?=[^\n]{0,100}\b(?:boss\s+)?tickets?\b)",
            normalized,
            re.IGNORECASE,
        )
        if fallback is None:
            return None
        match = fallback
    value = int(match.group(1))
    return value if 0 <= value <= MAX_TICKETS else None


def current_pacific_date(now: datetime | None = None) -> date:
    current = now or datetime.now(tz=PACIFIC)
    return current.astimezone(PACIFIC).date()


def pacific_midnight_timestamp(day: date) -> int:
    local_midnight = datetime.combine(day, datetime_time.min, tzinfo=PACIFIC)
    return int(local_midnight.timestamp())


def next_pacific_reset_timestamp(now: datetime | None = None) -> int:
    current = (now or datetime.now(tz=PACIFIC)).astimezone(PACIFIC)
    next_day = current.date() + timedelta(days=1)
    return pacific_midnight_timestamp(next_day)


def identity_tokens_for_user(user: discord.abc.User) -> tuple[str, ...]:
    sources = (
        getattr(user, "display_name", ""),
        getattr(user, "global_name", ""),
        getattr(user, "name", ""),
    )
    tokens = {
        re.sub(r"[^a-z0-9_]", "", source.lower())
        for source in sources
        if source
    }
    return tuple(sorted((token for token in tokens if len(token) >= 2), key=len, reverse=True))


class TicketStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    async def initialize(self) -> None:
        async with self.lock:
            await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_ids_json TEXT NOT NULL DEFAULT '[]',
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_status (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    account_username TEXT NOT NULL DEFAULT '',
                    tickets INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    cycle_date TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            ticket_status_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(ticket_status)")
            }
            if "account_username" not in ticket_status_columns:
                connection.execute(
                    "ALTER TABLE ticket_status "
                    "ADD COLUMN account_username TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_status_guild "
                "ON ticket_status(guild_id, tickets DESC, username COLLATE NOCASE)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_blocked_users (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    blocked_at INTEGER NOT NULL,
                    blocked_by INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_blocked_guild "
                "ON ticket_blocked_users(guild_id, username COLLATE NOCASE)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_nickname_config (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    updated_by INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_nickname_state (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    base_nickname TEXT,
                    last_applied_nickname TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_nickname_state_guild "
                "ON ticket_nickname_state(guild_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_nickname_preferences (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_nickname_preferences_guild "
                "ON ticket_nickname_preferences(guild_id, enabled)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_tracking_preferences (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_tracking_preferences_guild "
                "ON ticket_tracking_preferences(guild_id, enabled)"
            )

    async def set_channel(self, guild_id: int, channel_id: int) -> None:
        async with self.lock:
            await asyncio.to_thread(self._set_channel_sync, guild_id, channel_id)

    def _set_channel_sync(self, guild_id: int, channel_id: int) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ticket_guild_config
                    (guild_id, channel_id, message_ids_json, updated_at)
                VALUES (?, ?, '[]', ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    message_ids_json = '[]',
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, now),
            )

    async def get_config(self, guild_id: int) -> tuple[int, list[int]] | None:
        async with self.lock:
            return await asyncio.to_thread(self._get_config_sync, guild_id)

    def _get_config_sync(self, guild_id: int) -> tuple[int, list[int]] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT channel_id, message_ids_json FROM ticket_guild_config WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            message_ids = [int(value) for value in json.loads(row["message_ids_json"])]
        except (TypeError, ValueError, json.JSONDecodeError):
            message_ids = []
        return int(row["channel_id"]), message_ids

    async def list_configured_guilds(self) -> list[int]:
        async with self.lock:
            return await asyncio.to_thread(self._list_configured_guilds_sync)

    def _list_configured_guilds_sync(self) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT guild_id FROM ticket_guild_config ORDER BY guild_id"
            ).fetchall()
        return [int(row["guild_id"]) for row in rows]

    async def set_board_message_ids(self, guild_id: int, message_ids: list[int]) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._set_board_message_ids_sync, guild_id, message_ids
            )

    def _set_board_message_ids_sync(self, guild_id: int, message_ids: list[int]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ticket_guild_config
                SET message_ids_json = ?, updated_at = ?
                WHERE guild_id = ?
                """,
                (json.dumps(message_ids), int(time.time()), guild_id),
            )

    async def upsert_status(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        account_username: str,
        tickets: int,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._upsert_status_sync,
                guild_id,
                user_id,
                username,
                account_username,
                tickets,
            )

    def _upsert_status_sync(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        account_username: str,
        tickets: int,
    ) -> None:
        now = int(time.time())
        cycle = current_pacific_date().isoformat()
        with self._connect() as connection:
            self._normalize_guild_cycle_sync(connection, guild_id, cycle)
            connection.execute(
                """
                INSERT INTO ticket_status
                    (
                        guild_id,
                        user_id,
                        username,
                        account_username,
                        tickets,
                        updated_at,
                        cycle_date
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    account_username = excluded.account_username,
                    tickets = excluded.tickets,
                    updated_at = excluded.updated_at,
                    cycle_date = excluded.cycle_date
                """,
                (
                    guild_id,
                    user_id,
                    username[:100],
                    account_username[:100],
                    tickets,
                    now,
                    cycle,
                ),
            )

    async def update_identity(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        account_username: str,
    ) -> bool:
        async with self.lock:
            return await asyncio.to_thread(
                self._update_identity_sync,
                guild_id,
                user_id,
                username,
                account_username,
            )

    def _update_identity_sync(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        account_username: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ticket_status
                SET username = ?, account_username = ?
                WHERE guild_id = ? AND user_id = ?
                  AND (username <> ? OR account_username <> ?)
                """,
                (
                    username[:100],
                    account_username[:100],
                    guild_id,
                    user_id,
                    username[:100],
                    account_username[:100],
                ),
            )
        return cursor.rowcount > 0

    async def normalize_guild_cycle(self, guild_id: int) -> bool:
        async with self.lock:
            return await asyncio.to_thread(self._normalize_guild_cycle_entry, guild_id)

    def _normalize_guild_cycle_entry(self, guild_id: int) -> bool:
        cycle = current_pacific_date().isoformat()
        with self._connect() as connection:
            return self._normalize_guild_cycle_sync(connection, guild_id, cycle)

    @staticmethod
    def _normalize_guild_cycle_sync(
        connection: sqlite3.Connection, guild_id: int, cycle: str
    ) -> bool:
        # Previously tracked members replenish to 3/3 at Pacific midnight. A later
        # ticket check from OwO replaces this reset value with the user's real count.
        reset_epoch = pacific_midnight_timestamp(date.fromisoformat(cycle))
        cursor = connection.execute(
            """
            UPDATE ticket_status
            SET tickets = 3, updated_at = ?, cycle_date = ?
            WHERE guild_id = ? AND cycle_date <> ?
            """,
            (reset_epoch, cycle, guild_id, cycle),
        )
        return cursor.rowcount > 0

    async def reset_all_for_current_cycle(self) -> list[int]:
        async with self.lock:
            return await asyncio.to_thread(self._reset_all_for_current_cycle_sync)

    def _reset_all_for_current_cycle_sync(self) -> list[int]:
        cycle = current_pacific_date().isoformat()
        reset_epoch = pacific_midnight_timestamp(date.fromisoformat(cycle))
        with self._connect() as connection:
            guild_rows = connection.execute(
                "SELECT DISTINCT guild_id FROM ticket_status WHERE cycle_date <> ?",
                (cycle,),
            ).fetchall()
            guild_ids = [int(row["guild_id"]) for row in guild_rows]
            connection.execute(
                """
                UPDATE ticket_status
                SET tickets = 3, updated_at = ?, cycle_date = ?
                WHERE cycle_date <> ?
                """,
                (reset_epoch, cycle, cycle),
            )
        return guild_ids

    async def remove_status(
        self, guild_id: int, user_id: int
    ) -> TicketStatus | None:
        async with self.lock:
            return await asyncio.to_thread(
                self._remove_status_sync, guild_id, user_id
            )

    def _remove_status_sync(
        self, guild_id: int, user_id: int
    ) -> TicketStatus | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT guild_id, user_id, username, account_username, tickets, updated_at, cycle_date
                FROM ticket_status
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "DELETE FROM ticket_status WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
        return TicketStatus(
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            account_username=str(row["account_username"] or ""),
            tickets=int(row["tickets"]),
            updated_at=int(row["updated_at"]),
            cycle_date=str(row["cycle_date"]),
        )

    async def block_user(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        blocked_by: int,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._block_user_sync,
                guild_id,
                user_id,
                username,
                blocked_by,
            )

    def _block_user_sync(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        blocked_by: int,
    ) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ticket_blocked_users
                    (guild_id, user_id, username, blocked_at, blocked_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    blocked_at = excluded.blocked_at,
                    blocked_by = excluded.blocked_by
                """,
                (guild_id, user_id, username[:100], now, blocked_by),
            )
            connection.execute(
                "DELETE FROM ticket_status WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )

    async def unblock_user(self, guild_id: int, user_id: int) -> bool:
        async with self.lock:
            return await asyncio.to_thread(
                self._unblock_user_sync, guild_id, user_id
            )

    def _unblock_user_sync(self, guild_id: int, user_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM ticket_blocked_users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
        return cursor.rowcount > 0

    async def list_blocked(self, guild_id: int) -> list[BlockedTicketUser]:
        async with self.lock:
            return await asyncio.to_thread(self._list_blocked_sync, guild_id)

    def _list_blocked_sync(self, guild_id: int) -> list[BlockedTicketUser]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT guild_id, user_id, username, blocked_at, blocked_by
                FROM ticket_blocked_users
                WHERE guild_id = ?
                ORDER BY username COLLATE NOCASE ASC, user_id ASC
                """,
                (guild_id,),
            ).fetchall()
        return [
            BlockedTicketUser(
                guild_id=int(row["guild_id"]),
                user_id=int(row["user_id"]),
                username=str(row["username"]),
                blocked_at=int(row["blocked_at"]),
                blocked_by=int(row["blocked_by"]),
            )
            for row in rows
        ]

    async def load_all_blocked_ids(self) -> dict[int, set[int]]:
        async with self.lock:
            return await asyncio.to_thread(self._load_all_blocked_ids_sync)

    def _load_all_blocked_ids_sync(self) -> dict[int, set[int]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT guild_id, user_id FROM ticket_blocked_users"
            ).fetchall()
        result: dict[int, set[int]] = {}
        for row in rows:
            result.setdefault(int(row["guild_id"]), set()).add(int(row["user_id"]))
        return result

    async def nickname_markers_enabled(self, guild_id: int) -> bool:
        async with self.lock:
            return await asyncio.to_thread(
                self._nickname_markers_enabled_sync, guild_id
            )

    def _nickname_markers_enabled_sync(self, guild_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM ticket_nickname_config WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        return bool(row and int(row["enabled"]))

    async def set_nickname_markers_enabled(
        self,
        guild_id: int,
        enabled: bool,
        updated_by: int,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._set_nickname_markers_enabled_sync,
                guild_id,
                enabled,
                updated_by,
            )

    def _set_nickname_markers_enabled_sync(
        self,
        guild_id: int,
        enabled: bool,
        updated_by: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ticket_nickname_config
                    (guild_id, enabled, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (guild_id, int(enabled), int(time.time()), updated_by),
            )

    async def save_nickname_state(
        self,
        guild_id: int,
        user_id: int,
        base_nickname: str | None,
        last_applied_nickname: str,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._save_nickname_state_sync,
                guild_id,
                user_id,
                base_nickname,
                last_applied_nickname,
            )

    def _save_nickname_state_sync(
        self,
        guild_id: int,
        user_id: int,
        base_nickname: str | None,
        last_applied_nickname: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ticket_nickname_state
                    (guild_id, user_id, base_nickname, last_applied_nickname, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    base_nickname = excluded.base_nickname,
                    last_applied_nickname = excluded.last_applied_nickname,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    user_id,
                    base_nickname,
                    last_applied_nickname,
                    int(time.time()),
                ),
            )

    async def get_nickname_state(
        self, guild_id: int, user_id: int
    ) -> TicketNicknameState | None:
        async with self.lock:
            return await asyncio.to_thread(
                self._get_nickname_state_sync, guild_id, user_id
            )

    def _get_nickname_state_sync(
        self, guild_id: int, user_id: int
    ) -> TicketNicknameState | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT guild_id, user_id, base_nickname, last_applied_nickname, updated_at
                FROM ticket_nickname_state
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return TicketNicknameState(
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            base_nickname=(
                str(row["base_nickname"])
                if row["base_nickname"] is not None
                else None
            ),
            last_applied_nickname=str(row["last_applied_nickname"]),
            updated_at=int(row["updated_at"]),
        )

    async def delete_nickname_state(self, guild_id: int, user_id: int) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._delete_nickname_state_sync, guild_id, user_id
            )

    def _delete_nickname_state_sync(self, guild_id: int, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM ticket_nickname_state WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )

    async def list_nickname_states(
        self, guild_id: int
    ) -> list[TicketNicknameState]:
        async with self.lock:
            return await asyncio.to_thread(
                self._list_nickname_states_sync, guild_id
            )

    def _list_nickname_states_sync(
        self, guild_id: int
    ) -> list[TicketNicknameState]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT guild_id, user_id, base_nickname, last_applied_nickname, updated_at
                FROM ticket_nickname_state
                WHERE guild_id = ?
                ORDER BY user_id
                """,
                (guild_id,),
            ).fetchall()
        return [
            TicketNicknameState(
                guild_id=int(row["guild_id"]),
                user_id=int(row["user_id"]),
                base_nickname=(
                    str(row["base_nickname"])
                    if row["base_nickname"] is not None
                    else None
                ),
                last_applied_nickname=str(row["last_applied_nickname"]),
                updated_at=int(row["updated_at"]),
            )
            for row in rows
        ]

    async def get_status(
        self, guild_id: int, user_id: int
    ) -> TicketStatus | None:
        async with self.lock:
            return await asyncio.to_thread(
                self._get_status_sync, guild_id, user_id
            )

    def _get_status_sync(
        self, guild_id: int, user_id: int
    ) -> TicketStatus | None:
        cycle = current_pacific_date().isoformat()
        with self._connect() as connection:
            self._normalize_guild_cycle_sync(connection, guild_id, cycle)
            row = connection.execute(
                """
                SELECT guild_id, user_id, username, account_username, tickets, updated_at, cycle_date
                FROM ticket_status
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return TicketStatus(
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            account_username=str(row["account_username"] or ""),
            tickets=int(row["tickets"]),
            updated_at=int(row["updated_at"]),
            cycle_date=str(row["cycle_date"]),
        )

    async def nickname_marker_allowed(self, guild_id: int, user_id: int) -> bool:
        async with self.lock:
            return await asyncio.to_thread(
                self._nickname_marker_allowed_sync, guild_id, user_id
            )

    def _nickname_marker_allowed_sync(self, guild_id: int, user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT enabled
                FROM ticket_nickname_preferences
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
        # Nickname changes are explicitly opt-in. No row means disabled.
        return bool(row and int(row["enabled"]))

    async def set_nickname_marker_allowed(
        self,
        guild_id: int,
        user_id: int,
        enabled: bool,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._set_nickname_marker_allowed_sync,
                guild_id,
                user_id,
                enabled,
            )

    def _set_nickname_marker_allowed_sync(
        self,
        guild_id: int,
        user_id: int,
        enabled: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ticket_nickname_preferences
                    (guild_id, user_id, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (guild_id, user_id, int(enabled), int(time.time())),
            )

    async def list_nickname_opt_in_ids(self, guild_id: int) -> set[int]:
        async with self.lock:
            return await asyncio.to_thread(
                self._list_nickname_opt_in_ids_sync, guild_id
            )

    def _list_nickname_opt_in_ids_sync(self, guild_id: int) -> set[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id
                FROM ticket_nickname_preferences
                WHERE guild_id = ? AND enabled = 1
                """,
                (guild_id,),
            ).fetchall()
        return {int(row["user_id"]) for row in rows}

    async def ticket_tracking_allowed(self, guild_id: int, user_id: int) -> bool:
        async with self.lock:
            return await asyncio.to_thread(
                self._ticket_tracking_allowed_sync, guild_id, user_id
            )

    def _ticket_tracking_allowed_sync(self, guild_id: int, user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT enabled
                FROM ticket_tracking_preferences
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
        return row is None or bool(int(row["enabled"]))

    async def set_ticket_tracking_allowed(
        self,
        guild_id: int,
        user_id: int,
        enabled: bool,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._set_ticket_tracking_allowed_sync,
                guild_id,
                user_id,
                enabled,
            )

    def _set_ticket_tracking_allowed_sync(
        self,
        guild_id: int,
        user_id: int,
        enabled: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ticket_tracking_preferences
                    (guild_id, user_id, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (guild_id, user_id, int(enabled), int(time.time())),
            )

    async def load_all_tracking_opt_outs(self) -> dict[int, set[int]]:
        async with self.lock:
            return await asyncio.to_thread(self._load_all_tracking_opt_outs_sync)

    def _load_all_tracking_opt_outs_sync(self) -> dict[int, set[int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT guild_id, user_id
                FROM ticket_tracking_preferences
                WHERE enabled = 0
                """
            ).fetchall()
        result: dict[int, set[int]] = {}
        for row in rows:
            result.setdefault(int(row["guild_id"]), set()).add(int(row["user_id"]))
        return result

    async def list_status(self, guild_id: int) -> list[TicketStatus]:
        async with self.lock:
            return await asyncio.to_thread(self._list_status_sync, guild_id)

    def _list_status_sync(self, guild_id: int) -> list[TicketStatus]:
        cycle = current_pacific_date().isoformat()
        with self._connect() as connection:
            self._normalize_guild_cycle_sync(connection, guild_id, cycle)
            rows = connection.execute(
                """
                SELECT guild_id, user_id, username, account_username, tickets, updated_at, cycle_date
                FROM ticket_status
                WHERE guild_id = ?
                ORDER BY tickets DESC, username COLLATE NOCASE ASC, user_id ASC
                """,
                (guild_id,),
            ).fetchall()
        return [
            TicketStatus(
                guild_id=int(row["guild_id"]),
                user_id=int(row["user_id"]),
                username=str(row["username"]),
                account_username=str(row["account_username"] or ""),
                tickets=int(row["tickets"]),
                updated_at=int(row["updated_at"]),
                cycle_date=str(row["cycle_date"]),
            )
            for row in rows
        ]


class TicketBoardView(discord.ui.View):
    """Persistent ticket-board navigation, display modes, and personal controls."""

    def __init__(
        self,
        tracker: "TicketTracker",
        *,
        page: int = 0,
        page_count: int = 2,
        mention_mode: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.tracker = tracker
        self.page = max(0, page)
        self.page_count = max(1, page_count)
        self.mention_mode = mention_mode

        if self.page_count > 1:
            previous = discord.ui.Button(
                label="Previous",
                emoji="◀️",
                style=discord.ButtonStyle.secondary,
                custom_id="owo-helper:ticket-board:previous",
                disabled=self.page <= 0,
            )
            next_button = discord.ui.Button(
                label="Next",
                emoji="▶️",
                style=discord.ButtonStyle.secondary,
                custom_id="owo-helper:ticket-board:next",
                disabled=self.page >= self.page_count - 1,
            )
            previous.callback = self.previous_page
            next_button.callback = self.next_page
            self.add_item(previous)
            self.add_item(next_button)

        display_mode = discord.ui.Button(
            label="Text view" if mention_mode else "Ping view",
            emoji="📝" if mention_mode else "📣",
            style=discord.ButtonStyle.secondary,
            custom_id="owo-helper:ticket-board:display-mode",
        )
        display_mode.callback = self.toggle_display_mode
        self.add_item(display_mode)

        settings = discord.ui.Button(
            label="My settings",
            emoji="⚙️",
            style=discord.ButtonStyle.primary,
            custom_id="owo-helper:ticket-board:my-nickname",
        )
        settings.callback = self.my_settings
        self.add_item(settings)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        logger.exception(
            "Ticket-board interaction failed for custom_id=%s in guild %s",
            getattr(item, "custom_id", None),
            interaction.guild_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        message = "I could not update this ticket board. Please try again in a moment."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    async def _render(
        self,
        interaction: discord.Interaction,
        *,
        page: int,
        mention_mode: bool,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This ticket board is no longer attached to a server.", ephemeral=True
            )
            return

        started = time.monotonic()
        await interaction.response.defer()

        # Page navigation never performs Discord member requests. The sticky-board
        # replacement populates this cache, while a post-restart first click needs only
        # one fast SQLite read.
        statuses = await self.tracker.get_board_statuses(interaction.guild_id)
        page_count = self.tracker.board_page_count(statuses)
        page = max(0, min(page, page_count - 1))

        await interaction.edit_original_response(
            embed=self.tracker.build_board_embed(
                statuses,
                page,
                mention_mode=mention_mode,
            ),
            view=TicketBoardView(
                self.tracker,
                page=page,
                page_count=page_count,
                mention_mode=mention_mode,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        logger.info(
            "Rendered cached ticket-board page for guild %s in %.3fs",
            interaction.guild_id,
            time.monotonic() - started,
        )

    async def _move(self, interaction: discord.Interaction, offset: int) -> None:
        current = self.tracker.page_from_message(interaction.message)
        mention_mode = self.tracker.board_mention_mode_from_message(
            interaction.message
        )
        await self._render(
            interaction,
            page=current + offset,
            mention_mode=mention_mode,
        )

    async def previous_page(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, -1)

    async def next_page(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, 1)

    async def toggle_display_mode(self, interaction: discord.Interaction) -> None:
        current = self.tracker.page_from_message(interaction.message)
        mention_mode = self.tracker.board_mention_mode_from_message(
            interaction.message
        )
        await self._render(
            interaction,
            page=current,
            mention_mode=not mention_mode,
        )

    async def my_settings(self, interaction: discord.Interaction) -> None:
        await self.tracker.send_personal_nickname_panel(interaction)


class TicketNicknamePreferenceView(discord.ui.View):
    """Private controls for a member's marker, board entry, and tracking consent."""

    def __init__(
        self,
        tracker: "TicketTracker",
        guild_id: int,
        user_id: int,
        *,
        server_enabled: bool,
        user_enabled: bool,
        tracking_enabled: bool,
        has_status: bool,
        admin_blocked: bool,
        notice: str | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.tracker = tracker
        self.guild_id = guild_id
        self.user_id = user_id
        self.server_enabled = server_enabled
        self.user_enabled = user_enabled
        self.tracking_enabled = tracking_enabled
        self.has_status = has_status
        self.admin_blocked = admin_blocked
        self.notice = notice

        show = discord.ui.Button(
            label="Enable my marker",
            emoji="🏷️",
            style=discord.ButtonStyle.success,
            disabled=(
                not server_enabled
                or user_enabled
                or not tracking_enabled
                or admin_blocked
            ),
            row=0,
        )
        hide = discord.ui.Button(
            label="Disable my marker",
            emoji="🔕",
            style=discord.ButtonStyle.secondary,
            disabled=not server_enabled or not user_enabled,
            row=0,
        )
        refresh = discord.ui.Button(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        remove_entry = discord.ui.Button(
            label="Remove me from list",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            disabled=not has_status,
            row=1,
        )
        tracking = discord.ui.Button(
            label="Resume tracking" if not tracking_enabled else "Stop tracking me",
            emoji="▶️" if not tracking_enabled else "⏸️",
            style=(
                discord.ButtonStyle.success
                if not tracking_enabled
                else discord.ButtonStyle.danger
            ),
            disabled=admin_blocked,
            row=1,
        )

        show.callback = self.show_marker
        hide.callback = self.hide_marker
        refresh.callback = self.refresh
        remove_entry.callback = self.remove_entry
        tracking.callback = self.toggle_tracking
        for item in (show, hide, refresh, remove_entry, tracking):
            self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id and interaction.guild_id == self.guild_id:
            self.tracker.remember_member(interaction.user)
            return True
        await interaction.response.send_message(
            "These personal ticket controls belong to another member.", ephemeral=True
        )
        return False

    async def edit(self, interaction: discord.Interaction) -> None:
        server_enabled = await self.tracker.store.nickname_markers_enabled(self.guild_id)
        user_enabled = await self.tracker.store.nickname_marker_allowed(
            self.guild_id, self.user_id
        )
        tracking_enabled = self.user_id not in self.tracker.tracking_opt_outs.get(
            self.guild_id, set()
        )
        admin_blocked = self.user_id in self.tracker.blocked_users.get(
            self.guild_id, set()
        )
        status = await self.tracker.store.get_status(self.guild_id, self.user_id)
        view = TicketNicknamePreferenceView(
            self.tracker,
            self.guild_id,
            self.user_id,
            server_enabled=server_enabled,
            user_enabled=user_enabled,
            tracking_enabled=tracking_enabled,
            has_status=status is not None,
            admin_blocked=admin_blocked,
            notice=self.notice,
        )
        embed = await self.tracker.build_personal_nickname_embed(
            self.guild_id,
            self.user_id,
            server_enabled=server_enabled,
            user_enabled=user_enabled,
            tracking_enabled=tracking_enabled,
            admin_blocked=admin_blocked,
            notice=self.notice,
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)

    async def show_marker(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.notice = await self.tracker.set_personal_nickname_preference(
            self.guild_id,
            self.user_id,
            True,
        )
        await self.edit(interaction)

    async def hide_marker(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.notice = await self.tracker.set_personal_nickname_preference(
            self.guild_id,
            self.user_id,
            False,
        )
        await self.edit(interaction)

    async def remove_entry(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.notice = await self.tracker.remove_personal_ticket_entry(
            self.guild_id,
            self.user_id,
        )
        await self.edit(interaction)

    async def toggle_tracking(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.notice = await self.tracker.set_personal_tracking_preference(
            self.guild_id,
            self.user_id,
            not self.tracking_enabled,
        )
        await self.edit(interaction)

    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.notice = "Your ticket settings were refreshed."
        await self.edit(interaction)


class TicketManagementSelect(discord.ui.Select):
    def __init__(self, view: "TicketManagementView") -> None:
        self.management_view = view
        start = view.page * MANAGEMENT_PAGE_SIZE
        entries = view.entries[start:start + MANAGEMENT_PAGE_SIZE]
        options: list[discord.SelectOption] = []
        for entry in entries:
            if entry.blocked:
                state = "Blocked from ticket tracking"
                emoji = "🚫"
            else:
                state = f"Currently {entry.tickets}/3 tickets"
                emoji = ui_emoji_button(
                    view.tracker.bot, "ticket_available", "🎟️"
                )
            options.append(
                discord.SelectOption(
                    label=entry.username[:100],
                    value=str(entry.user_id),
                    description=f"{state} • {entry.user_id}"[:100],
                    emoji=emoji,
                    default=entry.user_id == view.selected_user_id,
                )
            )
        super().__init__(
            placeholder="Choose a tracked or blocked user…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.management_view.selected_user_id = int(self.values[0])
        self.management_view.notice = None
        await self.management_view.edit(interaction)


class TicketManagementView(discord.ui.View):
    def __init__(
        self,
        tracker: "TicketTracker",
        guild_id: int,
        entries: list[TicketManagementEntry],
        *,
        page: int = 0,
        selected_user_id: int | None = None,
        notice: str | None = None,
        nickname_enabled: bool = False,
    ) -> None:
        super().__init__(timeout=600)
        self.tracker = tracker
        self.guild_id = guild_id
        self.entries = entries
        self.page_count = max(1, (len(entries) + MANAGEMENT_PAGE_SIZE - 1) // MANAGEMENT_PAGE_SIZE)
        self.page = max(0, min(page, self.page_count - 1))
        self.selected_user_id = selected_user_id
        self.notice = notice
        self.nickname_enabled = nickname_enabled

        if entries:
            self.add_item(TicketManagementSelect(self))

        previous = discord.ui.Button(
            label="Previous",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
            row=1,
        )
        next_button = discord.ui.Button(
            label="Next",
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self.page_count - 1,
            row=1,
        )
        refresh = discord.ui.Button(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        remove = discord.ui.Button(
            label="Remove from list",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            disabled=selected_user_id is None or self.selected_entry_is_blocked(),
            row=2,
        )
        block = discord.ui.Button(
            label="Block tracking",
            emoji="🚫",
            style=discord.ButtonStyle.danger,
            disabled=selected_user_id is None or self.selected_entry_is_blocked(),
            row=2,
        )
        unblock = discord.ui.Button(
            label="Unblock",
            emoji="✅",
            style=discord.ButtonStyle.success,
            disabled=selected_user_id is None or not self.selected_entry_is_blocked(),
            row=2,
        )
        toggle_nicknames = discord.ui.Button(
            label=(
                "Disable nickname markers"
                if self.nickname_enabled
                else "Enable nickname markers"
            ),
            emoji="🏷️",
            style=(
                discord.ButtonStyle.danger
                if self.nickname_enabled
                else discord.ButtonStyle.success
            ),
            row=3,
        )
        sync_nicknames = discord.ui.Button(
            label="Sync nickname markers",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            disabled=not self.nickname_enabled,
            row=3,
        )

        previous.callback = self.previous_page
        next_button.callback = self.next_page
        refresh.callback = self.refresh
        remove.callback = self.remove_selected
        block.callback = self.block_selected
        unblock.callback = self.unblock_selected
        toggle_nicknames.callback = self.toggle_nickname_markers
        sync_nicknames.callback = self.sync_nickname_markers
        for item in (
            previous,
            next_button,
            refresh,
            remove,
            block,
            unblock,
            toggle_nicknames,
            sync_nicknames,
        ):
            self.add_item(item)

    def selected_entry(self) -> TicketManagementEntry | None:
        if self.selected_user_id is None:
            return None
        return next(
            (entry for entry in self.entries if entry.user_id == self.selected_user_id),
            None,
        )

    def selected_entry_is_blocked(self) -> bool:
        entry = self.selected_entry()
        return bool(entry and entry.blocked)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                "This management panel belongs to another server.", ephemeral=True
            )
            return False
        if self.tracker.has_management_permission(interaction.user):
            return True
        await interaction.response.send_message(
            "You need **Manage Server** permission to use this panel.", ephemeral=True
        )
        return False

    async def edit(self, interaction: discord.Interaction) -> None:
        refreshed = await self.tracker.management_entries(self.guild_id)
        selected = self.selected_user_id
        if selected is not None and not any(item.user_id == selected for item in refreshed):
            selected = None
        nickname_enabled = await self.tracker.store.nickname_markers_enabled(
            self.guild_id
        )
        view = TicketManagementView(
            self.tracker,
            self.guild_id,
            refreshed,
            page=self.page,
            selected_user_id=selected,
            notice=self.notice,
            nickname_enabled=nickname_enabled,
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(
                embed=self.tracker.build_management_embed(view),
                view=view,
            )
        else:
            await interaction.response.edit_message(
                embed=self.tracker.build_management_embed(view),
                view=view,
            )

    async def previous_page(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        self.selected_user_id = None
        self.notice = None
        await self.edit(interaction)

    async def next_page(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.page = min(self.page_count - 1, self.page + 1)
        self.selected_user_id = None
        self.notice = None
        await self.edit(interaction)

    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.notice = "List refreshed."
        await self.edit(interaction)

    async def remove_selected(self, interaction: discord.Interaction) -> None:
        entry = self.selected_entry()
        if entry is None:
            await interaction.response.send_message(
                "Choose a user first.", ephemeral=True
            )
            return
        if entry.blocked:
            await interaction.response.send_message(
                "That user is blocked. Use **Unblock** instead.", ephemeral=True
            )
            return
        await interaction.response.defer()
        removed = await self.tracker.store.remove_status(self.guild_id, entry.user_id)
        self.tracker.invalidate_board_cache(self.guild_id)
        self.tracker.drop_pending_for_user(self.guild_id, entry.user_id)
        await self.tracker.clear_ticket_nickname(
            self.guild_id,
            entry.user_id,
            reason="Boss-ticket entry removed",
        )
        if await self.tracker.store.get_config(self.guild_id) is not None:
            self.tracker.queue_board_refresh(self.guild_id)
        self.selected_user_id = None
        self.notice = (
            f"Removed {entry.username} from the current board. They can reappear "
            "after their next ticket check."
            if removed else "That user was already absent from the board."
        )
        logger.info(
            "Ticket manager removed user %s from guild %s by %s",
            entry.user_id,
            self.guild_id,
            interaction.user.id,
        )
        await self.edit(interaction)

    async def block_selected(self, interaction: discord.Interaction) -> None:
        entry = self.selected_entry()
        if entry is None:
            await interaction.response.send_message(
                "Choose a user first.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self.tracker.store.block_user(
            self.guild_id,
            entry.user_id,
            entry.username,
            interaction.user.id,
        )
        self.tracker.invalidate_board_cache(self.guild_id)
        self.tracker.blocked_users.setdefault(self.guild_id, set()).add(entry.user_id)
        self.tracker.drop_pending_for_user(self.guild_id, entry.user_id)
        await self.tracker.clear_ticket_nickname(
            self.guild_id,
            entry.user_id,
            reason="Boss-ticket tracking blocked",
        )
        if await self.tracker.store.get_config(self.guild_id) is not None:
            self.tracker.queue_board_refresh(self.guild_id)
        self.selected_user_id = None
        self.notice = (
            f"Blocked {entry.username}. Future ticket checks from this user will "
            "not be recorded until an admin unblocks them."
        )
        logger.info(
            "Ticket manager blocked user %s in guild %s by %s",
            entry.user_id,
            self.guild_id,
            interaction.user.id,
        )
        await self.edit(interaction)

    async def unblock_selected(self, interaction: discord.Interaction) -> None:
        entry = self.selected_entry()
        if entry is None:
            await interaction.response.send_message(
                "Choose a user first.", ephemeral=True
            )
            return
        await interaction.response.defer()
        changed = await self.tracker.store.unblock_user(self.guild_id, entry.user_id)
        self.tracker.blocked_users.setdefault(self.guild_id, set()).discard(entry.user_id)
        self.selected_user_id = None
        self.notice = (
            f"Unblocked {entry.username}. They will return after their next ticket check."
            if changed else "That user was already unblocked."
        )
        logger.info(
            "Ticket manager unblocked user %s in guild %s by %s",
            entry.user_id,
            self.guild_id,
            interaction.user.id,
        )
        await self.edit(interaction)

    async def toggle_nickname_markers(
        self, interaction: discord.Interaction
    ) -> None:
        await interaction.response.defer()
        if self.nickname_enabled:
            await self.tracker.store.set_nickname_markers_enabled(
                self.guild_id,
                False,
                interaction.user.id,
            )
            self.nickname_enabled = False
            self.tracker.queue_nickname_job(self.guild_id, "restore")
            self.notice = (
                "Nickname markers were disabled immediately. Existing managed suffixes "
                "are being restored in the background."
            )
        else:
            allowed, message = await self.tracker.nickname_feature_ready(
                self.guild_id
            )
            if not allowed:
                self.notice = message
                await self.edit(interaction)
                return
            await self.tracker.store.set_nickname_markers_enabled(
                self.guild_id,
                True,
                interaction.user.id,
            )
            self.nickname_enabled = True
            self.tracker.queue_nickname_job(self.guild_id, "cleanup")
            self.tracker.queue_nickname_job(self.guild_id, "sync")
            self.notice = (
                "Nickname markers are available. Members remain unchanged unless they "
                "have explicitly opted in; saved opt-ins are syncing in the background. "
                "Members can use My settings or the reaction shortcut under their ticket "
                "command."
            )
        logger.info(
            "Ticket nickname markers set to %s in guild %s by %s",
            self.nickname_enabled,
            self.guild_id,
            interaction.user.id,
        )
        await self.edit(interaction)

    async def sync_nickname_markers(
        self, interaction: discord.Interaction
    ) -> None:
        if not self.nickname_enabled:
            await interaction.response.send_message(
                "Enable nickname markers first.", ephemeral=True
            )
            return
        await interaction.response.defer()
        allowed, message = await self.tracker.nickname_feature_ready(self.guild_id)
        if not allowed:
            self.notice = message
            await self.edit(interaction)
            return
        self.tracker.queue_nickname_job(self.guild_id, "sync")
        self.notice = (
            "A background sync was queued for members who explicitly enabled their "
            "marker. The management panel remains responsive while it runs."
        )
        logger.info(
            "Ticket nickname marker sync queued in guild %s by %s",
            self.guild_id,
            interaction.user.id,
        )
        await self.edit(interaction)


class TicketTracker(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = TicketStore(DATABASE_FILE)
        self.pending_by_channel: dict[int, list[PendingTicketRequest]] = {}
        self.board_locks: dict[int, asyncio.Lock] = {}
        self.nickname_locks: dict[int, asyncio.Lock] = {}
        self.nickname_user_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self.reset_task: asyncio.Task[None] | None = None
        self.startup_task: asyncio.Task[None] | None = None
        self.processed_responses: dict[tuple[int, int], float] = {}
        self.capture_tasks: dict[tuple[int, int], asyncio.Task[bool]] = {}
        self.board_refresh_tasks: dict[int, asyncio.Task[None]] = {}
        self.board_refresh_dirty: set[int] = set()
        self.board_status_cache: dict[
            int, tuple[float, tuple[TicketStatus, ...]]
        ] = {}
        self.nickname_job_tasks: dict[int, asyncio.Task[None]] = {}
        self.nickname_job_requests: dict[int, list[str]] = {}
        self.recent_members: dict[
            tuple[int, int], tuple[float, discord.Member]
        ] = {}
        self.reaction_controls: dict[
            tuple[int, int, int], TicketReactionControl
        ] = {}
        self.blocked_users: dict[int, set[int]] = {}
        self.tracking_opt_outs: dict[int, set[int]] = {}
        self.identity_refresh_seen: dict[tuple[int, int], float] = {}
        self._restored = False
        self._closing = False

    async def cog_load(self) -> None:
        self._closing = False
        await self.store.initialize()
        self.blocked_users = await self.store.load_all_blocked_ids()
        self.tracking_opt_outs = await self.store.load_all_tracking_opt_outs()
        self.bot.add_view(TicketBoardView(self))
        self.reset_task = asyncio.create_task(self.daily_reset_loop())
        logger.info("Boss ticket storage ready at %s", DATABASE_FILE)

    async def cog_unload(self) -> None:
        self._closing = True
        if self.reset_task:
            self.reset_task.cancel()
            self.reset_task = None
        if self.startup_task:
            self.startup_task.cancel()
            self.startup_task = None
        for task in self.capture_tasks.values():
            task.cancel()
        for task in self.board_refresh_tasks.values():
            task.cancel()
        for task in self.nickname_job_tasks.values():
            task.cancel()
        self.capture_tasks.clear()
        self.board_refresh_tasks.clear()
        self.board_refresh_dirty.clear()
        self.nickname_job_tasks.clear()
        self.nickname_job_requests.clear()
        self.reaction_controls.clear()
        self.recent_members.clear()

    def board_lock(self, guild_id: int) -> asyncio.Lock:
        return self.board_locks.setdefault(guild_id, asyncio.Lock())

    def nickname_lock(self, guild_id: int) -> asyncio.Lock:
        return self.nickname_locks.setdefault(guild_id, asyncio.Lock())

    def nickname_user_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self.nickname_user_locks.setdefault((guild_id, user_id), asyncio.Lock())

    def remember_member(self, user: discord.abc.User) -> None:
        if not isinstance(user, discord.Member):
            return
        self.recent_members[(user.guild.id, user.id)] = (time.monotonic(), user)

    def recent_member(self, guild_id: int, user_id: int) -> discord.Member | None:
        cached = self.recent_members.get((guild_id, user_id))
        if cached is None:
            return None
        seen_at, member = cached
        if time.monotonic() - seen_at > RECENT_MEMBER_TTL_SECONDS:
            self.recent_members.pop((guild_id, user_id), None)
            return None
        return member

    def invalidate_board_cache(self, guild_id: int) -> None:
        self.board_status_cache.pop(guild_id, None)

    async def get_board_statuses(
        self,
        guild_id: int,
        *,
        force: bool = False,
    ) -> list[TicketStatus]:
        cached = self.board_status_cache.get(guild_id)
        if (
            not force
            and cached is not None
            and time.monotonic() - cached[0] <= BOARD_STATUS_CACHE_TTL_SECONDS
        ):
            return list(cached[1])
        statuses = await self.store.list_status(guild_id)
        self.board_status_cache[guild_id] = (
            time.monotonic(),
            tuple(statuses),
        )
        return statuses

    def queue_board_refresh(self, guild_id: int) -> None:
        if self._closing:
            return
        self.invalidate_board_cache(guild_id)
        self.board_refresh_dirty.add(guild_id)
        task = self.board_refresh_tasks.get(guild_id)
        if task is None or task.done():
            self.board_refresh_tasks[guild_id] = asyncio.create_task(
                self._board_refresh_worker(guild_id)
            )

    async def _board_refresh_worker(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(BOARD_REFRESH_DEBOUNCE_SECONDS)
            while guild_id in self.board_refresh_dirty:
                self.board_refresh_dirty.discard(guild_id)
                try:
                    await self.refresh_board(guild_id)
                except Exception:
                    logger.exception(
                        "Queued ticket-board refresh failed for guild %s", guild_id
                    )
                if guild_id in self.board_refresh_dirty:
                    await asyncio.sleep(BOARD_REFRESH_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        finally:
            self.board_refresh_tasks.pop(guild_id, None)
            # Close the tiny race where a new dirty flag is set after the loop has
            # observed an empty set but before this task is removed from the registry.
            if not self._closing and guild_id in self.board_refresh_dirty:
                self.board_refresh_tasks[guild_id] = asyncio.create_task(
                    self._board_refresh_worker(guild_id)
                )

    def queue_nickname_job(self, guild_id: int, mode: str) -> None:
        if self._closing:
            return
        if mode not in {"sync", "cleanup", "restore"}:
            raise ValueError(f"Unsupported nickname job mode: {mode}")
        queue = self.nickname_job_requests.setdefault(guild_id, [])
        if mode == "restore":
            queue.clear()
        if not queue or queue[-1] != mode:
            queue.append(mode)
        task = self.nickname_job_tasks.get(guild_id)
        if task is None or task.done():
            self.nickname_job_tasks[guild_id] = asyncio.create_task(
                self._nickname_job_worker(guild_id)
            )

    async def _nickname_job_worker(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(0)
            while True:
                queue = self.nickname_job_requests.get(guild_id, [])
                if not queue:
                    break
                mode = queue.pop(0)
                started = time.monotonic()
                if mode == "restore":
                    result = await self.restore_guild_nicknames(guild_id)
                elif mode == "cleanup":
                    result = await self.cleanup_unopted_nicknames(guild_id)
                else:
                    result = await self.sync_guild_nicknames(guild_id)
                logger.info(
                    "Background ticket nickname %s completed for guild %s: %s processed in %.2fs",
                    mode,
                    guild_id,
                    result.total_processed,
                    time.monotonic() - started,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(
                "Background ticket nickname job failed for guild %s", guild_id
            )
        finally:
            self.nickname_job_tasks.pop(guild_id, None)
            pending = self.nickname_job_requests.get(guild_id, [])
            if pending and not self._closing:
                # A request can arrive between the worker's empty-queue check and
                # cleanup. Start a successor instead of losing that request.
                self.nickname_job_tasks[guild_id] = asyncio.create_task(
                    self._nickname_job_worker(guild_id)
                )
            else:
                self.nickname_job_requests.pop(guild_id, None)

    def purge_reaction_controls(self) -> None:
        cutoff = time.monotonic() - REACTION_CONTROL_TTL_SECONDS
        self.reaction_controls = {
            key: control
            for key, control in self.reaction_controls.items()
            if control.created_at >= cutoff
        }

    def has_management_permission(self, user: discord.abc.User) -> bool:
        owner_id = int((os.getenv("BOT_OWNER_ID") or "0").strip() or 0)
        if owner_id and user.id == owner_id:
            return True
        return isinstance(user, discord.Member) and user.guild_permissions.manage_guild

    async def nickname_feature_ready(self, guild_id: int) -> tuple[bool, str]:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False, "I cannot access this server right now."
        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            try:
                bot_member = await guild.fetch_member(self.bot.user.id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                bot_member = None
        if bot_member is None:
            return False, "I could not check my server permissions."
        if not bot_member.guild_permissions.manage_nicknames:
            return (
                False,
                "Grant my bot role **Manage Nicknames**, then reopen `HBS`. "
                "The role must also be above members whose names should be updated.",
            )
        return True, "Nickname markers are ready."

    async def fetch_known_member(
        self, guild: discord.Guild, user_id: int
    ) -> discord.Member | None:
        member = self.recent_member(guild.id, user_id) or guild.get_member(user_id)
        if member is not None:
            self.remember_member(member)
            return member
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning(
                "Could not fetch member %s in guild %s for ticket nickname: %s",
                user_id,
                guild.id,
                exc,
            )
            return None
        self.remember_member(member)
        return member

    async def refresh_status_identities(
        self,
        guild_id: int,
        statuses: list[TicketStatus],
        *,
        force: bool = False,
    ) -> bool:
        """Refresh visible identities without requiring the Guild Members intent."""
        guild = self.bot.get_guild(guild_id)
        if guild is None or not statuses:
            return False

        now = time.monotonic()
        candidates = [
            status
            for status in statuses
            if force
            or now - self.identity_refresh_seen.get((guild_id, status.user_id), 0.0)
            >= IDENTITY_REFRESH_TTL_SECONDS
        ]
        if not candidates:
            return False

        semaphore = asyncio.Semaphore(IDENTITY_REFRESH_CONCURRENCY)

        async def refresh_one(status: TicketStatus) -> bool:
            async with semaphore:
                member = await self.fetch_known_member(guild, status.user_id)
            self.identity_refresh_seen[(guild_id, status.user_id)] = time.monotonic()
            if member is None:
                return False
            raw_display_name = getattr(member, "display_name", member.name)
            display_name = strip_ticket_nickname_marker(raw_display_name) or raw_display_name
            account_username = getattr(member, "name", "")
            if (
                display_name == status.username
                and account_username == status.account_username
            ):
                return False
            return await self.store.update_identity(
                guild_id,
                status.user_id,
                display_name,
                account_username,
            )

        results = await asyncio.gather(
            *(refresh_one(status) for status in candidates),
            return_exceptions=True,
        )
        changed = False
        for status, result in zip(candidates, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Could not refresh ticket identity for user %s in guild %s: %s",
                    status.user_id,
                    guild_id,
                    result,
                )
                continue
            changed = bool(result) or changed
        return changed

    def nickname_edit_status(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> str | None:
        if member.id == guild.owner_id:
            return "owner"
        bot_member = guild.me
        if bot_member is None:
            return "permission"
        if not bot_member.guild_permissions.manage_nicknames:
            return "permission"
        if self.bot.user is not None and member.id == self.bot.user.id:
            return "hierarchy"
        if bot_member.top_role <= member.top_role:
            return "hierarchy"
        return None

    @staticmethod
    def add_nickname_result(result: NicknameSyncResult, status: str) -> None:
        field = {
            "updated": "updated",
            "unchanged": "unchanged",
            "restored": "restored",
            "missing": "missing",
            "owner": "skipped_owner",
            "hierarchy": "skipped_hierarchy",
            "permission": "missing_permission",
            "failed": "failed",
            "already-cleared": "unchanged",
            "not-managed": "unchanged",
            "disabled": "unchanged",
            "opted-out": "opted_out",
        }.get(status, "failed")
        setattr(result, field, getattr(result, field) + 1)

    @staticmethod
    def nickname_result_text(prefix: str, result: NicknameSyncResult) -> str:
        parts: list[str] = []
        if result.updated:
            parts.append(f"{result.updated} updated")
        if result.restored:
            parts.append(f"{result.restored} restored")
        if result.unchanged:
            parts.append(f"{result.unchanged} unchanged")
        if result.skipped_owner:
            parts.append(f"{result.skipped_owner} server owner skipped")
        if result.skipped_hierarchy:
            parts.append(f"{result.skipped_hierarchy} above/equal role skipped")
        if result.missing_permission:
            parts.append(f"{result.missing_permission} missing permission")
        if result.opted_out:
            parts.append(f"{result.opted_out} personally hidden")
        if result.missing:
            parts.append(f"{result.missing} no longer in server")
        if result.failed:
            parts.append(f"{result.failed} failed")
        return f"{prefix}: " + (", ".join(parts) if parts else "nothing to change") + "."

    async def apply_ticket_nickname(
        self,
        guild_id: int,
        user_id: int,
        tickets: int,
    ) -> str:
        async with self.nickname_user_lock(guild_id, user_id):
            return await self._apply_ticket_nickname_unlocked(
                guild_id,
                user_id,
                tickets,
            )

    async def _apply_ticket_nickname_unlocked(
        self,
        guild_id: int,
        user_id: int,
        tickets: int,
    ) -> str:
        if not await self.store.nickname_markers_enabled(guild_id):
            return "disabled"
        if user_id in self.tracking_opt_outs.get(guild_id, set()):
            if await self.store.get_nickname_state(guild_id, user_id) is not None:
                await self._clear_ticket_nickname_unlocked(
                    guild_id,
                    user_id,
                    reason="Member opted out of OwO boss-ticket tracking",
                )
            return "opted-out"
        if not await self.store.nickname_marker_allowed(guild_id, user_id):
            if await self.store.get_nickname_state(guild_id, user_id) is not None:
                await self._clear_ticket_nickname_unlocked(
                    guild_id,
                    user_id,
                    reason="Member disabled their OwO boss-ticket nickname marker",
                )
            return "opted-out"
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return "missing"
        member = await self.fetch_known_member(guild, user_id)
        if member is None:
            return "missing"
        edit_status = self.nickname_edit_status(guild, member)
        if edit_status is not None:
            logger.info(
                "Skipped ticket nickname for user %s in guild %s: %s",
                user_id,
                guild_id,
                edit_status,
            )
            return edit_status

        state = await self.store.get_nickname_state(guild_id, user_id)
        current_nickname = member.nick
        if state is None:
            # On the first application, preserve the complete existing nickname even
            # if it coincidentally resembles our marker format.
            base_nickname = current_nickname
        elif current_nickname == state.last_applied_nickname:
            base_nickname = state.base_nickname
        else:
            # Another bot or an administrator changed the name after our last edit.
            # Preserve that change while removing only our known suffix.
            base_nickname = strip_ticket_nickname_marker(current_nickname)

        display_base = (
            base_nickname
            or getattr(member, "global_name", None)
            or member.name
        )
        new_nickname = build_ticket_nickname(display_base, tickets)
        if current_nickname == new_nickname:
            await self.store.save_nickname_state(
                guild_id,
                user_id,
                base_nickname,
                new_nickname,
            )
            return "unchanged"

        try:
            await member.edit(
                nick=new_nickname,
                reason=f"OwO boss tickets updated to {tickets}/3",
            )
        except discord.Forbidden:
            logger.warning(
                "Missing permission or role hierarchy for ticket nickname user %s in guild %s",
                user_id,
                guild_id,
            )
            return "hierarchy"
        except discord.HTTPException as exc:
            logger.warning(
                "Could not update ticket nickname for user %s in guild %s: %s",
                user_id,
                guild_id,
                exc,
            )
            return "failed"

        await self.store.save_nickname_state(
            guild_id,
            user_id,
            base_nickname,
            new_nickname,
        )
        logger.info(
            "Updated ticket nickname for user %s in guild %s to %s/3",
            user_id,
            guild_id,
            tickets,
        )
        return "updated"

    async def clear_ticket_nickname(
        self,
        guild_id: int,
        user_id: int,
        *,
        reason: str,
    ) -> str:
        async with self.nickname_user_lock(guild_id, user_id):
            return await self._clear_ticket_nickname_unlocked(
                guild_id,
                user_id,
                reason=reason,
            )

    async def _clear_ticket_nickname_unlocked(
        self,
        guild_id: int,
        user_id: int,
        *,
        reason: str,
    ) -> str:
        state = await self.store.get_nickname_state(guild_id, user_id)
        if state is None:
            return "not-managed"
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return "missing"
        member = await self.fetch_known_member(guild, user_id)
        if member is None:
            await self.store.delete_nickname_state(guild_id, user_id)
            return "missing"
        edit_status = self.nickname_edit_status(guild, member)
        if edit_status is not None:
            return edit_status

        current_nickname = member.nick
        if current_nickname == state.last_applied_nickname:
            target_nickname = state.base_nickname
        else:
            stripped = strip_ticket_nickname_marker(current_nickname)
            if stripped == current_nickname:
                await self.store.delete_nickname_state(guild_id, user_id)
                return "already-cleared"
            target_nickname = stripped

        try:
            await member.edit(nick=target_nickname, reason=reason)
        except discord.Forbidden:
            return "hierarchy"
        except discord.HTTPException as exc:
            logger.warning(
                "Could not restore ticket nickname for user %s in guild %s: %s",
                user_id,
                guild_id,
                exc,
            )
            return "failed"

        await self.store.delete_nickname_state(guild_id, user_id)
        logger.info(
            "Restored ticket nickname for user %s in guild %s",
            user_id,
            guild_id,
        )
        return "restored"

    async def sync_guild_nicknames(self, guild_id: int) -> NicknameSyncResult:
        result = NicknameSyncResult()
        async with self.nickname_lock(guild_id):
            opted_in = await self.store.list_nickname_opt_in_ids(guild_id)
            statuses = [
                status
                for status in await self.store.list_status(guild_id)
                if status.user_id in opted_in
            ]
            for index, status in enumerate(statuses):
                async with self.nickname_user_lock(guild_id, status.user_id):
                    latest = await self.store.get_status(guild_id, status.user_id)
                    if latest is None:
                        outcome = "missing"
                    else:
                        outcome = await self._apply_ticket_nickname_unlocked(
                            guild_id,
                            latest.user_id,
                            latest.tickets,
                        )
                self.add_nickname_result(result, outcome)
                if index + 1 < len(statuses):
                    await asyncio.sleep(NICKNAME_SYNC_DELAY_SECONDS)
        return result

    async def cleanup_unopted_nicknames(self, guild_id: int) -> NicknameSyncResult:
        result = NicknameSyncResult()
        async with self.nickname_lock(guild_id):
            opted_in = await self.store.list_nickname_opt_in_ids(guild_id)
            states = [
                state
                for state in await self.store.list_nickname_states(guild_id)
                if state.user_id not in opted_in
            ]
            for index, state in enumerate(states):
                async with self.nickname_user_lock(guild_id, state.user_id):
                    # A member can opt in while this background cleanup is running.
                    # Recheck inside the per-user lock so a fresh choice is never undone.
                    if await self.store.nickname_marker_allowed(
                        guild_id, state.user_id
                    ):
                        outcome = "unchanged"
                    else:
                        outcome = await self._clear_ticket_nickname_unlocked(
                            guild_id,
                            state.user_id,
                            reason="OwO boss-ticket nickname now requires member opt-in",
                        )
                self.add_nickname_result(result, outcome)
                if index + 1 < len(states):
                    await asyncio.sleep(NICKNAME_SYNC_DELAY_SECONDS)
        return result

    async def restore_guild_nicknames(self, guild_id: int) -> NicknameSyncResult:
        result = NicknameSyncResult()
        async with self.nickname_lock(guild_id):
            states = await self.store.list_nickname_states(guild_id)
            for index, state in enumerate(states):
                async with self.nickname_user_lock(guild_id, state.user_id):
                    outcome = await self._clear_ticket_nickname_unlocked(
                        guild_id,
                        state.user_id,
                        reason="OwO boss-ticket nickname markers disabled",
                    )
                self.add_nickname_result(result, outcome)
                if index + 1 < len(states):
                    await asyncio.sleep(NICKNAME_SYNC_DELAY_SECONDS)
        return result

    async def build_personal_nickname_embed(
        self,
        guild_id: int,
        user_id: int,
        *,
        server_enabled: bool,
        user_enabled: bool,
        tracking_enabled: bool,
        admin_blocked: bool,
        notice: str | None = None,
    ) -> discord.Embed:
        status = await self.store.get_status(guild_id, user_id)
        state = await self.store.get_nickname_state(guild_id, user_id)
        guild = self.bot.get_guild(guild_id)
        owner_note = bool(guild and guild.owner_id == user_id)

        if admin_blocked:
            tracking_text = "Blocked by a server manager"
        elif tracking_enabled:
            tracking_text = "Active"
        else:
            tracking_text = "Paused by you"

        if not server_enabled:
            marker_text = "Unavailable because the server feature is off"
        elif not user_enabled:
            marker_text = "Off by default — only you can enable it"
        elif state:
            marker_text = "Enabled by you and currently managed"
        else:
            marker_text = "Enabled by you; waiting for a ticket check or sync"

        count_text = f"{status.tickets}/3" if status else "Not currently listed"
        description = (
            f"**Ticket tracking:** {tracking_text}\n"
            f"**Board entry:** {count_text}\n"
            f"**Nickname marker:** {marker_text}\n\n"
            "Nickname changes are off by default for every member. Use **Enable my "
            "marker** only when you want the bot to add the ticket suffix. You can also "
            "toggle it from the action reaction under your own ticket command.\n\n"
            "**Remove me from list** deletes only your current board entry. Your next "
            "ticket check can add you again. **Stop tracking me** removes your entry, "
            "clears the nickname marker, and ignores future ticket checks until you "
            "choose **Resume tracking**."
        )
        if owner_note:
            description += (
                "\n\n⚠️ Discord does not allow bots to edit the server owner's "
                "nickname, but your board and tracking controls still work."
            )
        if admin_blocked:
            description += (
                "\n\n⚠️ A server manager blocked ticket tracking for you. Only a "
                "server manager can remove that block."
            )
        if notice:
            description += f"\n\n**Update:** {notice}"
        embed = discord.Embed(
            title="⚙️ My Boss-Ticket Settings",
            description=description,
            color=0x5865F2,
        )
        embed.set_footer(
            text=(
                "Open with My settings, /boss-ticket-nickname, "
                "H boss nickname, or HBN."
            )
        )
        return embed

    async def send_personal_nickname_panel(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This control only works inside a server.", ephemeral=True
            )
            return
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        self.remember_member(interaction.user)
        await interaction.response.defer(ephemeral=True, thinking=True)
        server_enabled = await self.store.nickname_markers_enabled(guild_id)
        user_enabled = await self.store.nickname_marker_allowed(guild_id, user_id)
        tracking_enabled = user_id not in self.tracking_opt_outs.get(guild_id, set())
        admin_blocked = user_id in self.blocked_users.get(guild_id, set())
        status = await self.store.get_status(guild_id, user_id)
        view = TicketNicknamePreferenceView(
            self,
            guild_id,
            user_id,
            server_enabled=server_enabled,
            user_enabled=user_enabled,
            tracking_enabled=tracking_enabled,
            has_status=status is not None,
            admin_blocked=admin_blocked,
        )
        embed = await self.build_personal_nickname_embed(
            guild_id,
            user_id,
            server_enabled=server_enabled,
            user_enabled=user_enabled,
            tracking_enabled=tracking_enabled,
            admin_blocked=admin_blocked,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def send_personal_nickname_panel_message(
        self,
        message: discord.Message,
    ) -> None:
        if message.guild is None:
            return
        guild_id = message.guild.id
        user_id = message.author.id
        server_enabled = await self.store.nickname_markers_enabled(guild_id)
        user_enabled = await self.store.nickname_marker_allowed(guild_id, user_id)
        tracking_enabled = user_id not in self.tracking_opt_outs.get(guild_id, set())
        admin_blocked = user_id in self.blocked_users.get(guild_id, set())
        status = await self.store.get_status(guild_id, user_id)
        view = TicketNicknamePreferenceView(
            self,
            guild_id,
            user_id,
            server_enabled=server_enabled,
            user_enabled=user_enabled,
            tracking_enabled=tracking_enabled,
            has_status=status is not None,
            admin_blocked=admin_blocked,
        )
        embed = await self.build_personal_nickname_embed(
            guild_id,
            user_id,
            server_enabled=server_enabled,
            user_enabled=user_enabled,
            tracking_enabled=tracking_enabled,
            admin_blocked=admin_blocked,
        )
        await message.reply(
            embed=embed,
            view=view,
            mention_author=False,
            delete_after=300,
        )

    async def set_personal_nickname_preference(
        self,
        guild_id: int,
        user_id: int,
        enabled: bool,
    ) -> str:
        await self.store.set_nickname_marker_allowed(guild_id, user_id, enabled)
        if not enabled:
            outcome = await self.clear_ticket_nickname(
                guild_id,
                user_id,
                reason="Member disabled their OwO boss-ticket nickname marker",
            )
            logger.info(
                "User %s disabled ticket nickname marker in guild %s (%s)",
                user_id,
                guild_id,
                outcome,
            )
            if outcome in {"restored", "already-cleared", "not-managed", "missing"}:
                return "Your nickname marker is hidden. Your board entry is unchanged."
            if outcome == "owner":
                return "Your preference is saved. Discord cannot edit the server owner."
            if outcome == "hierarchy":
                return "Your preference is saved, but role hierarchy prevented restoration."
            return "Your preference is saved, but the nickname could not be fully restored."

        if user_id in self.tracking_opt_outs.get(guild_id, set()):
            return "Resume ticket tracking before showing a nickname marker."
        if user_id in self.blocked_users.get(guild_id, set()):
            return "A server manager has blocked your ticket tracking."
        if not await self.store.nickname_markers_enabled(guild_id):
            return "Your preference is saved. A manager must enable markers first."
        status = await self.store.get_status(guild_id, user_id)
        if status is None:
            return "Your marker is enabled. Run `w boss t` to record a count and apply it."
        outcome = await self.apply_ticket_nickname(guild_id, user_id, status.tickets)
        logger.info(
            "User %s enabled ticket nickname marker in guild %s (%s)",
            user_id,
            guild_id,
            outcome,
        )
        if outcome in {"updated", "unchanged"}:
            return "Your marker is enabled and synced to the current ticket count."
        if outcome == "owner":
            return "Saved, but Discord cannot edit the server owner's nickname."
        if outcome == "hierarchy":
            return "Saved, but my role is not high enough to edit your nickname."
        if outcome == "permission":
            return "Saved, but the bot needs Manage Nicknames permission."
        return "Saved, but the nickname could not be updated right now."

    async def remove_personal_ticket_entry(
        self,
        guild_id: int,
        user_id: int,
    ) -> str:
        removed = await self.store.remove_status(guild_id, user_id)
        self.invalidate_board_cache(guild_id)
        self.drop_pending_for_user(guild_id, user_id)
        await self.clear_ticket_nickname(
            guild_id,
            user_id,
            reason="Member removed their OwO boss-ticket board entry",
        )
        if await self.store.get_config(guild_id) is not None:
            self.queue_board_refresh(guild_id)
        logger.info(
            "User %s removed their own ticket entry in guild %s",
            user_id,
            guild_id,
        )
        if removed is None:
            return "You were already absent from the ticket list."
        return "Your current entry was removed. A later `w boss t` can add you again."

    async def set_personal_tracking_preference(
        self,
        guild_id: int,
        user_id: int,
        enabled: bool,
    ) -> str:
        if enabled and user_id in self.blocked_users.get(guild_id, set()):
            return "A server manager blocked your tracking. Ask a manager to unblock you."

        await self.store.set_ticket_tracking_allowed(guild_id, user_id, enabled)
        opted_out = self.tracking_opt_outs.setdefault(guild_id, set())
        if enabled:
            opted_out.discard(user_id)
            logger.info(
                "User %s resumed ticket tracking in guild %s",
                user_id,
                guild_id,
            )
            return "Ticket tracking resumed. Run `w boss t` to add your current count."

        opted_out.add(user_id)
        removed = await self.store.remove_status(guild_id, user_id)
        self.invalidate_board_cache(guild_id)
        self.drop_pending_for_user(guild_id, user_id)
        await self.clear_ticket_nickname(
            guild_id,
            user_id,
            reason="Member opted out of OwO boss-ticket tracking",
        )
        if await self.store.get_config(guild_id) is not None:
            self.queue_board_refresh(guild_id)
        logger.info(
            "User %s opted out of ticket tracking in guild %s",
            user_id,
            guild_id,
        )
        suffix = " and your current entry was removed" if removed else ""
        return f"Ticket tracking is paused{suffix}. Future ticket checks will be ignored."

    async def add_ticket_command_marker_reaction(
        self,
        request: PendingTicketRequest,
        nickname_result: str,
    ) -> None:
        del nickname_result  # The reaction is now an action, not a passive status icon.
        if not await self.store.nickname_markers_enabled(request.guild_id):
            return
        enabled = await self.store.nickname_marker_allowed(
            request.guild_id, request.user_id
        )
        emoji = NICKNAME_HIDE_EMOJI if enabled else NICKNAME_SHOW_EMOJI
        channel = self.bot.get_channel(request.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(request.channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return
        get_partial = getattr(channel, "get_partial_message", None)
        if not callable(get_partial):
            return
        try:
            await get_partial(request.command_message_id).add_reaction(emoji)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return
        self.purge_reaction_controls()
        key = (request.guild_id, request.channel_id, request.command_message_id)
        self.reaction_controls[key] = TicketReactionControl(
            guild_id=request.guild_id,
            channel_id=request.channel_id,
            message_id=request.command_message_id,
            user_id=request.user_id,
            emoji=emoji,
            created_at=time.monotonic(),
        )

    def drop_pending_for_user(self, guild_id: int, user_id: int) -> None:
        for channel_id, pending in list(self.pending_by_channel.items()):
            remaining = [
                item for item in pending
                if not (item.guild_id == guild_id and item.user_id == user_id)
            ]
            if remaining:
                self.pending_by_channel[channel_id] = remaining
            else:
                self.pending_by_channel.pop(channel_id, None)

    async def management_entries(self, guild_id: int) -> list[TicketManagementEntry]:
        statuses = await self.store.list_status(guild_id)
        blocked = await self.store.list_blocked(guild_id)
        blocked_ids = {item.user_id for item in blocked}
        entries = [
            TicketManagementEntry(
                user_id=status.user_id,
                username=status.username,
                tickets=status.tickets,
                updated_at=status.updated_at,
                blocked=False,
            )
            for status in statuses
            if status.user_id not in blocked_ids
        ]
        entries.extend(
            TicketManagementEntry(
                user_id=item.user_id,
                username=item.username,
                tickets=None,
                updated_at=item.blocked_at,
                blocked=True,
            )
            for item in blocked
        )
        return entries

    def build_management_embed(self, view: TicketManagementView) -> discord.Embed:
        tracked_count = sum(not item.blocked for item in view.entries)
        blocked_count = sum(item.blocked for item in view.entries)
        selected = view.selected_entry()
        nickname_state = "Enabled" if view.nickname_enabled else "Disabled (default)"
        description = (
            f"**Tracked:** {tracked_count} • **Blocked:** {blocked_count}\n"
            f"**Ticket nickname markers:** {nickname_state}\n\n"
            "Choose a user, then select an action. **Remove from list** deletes only "
            "their current entry; their next ticket check can add them again. "
            "**Block tracking** removes them and ignores future ticket checks until "
            "an admin unblocks them.\n\n"
            "Enabling nickname markers only makes the feature available; every member "
            "stays unchanged until they explicitly opt in. Members can use **My "
            "settings** or the action reaction under their own ticket command. Bulk "
            "syncs run in the background and include opted-in members only.\n\n"
            "Nickname markers use `Name · 🎟🎟▫`, require **Manage Nicknames**, and "
            "cannot edit the server owner or members whose highest role is equal to "
            "or above the bot role."
        )
        if selected is not None:
            state = "Blocked" if selected.blocked else f"{selected.tickets}/3 tickets"
            description += (
                f"\n\n**Selected:** {discord.utils.escape_markdown(selected.username)} "
                f"(`{selected.user_id}`) — {state}"
            )
        if view.notice:
            description += f"\n\n✅ {view.notice}"
        embed = discord.Embed(
            title=f"⚙️ Boss Ticket Management — Page {view.page + 1}/{view.page_count}",
            description=description,
            color=0x5865F2,
        )
        embed.set_footer(
            text="Panel requires Manage Server. Nickname markers require Manage Nicknames."
        )
        return embed

    async def send_management_panel(
        self,
        *,
        guild_id: int,
        send: Any,
        ephemeral: bool = False,
    ) -> None:
        entries = await self.management_entries(guild_id)
        nickname_enabled = await self.store.nickname_markers_enabled(guild_id)
        view = TicketManagementView(
            self,
            guild_id,
            entries,
            nickname_enabled=nickname_enabled,
        )
        kwargs: dict[str, Any] = {
            "embed": self.build_management_embed(view),
            "view": view,
        }
        if ephemeral:
            kwargs["ephemeral"] = True
        await send(**kwargs)

    def purge_pending(self, channel_id: int) -> list[PendingTicketRequest]:
        now = time.monotonic()
        pending = [
            item
            for item in self.pending_by_channel.get(channel_id, [])
            if now - item.created_at <= PENDING_REQUEST_SECONDS
        ]
        if pending:
            self.pending_by_channel[channel_id] = pending
        else:
            self.pending_by_channel.pop(channel_id, None)
        return pending

    def arm_ticket_request(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        self.remember_member(message.author)
        if message.author.id in self.blocked_users.get(message.guild.id, set()):
            logger.info(
                "Ignored ticket check from blocked user %s in guild %s",
                message.author.id,
                message.guild.id,
            )
            return False
        if message.author.id in self.tracking_opt_outs.get(message.guild.id, set()):
            logger.info(
                "Ignored ticket check from opted-out user %s in guild %s",
                message.author.id,
                message.guild.id,
            )
            return False
        pending = self.purge_pending(message.channel.id)
        pending = [item for item in pending if item.user_id != message.author.id]
        pending.append(
            PendingTicketRequest(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                user_id=message.author.id,
                command_message_id=message.id,
                username=(
                    strip_ticket_nickname_marker(
                        getattr(message.author, "display_name", message.author.name)
                    )
                    or getattr(message.author, "display_name", message.author.name)
                ),
                account_username=getattr(message.author, "name", ""),
                identity_tokens=identity_tokens_for_user(message.author),
                created_at=time.monotonic(),
            )
        )
        self.pending_by_channel[message.channel.id] = pending
        logger.info(
            "Armed boss-ticket capture for user %s in guild %s",
            message.author.id,
            message.guild.id,
        )
        return True

    def match_pending_request_data(
        self,
        *,
        guild_id: int,
        channel_id: int,
        text: str,
        reference_id: int | None = None,
        mentioned_ids: set[int] | None = None,
    ) -> PendingTicketRequest | None:
        pending = [
            item
            for item in self.purge_pending(channel_id)
            if item.guild_id == guild_id
        ]
        if not pending:
            return None

        if reference_id is not None:
            for item in pending:
                if item.command_message_id == reference_id:
                    return item

        for item in pending:
            if item.user_id in (mentioned_ids or set()):
                return item

        normalized_text = re.sub(r"[^a-z0-9_]", "", text.lower())
        identified = [
            item
            for item in pending
            if any(token in normalized_text for token in item.identity_tokens)
        ]
        if identified:
            return min(identified, key=lambda item: item.created_at)

        # OwO normally answers ticket checks in channel order. FIFO remains a safe
        # final fallback when the response contains a decorated nickname.
        return min(pending, key=lambda item: item.created_at)

    def match_pending_request(
        self, message: discord.Message, text: str
    ) -> PendingTicketRequest | None:
        reference_id = (
            message.reference.message_id if message.reference is not None else None
        )
        return self.match_pending_request_data(
            guild_id=message.guild.id if message.guild else 0,
            channel_id=message.channel.id,
            text=text,
            reference_id=reference_id,
            mentioned_ids={user.id for user in message.mentions},
        )

    def response_already_processed(self, guild_id: int, message_id: int) -> bool:
        now = time.monotonic()
        self.processed_responses = {
            key: seen_at
            for key, seen_at in self.processed_responses.items()
            if now - seen_at <= 120
        }
        key = (guild_id, message_id)
        if key in self.processed_responses:
            return True
        self.processed_responses[key] = now
        return False

    def consume_pending(self, request: PendingTicketRequest) -> None:
        pending = self.pending_by_channel.get(request.channel_id, [])
        remaining = [item for item in pending if item != request]
        if remaining:
            self.pending_by_channel[request.channel_id] = remaining
        else:
            self.pending_by_channel.pop(request.channel_id, None)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._restored:
            return
        self._restored = True
        await ensure_ui_emojis(self.bot)
        self.startup_task = asyncio.create_task(self.restore_startup_state())

    async def restore_startup_state(self) -> None:
        try:
            changed_guilds = await self.store.reset_all_for_current_cycle()
            configured = await self.store.list_configured_guilds()
            for index, guild_id in enumerate(configured):
                self.queue_board_refresh(guild_id)
                if await self.store.nickname_markers_enabled(guild_id):
                    # Remove markers inherited from the old implicit-opt-in behavior,
                    # then reapply only preferences that were explicitly enabled.
                    self.queue_nickname_job(guild_id, "cleanup")
                    self.queue_nickname_job(guild_id, "sync")
                if index + 1 < len(configured):
                    await asyncio.sleep(STARTUP_QUEUE_DELAY_SECONDS)
            configured_ids = set(configured)
            for guild_id in changed_guilds:
                self.invalidate_board_cache(guild_id)
                if (
                    guild_id not in configured_ids
                    and await self.store.nickname_markers_enabled(guild_id)
                ):
                    self.queue_nickname_job(guild_id, "sync")
            if changed_guilds:
                logger.info(
                    "Replenished stale boss-ticket entries for %s guild(s)",
                    len(changed_guilds),
                )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Could not restore boss-ticket state after startup")

    async def record_ticket_response(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        text: str,
        reference_id: int | None = None,
        mentioned_ids: set[int] | None = None,
    ) -> bool:
        tickets = parse_ticket_count(text)
        if tickets is None:
            return False

        request = self.match_pending_request_data(
            guild_id=guild_id,
            channel_id=channel_id,
            text=text,
            reference_id=reference_id,
            mentioned_ids=mentioned_ids,
        )
        if request is None:
            logger.info(
                "Readable boss-ticket response %s had no pending request in guild %s",
                message_id,
                guild_id,
            )
            return False
        if request.user_id in self.blocked_users.get(request.guild_id, set()):
            self.consume_pending(request)
            logger.info(
                "Ignored completed ticket response from blocked user %s in guild %s",
                request.user_id,
                request.guild_id,
            )
            return True
        if request.user_id in self.tracking_opt_outs.get(request.guild_id, set()):
            self.consume_pending(request)
            logger.info(
                "Ignored completed ticket response from opted-out user %s in guild %s",
                request.user_id,
                request.guild_id,
            )
            return True
        if self.response_already_processed(guild_id, message_id):
            return True

        self.consume_pending(request)
        await self.store.upsert_status(
            request.guild_id,
            request.user_id,
            request.username,
            request.account_username,
            tickets,
        )
        self.invalidate_board_cache(request.guild_id)
        if await self.store.get_config(request.guild_id) is not None:
            self.queue_board_refresh(request.guild_id)
        logger.info(
            "Updated boss tickets for user %s in guild %s: %s/3",
            request.user_id,
            request.guild_id,
            tickets,
        )
        nickname_result = await self.apply_ticket_nickname(
            request.guild_id,
            request.user_id,
            tickets,
        )
        if nickname_result not in {"disabled", "unchanged", "opted-out"}:
            logger.info(
                "Ticket nickname result for user %s in guild %s: %s",
                request.user_id,
                request.guild_id,
                nickname_result,
            )
        await self.add_ticket_command_marker_reaction(request, nickname_result)
        # The sticky-board refresh was already queued immediately after the DB write.
        return True

    async def capture_ticket_response_with_retries(
        self, message: discord.Message
    ) -> bool:
        """Read one explicitly awaited OwO ticket response.

        OwO can create the application message before its final Components V2
        text is available. We therefore inspect the gateway object immediately,
        then retry only this one response message for a few seconds.
        """
        if message.guild is None:
            return False

        last_text = extract_message_text(message)
        if parse_ticket_count(last_text) is not None:
            return await self.record_ticket_response(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                message_id=message.id,
                text=last_text,
                reference_id=(
                    message.reference.message_id
                    if message.reference is not None
                    else None
                ),
                mentioned_ids={user.id for user in message.mentions},
            )

        raw: dict[str, Any] | None = None
        for delay in TICKET_CAPTURE_RETRY_DELAYS:
            if not self.purge_pending(message.channel.id):
                return False
            await asyncio.sleep(delay)
            raw = await fetch_raw_message(self.bot, message.channel.id, message.id)
            if raw is None:
                continue
            last_text = extract_raw_text(raw)
            if parse_ticket_count(last_text) is None:
                continue

            reference = raw.get("message_reference") or {}
            reference_id = int(reference.get("message_id", 0) or 0) or None
            mentioned_ids = {
                int(item.get("id", 0))
                for item in raw.get("mentions", [])
                if int(item.get("id", 0) or 0)
            }
            return await self.record_ticket_response(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                message_id=message.id,
                text=last_text,
                reference_id=reference_id,
                mentioned_ids=mentioned_ids,
            )

        preview = normalize_ticket_response_text(last_text)[:500]
        raw_keys = sorted(raw.keys()) if isinstance(raw, dict) else []
        logger.warning(
            "Could not read ticket count from awaited OwO response %s in guild %s; "
            "extracted=%r raw_keys=%s",
            message.id,
            message.guild.id,
            preview,
            raw_keys,
        )
        return False

    def schedule_ticket_response_capture(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        key = (message.guild.id, message.id)
        existing = self.capture_tasks.get(key)
        if existing and not existing.done():
            return

        task = asyncio.create_task(self.capture_ticket_response_with_retries(message))
        self.capture_tasks[key] = task

        def cleanup(done: asyncio.Task[bool]) -> None:
            self.capture_tasks.pop(key, None)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "Unhandled delayed boss-ticket capture for message %s", message.id
                )

        task.add_done_callback(cleanup)

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        try:
            if (
                payload.guild_id is None
                or self.bot.user is None
                or payload.user_id == self.bot.user.id
            ):
                return
            emoji = str(payload.emoji)
            if emoji not in {NICKNAME_SHOW_EMOJI, NICKNAME_HIDE_EMOJI}:
                return
            self.purge_reaction_controls()
            key = (payload.guild_id, payload.channel_id, payload.message_id)
            control = self.reaction_controls.get(key)
            if (
                control is None
                or control.user_id != payload.user_id
                or control.emoji != emoji
            ):
                # Ignore unrelated uses of the same common emoji without fetching the
                # message. This keeps reaction shortcuts cheap in large servers.
                return
            channel = await self.get_text_channel(payload.channel_id)
            if channel is None:
                return
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                self.reaction_controls.pop(key, None)
                return
            except (discord.Forbidden, discord.HTTPException):
                return
            bot_added_action = any(
                str(item.emoji) == emoji and item.me for item in message.reactions
            )
            if not bot_added_action:
                return
            if (
                message.author.id != control.user_id
                or not is_ticket_command(message.content or "")
            ):
                self.reaction_controls.pop(key, None)
                return
            actor = payload.member or discord.Object(id=payload.user_id)
            if not await self.store.nickname_markers_enabled(payload.guild_id):
                for target in (actor, self.bot.user):
                    try:
                        await message.remove_reaction(payload.emoji, target)
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                        pass
                self.reaction_controls.pop(key, None)
                return

            if payload.member is not None:
                self.remember_member(payload.member)
            desired = emoji == NICKNAME_SHOW_EMOJI
            current = await self.store.nickname_marker_allowed(
                payload.guild_id, payload.user_id
            )
            if desired != current:
                notice = await self.set_personal_nickname_preference(
                    payload.guild_id,
                    payload.user_id,
                    desired,
                )
            else:
                notice = "Preference was already in the requested state."

            try:
                await message.remove_reaction(payload.emoji, actor)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            try:
                await message.remove_reaction(payload.emoji, self.bot.user)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            next_emoji = NICKNAME_HIDE_EMOJI if desired else NICKNAME_SHOW_EMOJI
            try:
                await message.add_reaction(next_emoji)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            self.reaction_controls[key] = TicketReactionControl(
                guild_id=control.guild_id,
                channel_id=control.channel_id,
                message_id=control.message_id,
                user_id=control.user_id,
                emoji=next_emoji,
                created_at=time.monotonic(),
            )
            logger.info(
                "User %s toggled ticket nickname marker to %s in guild %s via reaction: %s",
                payload.user_id,
                desired,
                payload.guild_id,
                notice,
            )
        except Exception:
            logger.exception("Unhandled ticket nickname reaction toggle")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            if message.guild is None:
                return
            if not message.author.bot:
                if is_ticket_nickname_command(message.content or ""):
                    await self.send_personal_nickname_panel_message(message)
                    return
                if is_ticket_settings_command(message.content or ""):
                    await self.send_ticket_settings(message)
                    return
                if is_ticket_list_command(message.content or ""):
                    await self.send_ticket_list(message)
                    return
                if is_ticket_command(message.content or ""):
                    armed = self.arm_ticket_request(message)
                    if (
                        not armed
                        and message.author.id
                        in self.tracking_opt_outs.get(message.guild.id, set())
                    ):
                        try:
                            await message.add_reaction("⏸️")
                        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                            pass
                return

            if message.author.id != OWO_BOT_ID:
                return
            if self.purge_pending(message.channel.id):
                self.schedule_ticket_response_capture(message)
        except Exception:
            logger.exception("Unhandled boss-ticket tracking error")

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        try:
            if payload.guild_id is None or not self.purge_pending(payload.channel_id):
                return
            data = dict(payload.data)
            author_id = int((data.get("author") or {}).get("id", 0) or 0)
            text = extract_raw_text(data)

            if author_id != OWO_BOT_ID or parse_ticket_count(text) is None:
                raw = await fetch_raw_message(
                    self.bot, payload.channel_id, payload.message_id
                )
                if raw is None:
                    return
                if int((raw.get("author") or {}).get("id", 0) or 0) != OWO_BOT_ID:
                    return
                data = raw
                text = extract_raw_text(raw)

            if parse_ticket_count(text) is None:
                return

            reference = data.get("message_reference") or {}
            reference_id = int(reference.get("message_id", 0) or 0) or None
            mentioned_ids = {
                int(item.get("id", 0))
                for item in data.get("mentions", [])
                if int(item.get("id", 0) or 0)
            }
            await self.record_ticket_response(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                message_id=payload.message_id,
                text=text,
                reference_id=reference_id,
                mentioned_ids=mentioned_ids,
            )
        except Exception:
            logger.exception("Unhandled edited boss-ticket response")

    @app_commands.command(
        name="boss-ticket-channel",
        description="Choose the channel for the server boss-ticket board.",
    )
    @app_commands.describe(channel="Channel where the ticket list should be maintained")
    @app_commands.default_permissions(manage_guild=True)
    async def boss_ticket_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        old_config = await self.store.get_config(interaction.guild_id)
        if old_config:
            await self.delete_old_board(old_config[0], old_config[1])

        await self.store.set_channel(interaction.guild_id, channel.id)
        await self.refresh_board(interaction.guild_id)
        await interaction.followup.send(
            f"✅ Boss-ticket updates will be maintained in {channel.mention}. Users can "
            "refresh their entry with `owo boss t`, `owo boss ticket`, `w boss t`, or "
            "`w boss ticket` anywhere I can read messages.",
            ephemeral=True,
        )
        logger.info(
            "Configured boss-ticket board channel %s for guild %s",
            channel.id,
            interaction.guild_id,
        )

    @app_commands.command(
        name="boss-ticket-nickname",
        description="Choose whether your ticket count appears in your server nickname.",
    )
    async def boss_ticket_nickname(self, interaction: discord.Interaction) -> None:
        await self.send_personal_nickname_panel(interaction)
        logger.info(
            "Personal ticket nickname panel opened in guild %s by %s",
            interaction.guild_id,
            interaction.user.id,
        )

    @app_commands.command(
        name="boss-ticket-manage",
        description="Open the visual boss-ticket user management panel.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def boss_ticket_manage(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return
        if not self.has_management_permission(interaction.user):
            await interaction.response.send_message(
                "You need **Manage Server** permission to use this panel.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.send_management_panel(
            guild_id=interaction.guild_id,
            send=interaction.followup.send,
            ephemeral=True,
        )
        logger.info(
            "Ticket management panel opened in guild %s by %s",
            interaction.guild_id,
            interaction.user.id,
        )

    @app_commands.command(
        name="boss-ticket-remove",
        description="Remove a user from this server's boss-ticket board.",
    )
    @app_commands.describe(
        user_id="Discord user ID or mention shown on the ticket board"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def boss_ticket_remove(
        self,
        interaction: discord.Interaction,
        user_id: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return

        match = USER_ID_RE.fullmatch(user_id.strip())
        if match is None:
            await interaction.response.send_message(
                "Enter the Discord user ID shown on the ticket board, or paste a user mention.",
                ephemeral=True,
            )
            return

        target_id = int(match.group(1) or match.group(2))
        await interaction.response.defer(ephemeral=True)
        removed = await self.store.remove_status(interaction.guild_id, target_id)
        self.invalidate_board_cache(interaction.guild_id)
        self.drop_pending_for_user(interaction.guild_id, target_id)
        await self.clear_ticket_nickname(
            interaction.guild_id,
            target_id,
            reason="Boss-ticket entry removed",
        )
        if removed is None:
            await interaction.followup.send(
                f"No ticket entry exists for `{target_id}` in this server.",
                ephemeral=True,
            )
            return

        if await self.store.get_config(interaction.guild_id) is not None:
            self.queue_board_refresh(interaction.guild_id)

        await interaction.followup.send(
            f"🗑️ Removed **{discord.utils.escape_markdown(removed.username)}** "
            f"(`{removed.user_id}`) from the boss-ticket board.",
            ephemeral=True,
        )
        logger.info(
            "Removed boss-ticket entry for user %s from guild %s by admin %s",
            removed.user_id,
            interaction.guild_id,
            interaction.user.id,
        )

    @app_commands.command(
        name="boss-ticket-list",
        description="Show the current server boss-ticket list.",
    )
    async def boss_ticket_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        statuses = await self.get_board_statuses(interaction.guild_id)
        page_count = self.board_page_count(statuses)
        await interaction.followup.send(
            embed=self.build_board_embed(statuses, 0),
            view=TicketBoardView(self, page=0, page_count=page_count),
            allowed_mentions=discord.AllowedMentions.none(),
            ephemeral=True,
        )
        logger.info(
            "Slash ticket list requested in guild %s (%s entries)",
            interaction.guild_id,
            len(statuses),
        )

    async def send_ticket_list(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        config = await self.store.get_config(message.guild.id)
        statuses = await self.get_board_statuses(message.guild.id)
        page_count = self.board_page_count(statuses)
        await message.reply(
            embed=self.build_board_embed(statuses, 0),
            view=TicketBoardView(self, page=0, page_count=page_count),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if config is None:
            await message.channel.send(
                "ℹ️ The list is shown here, but no persistent ticket-board channel is "
                "configured yet. A server manager can use `/boss-ticket-channel`."
            )
        logger.info(
            "Prefix ticket list requested by %s in guild %s (%s entries)",
            message.author.id,
            message.guild.id,
            len(statuses),
        )

    async def send_ticket_settings(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if not self.has_management_permission(message.author):
            await message.reply(
                "You need **Manage Server** permission to manage ticket users.",
                mention_author=False,
            )
            return
        await self.send_management_panel(
            guild_id=message.guild.id,
            send=lambda **kwargs: message.reply(
                mention_author=False,
                delete_after=600,
                **kwargs,
            ),
        )
        logger.info(
            "Prefix ticket management panel opened by %s in guild %s",
            message.author.id,
            message.guild.id,
        )

    async def get_text_channel(self, channel_id: int) -> discord.TextChannel | None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def delete_old_board(self, channel_id: int, message_ids: list[int]) -> None:
        channel = await self.get_text_channel(channel_id)
        if channel is None:
            return
        for message_id in message_ids:
            try:
                message = await channel.fetch_message(message_id)
                if self.bot.user and message.author.id == self.bot.user.id:
                    await message.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                continue

    def board_page_count(self, statuses: list[TicketStatus]) -> int:
        return max(1, (len(statuses) + BOARD_PAGE_SIZE - 1) // BOARD_PAGE_SIZE)

    def page_from_message(self, message: discord.Message | None) -> int:
        if message is None or not message.embeds:
            return 0
        title = message.embeds[0].title or ""
        match = re.search(r"Page\s+(\d+)\s*/\s*(\d+)", title, re.IGNORECASE)
        return max(0, int(match.group(1)) - 1) if match else 0

    def board_mention_mode_from_message(
        self, message: discord.Message | None
    ) -> bool:
        if message is None or not message.embeds:
            return False
        title = message.embeds[0].title or ""
        return "Ping view" in title

    def ticket_icons(self, tickets: int) -> str:
        tickets = max(0, min(MAX_TICKETS, tickets))
        available = ui_emoji_text(self.bot, "ticket_available", "🎟️")
        used = ui_emoji_text(self.bot, "ticket_used", "▫️")
        return "".join(
            [available] * tickets + [used] * (MAX_TICKETS - tickets)
        )

    def build_board_embed(
        self,
        statuses: list[TicketStatus],
        page: int = 0,
        *,
        mention_mode: bool = False,
    ) -> discord.Embed:
        next_reset = next_pacific_reset_timestamp()
        page_count = self.board_page_count(statuses)
        page = max(0, min(page, page_count - 1))
        start = page * BOARD_PAGE_SIZE
        current = statuses[start:start + BOARD_PAGE_SIZE]

        counts = {number: 0 for number in range(4)}
        for status in statuses:
            counts[status.tickets] = counts.get(status.tickets, 0) + 1
        summary = " • ".join(
            f"**{number}/3:** {counts[number]}" for number in (3, 2, 1, 0)
        )

        if current:
            lines: list[str] = []
            for status in current:
                ticket_icons = self.ticket_icons(status.tickets)
                account_name = (
                    f"@{status.account_username}"
                    if status.account_username
                    else "@unknown"
                )
                if mention_mode:
                    identity = f"<@{status.user_id}> · `{account_name}`"
                    trailing = f"<t:{status.updated_at}:R>"
                else:
                    safe_name = discord.utils.escape_markdown(status.username)
                    identity = f"**{safe_name}** · `{account_name}`"
                    trailing = f"`{status.user_id}` · <t:{status.updated_at}:R>"
                lines.append(
                    f"{ticket_icons} **{status.tickets}/3** · {identity} · {trailing}"
                )
            body = "\n".join(lines)
        else:
            body = (
                "No ticket checks are currently listed. Run `owo boss t` or "
                "`w boss t` to add your current count."
            )

        title_icon = ui_emoji_text(self.bot, "ticket_available", "🎟️")
        title = f"{title_icon} Guild Boss Tickets"
        if page_count > 1:
            title += f" · Page {page + 1}/{page_count}"
        title += " · Ping view" if mention_mode else " · Text view"
        embed = discord.Embed(
            title=title,
            description=(
                f"{summary}\n\n{body}\n\n"
                f"**Next refill:** <t:{next_reset}:R> · <t:{next_reset}:F>"
            ),
            color=0x5865F2,
        )
        embed.set_footer(
            text=(
                "Pages use cached ticket data for fast switching. My settings controls "
                "your opt-in nickname marker, board entry, and tracking preference."
            )
        )
        return embed

    async def refresh_board(self, guild_id: int) -> None:
        """Replace the configured board with a fresh message set.

        Discord message editing has been inconsistent for this Components-heavy
        workflow. Sending the new board first and deleting the previous board keeps
        the configured channel clean while guaranteeing that the visible list is new.
        """
        started = time.monotonic()
        async with self.board_lock(guild_id):
            config = await self.store.get_config(guild_id)
            if config is None:
                return
            channel_id, stored_ids = config
            channel = await self.get_text_channel(channel_id)
            if channel is None:
                logger.warning("Ticket board channel for guild %s is unavailable", guild_id)
                return

            await self.store.normalize_guild_cycle(guild_id)
            statuses = await self.get_board_statuses(guild_id, force=True)
            page_count = self.board_page_count(statuses)

            try:
                new_message = await channel.send(
                    embed=self.build_board_embed(statuses, 0),
                    view=TicketBoardView(self, page=0, page_count=page_count),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                logger.warning(
                    "Could not send replacement ticket board for guild %s: %s",
                    guild_id,
                    exc,
                )
                return

            new_ids = [new_message.id]
            await self.store.set_board_message_ids(guild_id, new_ids)

            # Delete every previous page only after the replacement is safely visible.
            for old_id in stored_ids:
                if old_id == new_message.id:
                    continue
                try:
                    await channel.get_partial_message(old_id).delete()
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    continue

            logger.info(
                "Replaced boss-ticket board for guild %s: %s entries in one paginated message (%s page(s)) in %.3fs",
                guild_id,
                len(statuses),
                page_count,
                time.monotonic() - started,
            )

    async def daily_reset_loop(self) -> None:
        try:
            await self.bot.wait_until_ready()
            while not self.bot.is_closed():
                reset_at = next_pacific_reset_timestamp()
                await asyncio.sleep(max(1, reset_at - time.time() + 1))
                changed_guilds = await self.store.reset_all_for_current_cycle()
                configured = await self.store.list_configured_guilds()
                for index, guild_id in enumerate(configured):
                    self.invalidate_board_cache(guild_id)
                    self.queue_board_refresh(guild_id)
                    if index + 1 < len(configured):
                        await asyncio.sleep(STARTUP_QUEUE_DELAY_SECONDS)
                for guild_id in changed_guilds:
                    if await self.store.nickname_markers_enabled(guild_id):
                        self.queue_nickname_job(guild_id, "sync")
                logger.info(
                    "Replenished Pacific-midnight boss-ticket entries; %s guild(s) changed",
                    len(changed_guilds),
                )
        except asyncio.CancelledError:
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketTracker(bot))
