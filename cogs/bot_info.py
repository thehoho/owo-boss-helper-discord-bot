"""Public bot information and owner-only operational statistics.

The public `H about` and `/about` commands identify the developer and explain the
project. Owner-only commands expose server reach and usage without relying on log
files. Server metadata and aggregate usage counters are stored locally in SQLite.
"""

from __future__ import annotations

import asyncio
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

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "bot_stats.db"
TEAM_DATABASE_FILE = PROJECT_ROOT / "team_templates.db"
TICKET_DATABASE_FILE = PROJECT_ROOT / "boss_tickets.db"
LOG_FILE = PROJECT_ROOT / "logs" / "bot.log"

BOT_VERSION = "0.10.1-beta"
DEFAULT_DEVELOPER_NAME = "Hassaan"
DEFAULT_GITHUB_URL = "https://github.com/thehoho/owo-boss-helper-discord-bot"
DEFAULT_DESCRIPTION = (
    "OwO Boss Helper makes guild-boss fights easier by generating ordered Neon "
    "commands with live HP, tracking boss cooldowns and tickets, and saving guided "
    "team templates with exact weapon IDs."
)
ABOUT_COMMANDS = {"habout"}
PERIODIC_SYNC_SECONDS = 6 * 60 * 60
SERVERS_PER_PAGE = 10


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


def compact_command(content: str) -> str:
    return re.sub(r"\s+", "", content or "").lower()


def classify_message_usage(content: str) -> str | None:
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
    if compact == "hhelp":
        return "help_views"
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
                    last_used_at INTEGER
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

    async def list_guilds(self) -> list[GuildRecord]:
        async with self.lock:
            return await asyncio.to_thread(self._list_guilds_sync)

    def _list_guilds_sync(self) -> list[GuildRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT guild_id, guild_name, owner_id, member_count, channel_count,
                       joined_at, last_seen_at, left_at, active, usage_count, last_used_at
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


class AboutLinks(discord.ui.View):
    def __init__(self, github_url: str, support_url: str) -> None:
        super().__init__(timeout=120)
        if github_url.startswith("https://"):
            self.add_item(discord.ui.Button(label="Source code", url=github_url))
        if support_url.startswith("https://"):
            self.add_item(discord.ui.Button(label="Support server", url=support_url))


class BotInfo(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = StatsStore(DATABASE_FILE)
        self.started_monotonic = time.monotonic()
        self.periodic_task: asyncio.Task[None] | None = None
        self.restored = False
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

    async def periodic_sync(self) -> None:
        try:
            while True:
                await asyncio.sleep(PERIODIC_SYNC_SECONDS)
                await self.store.sync_guilds(self.bot.guilds)
                logger.info("Refreshed persistent metadata for %s guild(s)", len(self.bot.guilds))
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
                "• Guild-boss timing and cooldown alerts\n"
                "• Exact weapon-ID team templates\n"
                "• Per-server boss-ticket boards"
            ),
            inline=False,
        )
        embed.add_field(name="Developer", value=f"**{self.developer_name}**", inline=True)
        embed.add_field(name="Version", value=f"`{BOT_VERSION}`", inline=True)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(
            name="Get started",
            value="Use `H help` for commands and setup instructions.",
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
        await self.store.sync_guilds(self.bot.guilds)
        logger.info("Recorded %s active guild(s) in bot statistics", len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
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
        logger.info("Bot left guild %s (%s)", guild.name, guild.id)
        await self.notify_owner_about_guild(guild, joined=False)

    async def notify_owner_about_guild(
        self, guild: discord.Guild, *, joined: bool
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        metric = classify_message_usage(message.content or "")
        if metric is not None:
            await self.store.record_usage(message.guild.id, metric)
        if compact_command(message.content or "") in ABOUT_COMMANDS:
            await message.reply(
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
            "help_views": "Help views",
            "about_views": "About views",
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

        page_count = max(1, (len(records) + SERVERS_PER_PAGE - 1) // SERVERS_PER_PAGE)
        selected_page = min(page, page_count)
        start = (selected_page - 1) * SERVERS_PER_PAGE
        selected = records[start:start + SERVERS_PER_PAGE]

        lines: list[str] = []
        for index, record in enumerate(selected, start=start + 1):
            status = "🟢" if record.active else "⚫"
            last_used = (
                f"<t:{record.last_used_at}:R>" if record.last_used_at else "no tracked use"
            )
            lines.append(
                f"{status} **{index}. {discord.utils.escape_markdown(record.guild_name)}**\n"
                f"`{record.guild_id}` • {record.member_count:,} members • "
                f"{record.channel_count:,} channels • {record.usage_count:,} uses • {last_used}"
            )

        embed = discord.Embed(
            title=f"🌐 Bot Servers — Page {selected_page}/{page_count}",
            description="\n\n".join(lines) if lines else "No server records yet.",
            color=0x5865F2,
        )
        embed.set_footer(
            text=f"{sum(1 for item in records if item.active)} active • {len(records)} historical"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotInfo(bot))
