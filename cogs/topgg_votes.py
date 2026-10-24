"""Top.gg voting link, vote history, metrics, and opt-in DM reminders."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from .helper_prefix import get_guild_helper_prefix, parse_helper_command_argument
from .message_utils import safe_reply

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "topgg_votes.db"
TOPGG_BOT_ID = "1516054547456458952"
TOPGG_VOTE_URL = f"https://top.gg/bot/{TOPGG_BOT_ID}/vote"
TOPGG_API_BASE_URL = "https://top.gg/api/v1"
VOTE_COMMAND_ALIASES = frozenset({"h vote", "hvote", "hv"})
VOTE_SYNC_OVERLAP_SECONDS = 5 * 60
TOPGG_HISTORY_START = int(datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp())
MAX_VOTE_PAGES = 100


def parse_topgg_timestamp(value: object) -> int:
    """Parse an API timestamp into a UTC Unix timestamp."""
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def positive_int(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


@dataclass(frozen=True)
class VoteSummary:
    votes: int
    points: int
    reminders_enabled: bool
    latest_vote_at: int | None
    next_vote_at: int | None


@dataclass(frozen=True)
class DueReminder:
    event_id: str
    user_id: int
    voted_at: int
    expires_at: int


class VoteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    async def initialize(self) -> None:
        async with self.lock:
            await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS topgg_vote_events (
                    event_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    voted_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    weight INTEGER NOT NULL DEFAULT 1,
                    reminded_at INTEGER
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_topgg_vote_user "
                "ON topgg_vote_events(user_id, voted_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS topgg_vote_profiles (
                    user_id INTEGER PRIMARY KEY,
                    reminders_enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS topgg_vote_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    async def record_vote(
        self,
        *,
        user_id: int,
        voted_at: int,
        expires_at: int,
        weight: int,
    ) -> bool:
        async with self.lock:
            return await asyncio.to_thread(
                self._record_vote_sync,
                user_id,
                voted_at,
                expires_at,
                weight,
            )

    def _record_vote_sync(
        self,
        user_id: int,
        voted_at: int,
        expires_at: int,
        weight: int,
    ) -> bool:
        event_id = f"{int(user_id)}:{int(voted_at)}"
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO topgg_vote_events (
                    event_id, user_id, voted_at, expires_at, weight
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    int(user_id),
                    int(voted_at),
                    int(expires_at),
                    max(1, int(weight or 1)),
                ),
            )
            return cursor.rowcount > 0

    async def summary(self, user_id: int) -> VoteSummary:
        async with self.lock:
            return await asyncio.to_thread(self._summary_sync, user_id)

    def _summary_sync(self, user_id: int) -> VoteSummary:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS votes,
                       COALESCE(SUM(weight), 0) AS points,
                       MAX(voted_at) AS latest_vote_at,
                       MAX(expires_at) AS next_vote_at
                FROM topgg_vote_events
                WHERE user_id = ?
                """,
                (int(user_id),),
            ).fetchone()
            profile = connection.execute(
                "SELECT reminders_enabled FROM topgg_vote_profiles WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        return VoteSummary(
            votes=int(row["votes"] or 0),
            points=int(row["points"] or 0),
            reminders_enabled=bool(profile and profile["reminders_enabled"]),
            latest_vote_at=int(row["latest_vote_at"]) if row["latest_vote_at"] else None,
            next_vote_at=int(row["next_vote_at"]) if row["next_vote_at"] else None,
        )

    async def set_reminders(self, user_id: int, enabled: bool) -> VoteSummary:
        now = int(time.time())
        async with self.lock:
            await asyncio.to_thread(self._set_reminders_sync, user_id, enabled, now)
        return await self.summary(user_id)

    def _set_reminders_sync(self, user_id: int, enabled: bool, now: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO topgg_vote_profiles (user_id, reminders_enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    reminders_enabled = excluded.reminders_enabled,
                    updated_at = excluded.updated_at
                """,
                (int(user_id), int(enabled), int(now)),
            )
            if enabled:
                # Enabling reminders should not immediately send notices for votes
                # that had already expired before the member opted in.
                connection.execute(
                    """
                    UPDATE topgg_vote_events
                    SET reminded_at = COALESCE(reminded_at, ?)
                    WHERE user_id = ? AND expires_at <= ?
                    """,
                    (int(now), int(user_id), int(now)),
                )

    async def due_reminders(self, now: int | None = None) -> list[DueReminder]:
        async with self.lock:
            return await asyncio.to_thread(self._due_reminders_sync, int(now or time.time()))

    def _due_reminders_sync(self, now: int) -> list[DueReminder]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT events.event_id, events.user_id, events.voted_at, events.expires_at
                FROM topgg_vote_events AS events
                JOIN topgg_vote_profiles AS profiles USING (user_id)
                WHERE profiles.reminders_enabled = 1
                  AND events.reminded_at IS NULL
                  AND events.expires_at <= ?
                  AND events.event_id = (
                      SELECT newest.event_id
                      FROM topgg_vote_events AS newest
                      WHERE newest.user_id = events.user_id
                        AND newest.reminded_at IS NULL
                        AND newest.expires_at <= ?
                      ORDER BY newest.expires_at DESC, newest.voted_at DESC
                      LIMIT 1
                  )
                ORDER BY events.expires_at ASC
                LIMIT 100
                """,
                (int(now), int(now)),
            ).fetchall()
        return [
            DueReminder(
                event_id=str(row["event_id"]),
                user_id=int(row["user_id"]),
                voted_at=int(row["voted_at"]),
                expires_at=int(row["expires_at"]),
            )
            for row in rows
        ]

    async def mark_reminded(self, event_id: str, at: int | None = None) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._mark_reminded_sync, event_id, int(at or time.time())
            )

    def _mark_reminded_sync(self, event_id: str, at: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE topgg_vote_events SET reminded_at = ? WHERE event_id = ?",
                (int(at), str(event_id)),
            )

    async def mark_due_reminders(
        self, user_id: int, through_expires_at: int, at: int | None = None
    ) -> None:
        async with self.lock:
            await asyncio.to_thread(
                self._mark_due_reminders_sync,
                int(user_id),
                int(through_expires_at),
                int(at or time.time()),
            )

    def _mark_due_reminders_sync(
        self, user_id: int, through_expires_at: int, at: int
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE topgg_vote_events
                SET reminded_at = ?
                WHERE user_id = ? AND reminded_at IS NULL AND expires_at <= ?
                """,
                (int(at), int(user_id), int(through_expires_at)),
            )

    async def get_last_sync(self) -> int | None:
        async with self.lock:
            return await asyncio.to_thread(self._get_last_sync_sync)

    def _get_last_sync_sync(self) -> int | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM topgg_vote_metadata WHERE key = 'last_successful_sync'"
            ).fetchone()
        if not row:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    async def set_last_sync(self, value: int) -> None:
        async with self.lock:
            await asyncio.to_thread(self._set_last_sync_sync, int(value))

    def _set_last_sync_sync(self, value: int) -> None:
        now = int(time.time())
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO topgg_vote_metadata (key, value, updated_at)
                VALUES ('last_successful_sync', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(int(value)), now),
            )


class VotePanelView(discord.ui.View):
    def __init__(
        self,
        cog: "TopGGVotes",
        owner_id: int,
        enabled: bool,
        helper_prefix: str,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.helper_prefix = helper_prefix
        self.add_item(
            discord.ui.Button(
                label="Vote on Top.gg",
                emoji="🗳️",
                style=discord.ButtonStyle.link,
                url=TOPGG_VOTE_URL,
            )
        )
        toggle = discord.ui.Button(
            label="Turn off reminders" if enabled else "Remind me in 12 hours",
            emoji="🔕" if enabled else "🔔",
            style=(discord.ButtonStyle.secondary if enabled else discord.ButtonStyle.success),
            custom_id="topgg_vote:panel_toggle",
        )
        toggle.callback = self.toggle_reminders
        self.add_item(toggle)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Open your own vote panel with `/vote` or this server's vote command.",
            ephemeral=True,
        )
        return False

    async def toggle_reminders(self, interaction: discord.Interaction) -> None:
        current = await self.cog.store.summary(interaction.user.id)
        updated = await self.cog.store.set_reminders(
            interaction.user.id, not current.reminders_enabled
        )
        await interaction.response.edit_message(
            embed=self.cog.build_vote_embed(updated, self.helper_prefix),
            view=VotePanelView(
                self.cog,
                interaction.user.id,
                updated.reminders_enabled,
                self.helper_prefix,
            ),
        )


class VoteReminderDMView(discord.ui.View):
    def __init__(self, cog: "TopGGVotes") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(
            discord.ui.Button(
                label="Vote on Top.gg",
                emoji="🗳️",
                style=discord.ButtonStyle.link,
                url=TOPGG_VOTE_URL,
            )
        )

    @discord.ui.button(
        label="Turn off vote reminders",
        emoji="🔕",
        style=discord.ButtonStyle.secondary,
        custom_id="topgg_vote:disable_reminders",
    )
    async def disable_reminders(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.cog.store.set_reminders(interaction.user.id, False)
        await interaction.response.edit_message(
            content="🔕 Top.gg vote reminders are now off. Voting remains optional.",
            embed=None,
            view=None,
        )


class TopGGVotes(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = VoteStore(DATABASE_FILE)
        self.api_token = os.getenv("TOPGG_TOKEN", "").strip()
        self.http_session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        await self.store.initialize()
        self.bot.add_view(VoteReminderDMView(self))
        self.reminder_loop.start()
        if self.api_token:
            self.vote_sync_loop.start()
            self.metrics_loop.start()
        else:
            logger.info(
                "TOPGG_TOKEN is not configured; vote link commands are active, "
                "but vote syncing and Top.gg metrics are paused"
            )
        logger.info("Top.gg vote storage ready at %s", DATABASE_FILE)

    def cog_unload(self) -> None:
        self.reminder_loop.cancel()
        if self.vote_sync_loop.is_running():
            self.vote_sync_loop.cancel()
        if self.metrics_loop.is_running():
            self.metrics_loop.cancel()
        if self.http_session and not self.http_session.closed:
            asyncio.create_task(self.http_session.close())

    async def _session(self) -> aiohttp.ClientSession:
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self.http_session

    async def api_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        session = await self._session()
        headers = {"Authorization": f"Bearer {self.api_token}"}
        async with session.request(
            method,
            f"{TOPGG_API_BASE_URL}{path}",
            headers=headers,
            json=payload,
        ) as response:
            body = await response.text()
            if not 200 <= response.status < 300:
                raise RuntimeError(
                    f"Top.gg API returned HTTP {response.status}: {body[:300]}"
                )
            if not body:
                return {}
            parsed = await response.json(content_type=None)
            return parsed if isinstance(parsed, dict) else {}

    async def sync_votes_once(self) -> int:
        now = int(time.time())
        last_sync = await self.store.get_last_sync()
        since = (
            max(0, last_sync - VOTE_SYNC_OVERLAP_SECONDS)
            if last_sync is not None
            else TOPGG_HISTORY_START
        )
        start_date = datetime.fromtimestamp(since, tz=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        path = f"/projects/@me/votes?startDate={quote(start_date, safe='')}"
        inserted = 0
        pages = 0
        while path and pages < MAX_VOTE_PAGES:
            response = await self.api_request("GET", path)
            pages += 1
            for item in response.get("data") or []:
                if not isinstance(item, dict):
                    continue
                user_id_text = str(item.get("platform_id") or "").strip()
                if not user_id_text.isdigit():
                    continue
                voted_at = parse_topgg_timestamp(item.get("created_at"))
                expires_at = parse_topgg_timestamp(item.get("expires_at"))
                if not voted_at or not expires_at:
                    continue
                if await self.store.record_vote(
                    user_id=int(user_id_text),
                    voted_at=voted_at,
                    expires_at=expires_at,
                    weight=positive_int(item.get("weight")),
                ):
                    inserted += 1
            cursor = str(response.get("cursor") or "").strip()
            path = f"/projects/@me/votes?cursor={quote(cursor, safe='')}" if cursor else ""
        if pages >= MAX_VOTE_PAGES and path:
            raise RuntimeError("Top.gg vote pagination exceeded the safety limit")
        await self.store.set_last_sync(now)
        return inserted

    async def post_metrics_once(self) -> None:
        await self.api_request(
            "PATCH",
            "/projects/@me/metrics",
            payload={"server_count": len(self.bot.guilds)},
        )

    @tasks.loop(minutes=5)
    async def vote_sync_loop(self) -> None:
        try:
            inserted = await self.sync_votes_once()
            if inserted:
                logger.info("Recorded %s new Top.gg vote event(s)", inserted)
        except Exception:
            logger.exception("Top.gg vote synchronization failed")

    @vote_sync_loop.before_loop
    async def before_vote_sync(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def metrics_loop(self) -> None:
        try:
            await self.post_metrics_once()
            logger.info("Posted Top.gg metric: %s servers", len(self.bot.guilds))
        except Exception:
            logger.exception("Top.gg metric update failed")

    @metrics_loop.before_loop
    async def before_metrics(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def reminder_loop(self) -> None:
        for reminder in await self.store.due_reminders():
            try:
                user = self.bot.get_user(reminder.user_id) or await self.bot.fetch_user(
                    reminder.user_id
                )
                await user.send(
                    "🗳️ **You can vote for OwO Boss Helper again.**\n"
                    "Voting is optional and does not unlock or limit any feature. "
                    "This is the only reminder for your last vote; ignoring it will not "
                    "send repeated reminders. To unsubscribe, press **Turn off vote "
                    "reminders** below.",
                    view=VoteReminderDMView(self),
                )
                logger.info("Sent Top.gg vote reminder to user %s", reminder.user_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                logger.info(
                    "Could not deliver Top.gg vote reminder to user %s",
                    reminder.user_id,
                )
            finally:
                # One reminder per vote, including when DMs are closed. This avoids
                # retry spam and makes ignoring a delivered reminder sufficient.
                await self.store.mark_due_reminders(
                    reminder.user_id, reminder.expires_at
                )

    @reminder_loop.before_loop
    async def before_reminders(self) -> None:
        await self.bot.wait_until_ready()

    def build_vote_embed(
        self, summary: VoteSummary, helper_prefix: str
    ) -> discord.Embed:
        status = "On" if summary.reminders_enabled else "Off"
        embed = discord.Embed(
            title="🗳️ Vote for OwO Boss Helper",
            url=TOPGG_VOTE_URL,
            description=(
                "Voting is completely optional. It does not unlock features or limit "
                "anything in the bot, but it helps more OwO players discover the helper."
            ),
            color=0xFF3366,
        )
        embed.add_field(
            name="Your support",
            value=(
                f"Votes recorded: **{summary.votes:,}**\n"
                f"Vote points: **{summary.points:,}**"
            ),
            inline=True,
        )
        timing = "Vote now on Top.gg."
        now = int(time.time())
        if summary.next_vote_at and summary.next_vote_at > now:
            timing = f"You can vote again <t:{summary.next_vote_at}:R>."
        elif summary.latest_vote_at:
            timing = "Your latest recorded vote has expired, so you can vote again now."
        embed.add_field(name="Availability", value=timing, inline=True)
        embed.add_field(
            name=f"12-hour reminder: {status}",
            value=(
                "When enabled, the bot sends one DM when your latest vote becomes "
                "available again. Ignoring that DM causes no repeat messages. Every "
                "reminder includes a **Turn off** button."
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"You can also use {helper_prefix} vote on / {helper_prefix} vote off. "
                "Reminder preference and vote history are global."
            )
        )
        return embed

    async def send_vote_panel(
        self,
        *,
        user: discord.abc.User,
        helper_prefix: str,
        respond: Any,
    ) -> None:
        summary = await self.store.summary(user.id)
        await respond(
            embed=self.build_vote_embed(summary, helper_prefix),
            view=VotePanelView(
                self, user.id, summary.reminders_enabled, helper_prefix
            ),
        )

    async def handle_vote_argument(
        self, message: discord.Message, helper_prefix: str, argument: str
    ) -> None:
        action = argument.strip().casefold()
        if action in {"on", "enable", "yes"}:
            summary = await self.store.set_reminders(message.author.id, True)
            await safe_reply(
                message,
                "🔔 Top.gg vote reminders are on. I will send one DM when your latest "
                "vote becomes available again. Use the reminder's **Turn off** button "
                f"or `{helper_prefix} vote off` to unsubscribe.",
                embed=self.build_vote_embed(summary, helper_prefix),
                view=VotePanelView(self, message.author.id, True, helper_prefix),
                mention_author=False,
            )
            return
        if action in {"off", "disable", "no", "stop"}:
            summary = await self.store.set_reminders(message.author.id, False)
            await safe_reply(
                message,
                "🔕 Top.gg vote reminders are off. Voting remains optional.",
                embed=self.build_vote_embed(summary, helper_prefix),
                view=VotePanelView(self, message.author.id, False, helper_prefix),
                mention_author=False,
            )
            return
        if action:
            await safe_reply(
                message,
                f"Use `{helper_prefix} vote`, `{helper_prefix} vote on`, or "
                f"`{helper_prefix} vote off`.",
                mention_author=False,
            )
            return
        summary = await self.store.summary(message.author.id)
        await safe_reply(
            message,
            embed=self.build_vote_embed(summary, helper_prefix),
            view=VotePanelView(
                self,
                message.author.id,
                summary.reminders_enabled,
                helper_prefix,
            ),
            mention_author=False,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        helper_prefix = await get_guild_helper_prefix(message.guild.id)
        argument = parse_helper_command_argument(
            message.content or "", helper_prefix, VOTE_COMMAND_ALIASES
        )
        if argument is not None:
            await self.handle_vote_argument(message, helper_prefix, argument)

    @app_commands.command(
        name="vote",
        description="Open the optional Top.gg vote link and manage 12-hour reminders.",
    )
    async def vote_command(self, interaction: discord.Interaction) -> None:
        helper_prefix = await get_guild_helper_prefix(interaction.guild_id)
        summary = await self.store.summary(interaction.user.id)
        await interaction.response.send_message(
            embed=self.build_vote_embed(summary, helper_prefix),
            view=VotePanelView(
                self,
                interaction.user.id,
                summary.reminders_enabled,
                helper_prefix,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TopGGVotes(bot))
