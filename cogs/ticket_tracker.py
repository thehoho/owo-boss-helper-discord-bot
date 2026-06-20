"""Per-guild OwO boss-ticket board with Pacific-midnight resets.

The tracker only records a user's ticket count after that user explicitly runs an
OwO boss-ticket command in a server where the helper is present. It does not infer
usage from battles or from activity in other servers.
"""

from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)

OWO_BOT_ID = 408785106942164992
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "boss_tickets.db"
PACIFIC = ZoneInfo("America/Los_Angeles")
PENDING_REQUEST_SECONDS = 60
BOARD_PAGE_SIZE = 20
MAX_TICKETS = 3

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
    identity_tokens: tuple[str, ...]
    created_at: float


@dataclass(frozen=True)
class TicketStatus:
    guild_id: int
    user_id: int
    username: str
    tickets: int
    updated_at: int
    cycle_date: str


def normalize_ticket_command(content: str) -> str:
    return re.sub(r"\s+", "", content or "").lower()


def is_ticket_command(content: str) -> bool:
    return normalize_ticket_command(content) in TICKET_COMMANDS


def is_ticket_list_command(content: str) -> bool:
    return normalize_ticket_command(content) in TICKET_LIST_COMMANDS


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
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
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
                    tickets INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    cycle_date TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ticket_status_guild "
                "ON ticket_status(guild_id, tickets DESC, username COLLATE NOCASE)"
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
        tickets: int,
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._upsert_status_sync,
                guild_id,
                user_id,
                username,
                tickets,
            )

    def _upsert_status_sync(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        tickets: int,
    ) -> None:
        now = int(time.time())
        cycle = current_pacific_date().isoformat()
        with self._connect() as connection:
            self._normalize_guild_cycle_sync(connection, guild_id, cycle)
            connection.execute(
                """
                INSERT INTO ticket_status
                    (guild_id, user_id, username, tickets, updated_at, cycle_date)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    tickets = excluded.tickets,
                    updated_at = excluded.updated_at,
                    cycle_date = excluded.cycle_date
                """,
                (guild_id, user_id, username[:100], tickets, now, cycle),
            )

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
                SELECT guild_id, user_id, username, tickets, updated_at, cycle_date
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
            tickets=int(row["tickets"]),
            updated_at=int(row["updated_at"]),
            cycle_date=str(row["cycle_date"]),
        )

    async def list_status(self, guild_id: int) -> list[TicketStatus]:
        async with self.lock:
            return await asyncio.to_thread(self._list_status_sync, guild_id)

    def _list_status_sync(self, guild_id: int) -> list[TicketStatus]:
        cycle = current_pacific_date().isoformat()
        with self._connect() as connection:
            self._normalize_guild_cycle_sync(connection, guild_id, cycle)
            rows = connection.execute(
                """
                SELECT guild_id, user_id, username, tickets, updated_at, cycle_date
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
                tickets=int(row["tickets"]),
                updated_at=int(row["updated_at"]),
                cycle_date=str(row["cycle_date"]),
            )
            for row in rows
        ]


class TicketTracker(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = TicketStore(DATABASE_FILE)
        self.pending_by_channel: dict[int, list[PendingTicketRequest]] = {}
        self.board_locks: dict[int, asyncio.Lock] = {}
        self.reset_task: asyncio.Task[None] | None = None
        self.processed_responses: dict[tuple[int, int], float] = {}
        self.capture_tasks: dict[tuple[int, int], asyncio.Task[bool]] = {}
        self._restored = False

    async def cog_load(self) -> None:
        await self.store.initialize()
        self.reset_task = asyncio.create_task(self.daily_reset_loop())
        logger.info("Boss ticket storage ready at %s", DATABASE_FILE)

    async def cog_unload(self) -> None:
        if self.reset_task:
            self.reset_task.cancel()
            self.reset_task = None
        for task in self.capture_tasks.values():
            task.cancel()
        self.capture_tasks.clear()

    def board_lock(self, guild_id: int) -> asyncio.Lock:
        return self.board_locks.setdefault(guild_id, asyncio.Lock())

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

    def arm_ticket_request(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        pending = self.purge_pending(message.channel.id)
        pending = [item for item in pending if item.user_id != message.author.id]
        pending.append(
            PendingTicketRequest(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                user_id=message.author.id,
                command_message_id=message.id,
                username=getattr(message.author, "display_name", message.author.name),
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
        changed_guilds = await self.store.reset_all_for_current_cycle()
        configured = await self.store.list_configured_guilds()
        for guild_id in configured:
            try:
                await self.refresh_board(guild_id)
            except Exception:
                logger.exception("Could not restore ticket board for guild %s", guild_id)
        if changed_guilds:
            logger.info("Replenished stale boss-ticket entries for %s guild(s)", len(changed_guilds))

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
        if self.response_already_processed(guild_id, message_id):
            return True

        self.consume_pending(request)
        await self.store.upsert_status(
            request.guild_id,
            request.user_id,
            request.username,
            tickets,
        )
        logger.info(
            "Updated boss tickets for user %s in guild %s: %s/3",
            request.user_id,
            request.guild_id,
            tickets,
        )
        if await self.store.get_config(request.guild_id) is not None:
            await self.refresh_board(request.guild_id)
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
    async def on_message(self, message: discord.Message) -> None:
        try:
            if message.guild is None:
                return
            if not message.author.bot:
                if is_ticket_list_command(message.content or ""):
                    await self.send_ticket_list(message)
                    return
                if is_ticket_command(message.content or ""):
                    self.arm_ticket_request(message)
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
        if removed is None:
            await interaction.followup.send(
                f"No ticket entry exists for `{target_id}` in this server.",
                ephemeral=True,
            )
            return

        if await self.store.get_config(interaction.guild_id) is not None:
            await self.refresh_board(interaction.guild_id)

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
        statuses = await self.store.list_status(interaction.guild_id)
        embeds = self.build_board_embeds(statuses)
        for index, embed in enumerate(embeds):
            if index == 0:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(
            "Slash ticket list requested in guild %s (%s entries)",
            interaction.guild_id,
            len(statuses),
        )

    async def send_ticket_list(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        config = await self.store.get_config(message.guild.id)
        statuses = await self.store.list_status(message.guild.id)
        embeds = self.build_board_embeds(statuses)
        for index, embed in enumerate(embeds):
            if index == 0:
                await message.reply(embed=embed, mention_author=False)
            else:
                await message.channel.send(embed=embed)
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

    def build_board_embeds(self, statuses: list[TicketStatus]) -> list[discord.Embed]:
        next_reset = next_pacific_reset_timestamp()
        if not statuses:
            pages: list[list[TicketStatus]] = [[]]
        else:
            pages = [
                statuses[index:index + BOARD_PAGE_SIZE]
                for index in range(0, len(statuses), BOARD_PAGE_SIZE)
            ]

        counts = {number: 0 for number in range(4)}
        for status in statuses:
            counts[status.tickets] = counts.get(status.tickets, 0) + 1
        summary = " • ".join(f"**{number}/3:** {counts[number]}" for number in (3, 2, 1, 0))

        embeds: list[discord.Embed] = []
        for page_index, page in enumerate(pages, start=1):
            if page:
                lines = []
                for status in page:
                    safe_name = discord.utils.escape_markdown(status.username)
                    icon = "🎟️" if status.tickets else "▫️"
                    lines.append(
                        f"{icon} **{status.tickets}/3** — **{safe_name}** — "
                        f"`{status.user_id}` — <t:{status.updated_at}:R>"
                    )
                body = "\n".join(lines)
            else:
                body = (
                    "No ticket checks have been recorded yet. Run `owo boss t` or "
                    "`w boss t` anywhere in this server to add your current count."
                )

            title = "🎟️ Guild Boss Tickets"
            if len(pages) > 1:
                title += f" — Page {page_index}/{len(pages)}"
            embed = discord.Embed(
                title=title,
                description=(
                    f"{summary}\n\n{body}\n\n"
                    f"**All tickets replenish:** <t:{next_reset}:R> "
                    f"(<t:{next_reset}:F>)"
                ),
                color=0x5865F2,
            )
            embed.set_footer(
                text=(
                    "Previously tracked members replenish to 3/3 at Pacific midnight. "
                    "Later OwO checks replace that reset value with the real count."
                )
            )
            embeds.append(embed)
        return embeds

    async def refresh_board(self, guild_id: int) -> None:
        """Replace the configured board with a fresh message set.

        Discord message editing has been inconsistent for this Components-heavy
        workflow. Sending the new board first and deleting the previous board keeps
        the configured channel clean while guaranteeing that the visible list is new.
        """
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
            statuses = await self.store.list_status(guild_id)
            embeds = self.build_board_embeds(statuses)
            new_messages: list[discord.Message] = []

            try:
                for embed in embeds:
                    new_messages.append(await channel.send(embed=embed))
            except (discord.Forbidden, discord.HTTPException) as exc:
                logger.warning(
                    "Could not send replacement ticket board for guild %s: %s",
                    guild_id,
                    exc,
                )
                # Do not leave a partial replacement behind when a multi-page send fails.
                for message in new_messages:
                    try:
                        await message.delete()
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                        pass
                return

            new_ids = [message.id for message in new_messages]
            await self.store.set_board_message_ids(guild_id, new_ids)

            # Delete the previous board only after the replacement is safely visible.
            for old_id in stored_ids:
                if old_id in new_ids:
                    continue
                try:
                    await channel.get_partial_message(old_id).delete()
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    continue

            logger.info(
                "Replaced boss-ticket board for guild %s: %s entries across %s page(s)",
                guild_id,
                len(statuses),
                len(new_ids),
            )

    async def daily_reset_loop(self) -> None:
        try:
            await self.bot.wait_until_ready()
            while not self.bot.is_closed():
                reset_at = next_pacific_reset_timestamp()
                await asyncio.sleep(max(1, reset_at - time.time() + 1))
                changed_guilds = await self.store.reset_all_for_current_cycle()
                configured = await self.store.list_configured_guilds()
                for guild_id in configured:
                    try:
                        await self.refresh_board(guild_id)
                    except Exception:
                        logger.exception(
                            "Could not refresh ticket board after daily reset for guild %s",
                            guild_id,
                        )
                logger.info(
                    "Replenished Pacific-midnight boss-ticket entries; %s guild(s) changed",
                    len(changed_guilds),
                )
        except asyncio.CancelledError:
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketTracker(bot))
