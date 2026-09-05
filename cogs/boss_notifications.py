"""Opt-in guild-boss reward and outcome DM notifications."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

from .helper_prefix import get_guild_helper_prefix, parse_helper_command_argument
from .message_utils import safe_reply


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "boss_notifications.db"
MAX_REWARD_IMAGE_BYTES = 512 * 1024
OBSERVATION_RETENTION_SECONDS = 7 * 24 * 60 * 60
REWARD_LABELS = {
    "shards": "weapon shards",
    "weapon_crates": "weapon crates",
    "boss_crates": "boss weapon crates",
    "xp": "XP",
    "x2": "any x2 reward",
    "end": "boss end",
}
REWARD_ALIASES = {
    "shard": "shards", "shards": "shards", "ws": "shards",
    "weaponshard": "shards", "weaponshards": "shards",
    "crate": "weapon_crates", "crates": "weapon_crates", "wc": "weapon_crates",
    "wcrate": "weapon_crates", "wcrates": "weapon_crates",
    "weaponcrate": "weapon_crates", "weaponcrates": "weapon_crates",
    "bcrate": "boss_crates", "bcrates": "boss_crates", "bc": "boss_crates",
    "bwc": "boss_crates", "bosscrate": "boss_crates", "bosscrates": "boss_crates",
    "bossweaponcrate": "boss_crates", "bossweaponcrates": "boss_crates",
    "xp": "xp", "experience": "xp", "x2": "x2", "double": "x2",
    "doubled": "x2", "end": "end", "ends": "end", "ended": "end",
    "finish": "end", "finished": "end",
}
PREFIX_ALIASES = {
    "h boss notify", "h boss notification", "h boss notifications", "h boss ping",
}


# OwO's reward image uses a fixed ten-pixel font. These lossless masks were
# derived from supplied production cards, avoiding external OCR services.
_REWARD_DIGIT_ROWS = {
    "0": ("..###..", ".##.##.", "##...##", "#.....#", "#.....#", "#.....#", "#.....#", "##...##", ".##.##.", "..###.."),
    "1": ("..##", ".###", "####", "..##", "..##", "..##", "..##", "..##", "..##", "..##"),
    "2": (".####.", "##..##", "#....#", ".....#", "....##", "...##.", ".##...", "##....", "##....", "######"),
    "3": (".####..", "##..##.", ".....#.", "....##.", "...##..", "....##.", ".....##", "#....#.", "##..##.", ".####.."),
    "4": (".....##.", "....###.", "...####.", "...#.##.", "..##.##.", ".##..##.", ".#...##.", "########", ".....##.", ".....##."),
    "5": (".#####.", ".#.....", ".#.....", "##.....", "#####..", "##..##.", ".....##", ".....##", "##..##.", ".####.."),
    "6": ("...##..", "..##...", "..##...", ".##....", "#####..", "##..##.", "#....##", "#....##", "##..##.", ".####.."),
    "7": ("######", "....##", "....#.", "...#..", "..##..", "..##..", "..#...", "..#...", ".##...", ".##..."),
    "8": (".####.", "##..##", "#....#", "##...#", ".####.", "##..##", "#....#", "#....#", "##..##", ".####."),
    "9": (".####..", "##..##.", "#....#.", "#....##", "##..##.", ".#####.", "...##..", "...##..", "..##...", "..#...."),
}
_REWARD_DIGITS = {
    char: tuple(tuple(pixel == "#" for pixel in row) for row in rows)
    for char, rows in _REWARD_DIGIT_ROWS.items()
}


@dataclass(frozen=True)
class BossRewards:
    shards: int
    weapon_crates: int
    boss_crates: int
    xp: int
    doubled_mask: int = 0
    confidence: float = 1.0

    def doubled_types(self) -> tuple[str, ...]:
        keys = ("shards", "weapon_crates", "boss_crates", "xp")
        return tuple(key for index, key in enumerate(keys) if self.doubled_mask & (1 << index))


@dataclass(frozen=True)
class BossSubscription:
    guild_id: int
    user_id: int
    reward_type: str
    minimum: int
    mode: str
    boss_key: int


@dataclass(frozen=True)
class BossSnapshot:
    guild_id: int
    boss_key: int
    channel_id: int
    message_id: int
    rewards: BossRewards
    observations: int = 1
    conflicts: int = 0


def _digit_similarity(observed: list[list[bool]], template: tuple[tuple[bool, ...], ...]) -> float:
    if len(observed) != len(template) or len(observed[0]) != len(template[0]):
        return -1.0
    overlap = union = 0
    for observed_row, template_row in zip(observed, template):
        for actual, expected in zip(observed_row, template_row):
            overlap += int(actual and expected)
            union += int(actual or expected)
    return overlap / union if union else 0.0


def _recognize_reward_run(mask: list[list[bool]]) -> tuple[str, list[float]] | None:
    width = len(mask[0]) if mask else 0
    if not width or len(mask) != 10:
        return None
    best: tuple[float, list[tuple[str, float]]] | None = None

    def solve(position: int, remaining: int) -> list[list[tuple[str, float]]]:
        if remaining == 0:
            return [[]] if position == width else []
        results: list[list[tuple[str, float]]] = []
        for end in range(position + 3, min(width, position + 8) + 1):
            segment = [row[position:end] for row in mask]
            for character, template in _REWARD_DIGITS.items():
                score = _digit_similarity(segment, template)
                if score < 0:
                    continue
                for tail in solve(end, remaining - 1):
                    results.append([(character, score), *tail])
        return results

    for count in range(1, min(6, width // 3) + 1):
        for candidate in solve(0, count):
            scores = [score for _, score in candidate]
            quality = sum(scores) / len(scores) - min(scores) * 0.05 - count * 0.005
            if best is None or quality > best[0]:
                best = (quality, candidate)
    if best is None:
        return None
    characters = "".join(character for character, _ in best[1])
    scores = [score for _, score in best[1]]
    if min(scores) < 0.82 or sum(scores) / len(scores) < 0.88:
        return None
    return characters, scores


def _read_reward_slot(image: Image.Image, index: int) -> tuple[int, float] | None:
    slot_left = 8 + index * 152
    top = 18 if index == 3 else 19
    crop = image.crop((slot_left + 36, top, slot_left + 110, top + 11)).convert("RGB")
    mask: list[list[bool]] = []
    for y in range(crop.height):
        row: list[bool] = []
        for x in range(crop.width):
            red, green, blue = crop.getpixel((x, y))
            row.append(red > 180 and green > 180 and blue > 180 and max(red, green, blue) - min(red, green, blue) < 60)
        mask.append(row)
    # Remove comma-only columns, including commas touching their next digit.
    for x in range(crop.width):
        active_rows = [y for y in range(crop.height) if mask[y][x]]
        if active_rows and min(active_rows) >= 8:
            for y in active_rows:
                mask[y][x] = False
    mask = mask[:10]
    projection = [sum(row[x] for row in mask) for x in range(crop.width)]
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x, count in enumerate([*projection, 0]):
        if count and start is None:
            start = x
        elif not count and start is not None:
            runs.append((start, x - 1))
            start = None
    characters: list[str] = []
    scores: list[float] = []
    for left, right in runs:
        recognized = _recognize_reward_run([row[left : right + 1] for row in mask])
        if recognized is None:
            return None
        text, glyph_scores = recognized
        characters.append(text)
        scores.extend(glyph_scores)
    if not characters or not scores:
        return None
    return int("".join(characters)), min(scores)


def _read_doubled_mask(image: Image.Image) -> int:
    mask = 0
    for index in range(4):
        start = 8 + index * 152 + 136
        end = min(image.width, start + 28)
        yellow_pixels = 0
        for y in range(0, min(19, image.height)):
            for x in range(start, end):
                red, green, blue = image.getpixel((x, y))[:3]
                if red > 160 and green > 150 and blue < 100 and red + green > 350:
                    yellow_pixels += 1
        if yellow_pixels >= 50:
            mask |= 1 << index
    return mask


def read_boss_rewards(image_bytes: bytes) -> BossRewards | None:
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except OSError:
        return None
    if image.size != (620, 60):
        return None
    results = [_read_reward_slot(image, index) for index in range(4)]
    if any(result is None for result in results):
        return None
    values = [int(result[0]) for result in results if result is not None]
    confidence = min(float(result[1]) for result in results if result is not None)
    shards, weapon_crates, boss_crates, xp = values
    if not (
        1 <= shards <= 10_000
        and 1 <= weapon_crates <= 100
        and 1 <= boss_crates <= 100
        and 100 <= xp <= 10_000_000
    ):
        return None
    return BossRewards(
        shards=shards,
        weapon_crates=weapon_crates,
        boss_crates=boss_crates,
        xp=xp,
        doubled_mask=_read_doubled_mask(image),
        confidence=confidence,
    )


def extract_reward_media_url(data: dict[str, Any]) -> str | None:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            url = value.get("url")
            if isinstance(url, str):
                parsed = urlsplit(url)
                if (
                    parsed.scheme == "https"
                    and parsed.hostname in {"cdn.discordapp.com", "media.discordapp.net"}
                    and parsed.path.casefold().endswith("/reward.png")
                ):
                    found.append(url)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data.get("components", []))
    return found[0] if found else None


def parse_minimum(value: str) -> int | None:
    normalized = (value or "").strip().casefold().replace(",", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([km]?)", normalized)
    if not match:
        return None
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2)]
    result = int(float(match.group(1)) * multiplier)
    return result if 1 <= result <= 2_000_000_000 else None


def reward_matches(subscription: BossSubscription, rewards: BossRewards) -> bool:
    if subscription.reward_type == "x2":
        return bool(rewards.doubled_mask)
    value = {
        "shards": rewards.shards,
        "weapon_crates": rewards.weapon_crates,
        "boss_crates": rewards.boss_crates,
        "xp": rewards.xp,
    }.get(subscription.reward_type)
    return value is not None and value >= subscription.minimum


class ClosingConnection(sqlite3.Connection):
    def __enter__(self) -> "ClosingConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


class BossNotificationStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DATABASE_FILE
        self.ensure_schema()

    def connect(self) -> ClosingConnection:
        connection = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS boss_notification_consent (
                    user_id INTEGER PRIMARY KEY,
                    consented_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS boss_notification_subscriptions (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    reward_type TEXT NOT NULL,
                    minimum INTEGER NOT NULL DEFAULT 1,
                    mode TEXT NOT NULL DEFAULT 'recurring',
                    boss_key INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, reward_type)
                );
                CREATE INDEX IF NOT EXISTS idx_boss_notify_guild
                    ON boss_notification_subscriptions (guild_id);
                CREATE TABLE IF NOT EXISTS boss_reward_snapshots (
                    guild_id INTEGER NOT NULL,
                    boss_key INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    shards INTEGER NOT NULL,
                    weapon_crates INTEGER NOT NULL,
                    boss_crates INTEGER NOT NULL,
                    xp INTEGER NOT NULL,
                    doubled_mask INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL,
                    observations INTEGER NOT NULL DEFAULT 1,
                    conflicts INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, boss_key)
                );
                CREATE TABLE IF NOT EXISTS boss_notification_deliveries (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    boss_key INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sent_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, boss_key, kind)
                );
                """
            )

    def guild_ids(self) -> set[int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT guild_id FROM boss_notification_subscriptions"
            ).fetchall()
        return {int(row[0]) for row in rows}

    def has_consent(self, user_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM boss_notification_consent WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row is not None

    def set_consent(self, user_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO boss_notification_consent (user_id, consented_at) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET consented_at = excluded.consented_at",
                (user_id, int(time.time())),
            )

    def upsert_subscription(self, subscription: BossSubscription) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO boss_notification_subscriptions
                    (guild_id, user_id, reward_type, minimum, mode, boss_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, reward_type) DO UPDATE SET
                    minimum = excluded.minimum,
                    mode = excluded.mode,
                    boss_key = excluded.boss_key,
                    created_at = excluded.created_at
                """,
                (
                    subscription.guild_id, subscription.user_id,
                    subscription.reward_type, subscription.minimum,
                    subscription.mode, subscription.boss_key, int(time.time()),
                ),
            )

    def remove_subscriptions(self, guild_id: int, user_id: int, reward_type: str | None) -> int:
        with self.connect() as connection:
            if reward_type is None:
                cursor = connection.execute(
                    "DELETE FROM boss_notification_subscriptions WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM boss_notification_subscriptions WHERE guild_id = ? AND user_id = ? AND reward_type = ?",
                    (guild_id, user_id, reward_type),
                )
            remaining = connection.execute(
                "SELECT 1 FROM boss_notification_subscriptions WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if remaining is None:
                connection.execute(
                    "DELETE FROM boss_notification_consent WHERE user_id = ?", (user_id,)
                )
        return max(0, cursor.rowcount)

    def list_subscriptions(self, guild_id: int, user_id: int | None = None) -> list[BossSubscription]:
        query = "SELECT * FROM boss_notification_subscriptions WHERE guild_id = ?"
        values: list[int] = [guild_id]
        if user_id is not None:
            query += " AND user_id = ?"
            values.append(user_id)
        query += " ORDER BY user_id, reward_type"
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [
            BossSubscription(
                guild_id=int(row["guild_id"]), user_id=int(row["user_id"]),
                reward_type=str(row["reward_type"]), minimum=int(row["minimum"]),
                mode=str(row["mode"]), boss_key=int(row["boss_key"]),
            )
            for row in rows
        ]

    def claim_pending_once(self, guild_id: int, boss_key: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM boss_notification_subscriptions "
                "WHERE guild_id = ? AND mode = 'once' AND boss_key NOT IN (0, ?)",
                (guild_id, boss_key),
            )
            connection.execute(
                "UPDATE boss_notification_subscriptions SET boss_key = ? "
                "WHERE guild_id = ? AND mode = 'once' AND boss_key = 0",
                (boss_key, guild_id),
            )

    def clear_completed_once(self, guild_id: int, boss_key: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM boss_notification_subscriptions "
                "WHERE guild_id = ? AND mode = 'once' AND boss_key = ?",
                (guild_id, boss_key),
            )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> BossSnapshot:
        return BossSnapshot(
            guild_id=int(row["guild_id"]), boss_key=int(row["boss_key"]),
            channel_id=int(row["channel_id"]), message_id=int(row["message_id"]),
            rewards=BossRewards(
                shards=int(row["shards"]), weapon_crates=int(row["weapon_crates"]),
                boss_crates=int(row["boss_crates"]), xp=int(row["xp"]),
                doubled_mask=int(row["doubled_mask"]), confidence=float(row["confidence"]),
            ),
            observations=int(row["observations"]), conflicts=int(row["conflicts"]),
        )

    def save_snapshot(self, snapshot: BossSnapshot) -> tuple[BossSnapshot, bool]:
        now = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM boss_reward_snapshots WHERE guild_id = ? AND boss_key = ?",
                (snapshot.guild_id, snapshot.boss_key),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO boss_reward_snapshots
                        (guild_id, boss_key, channel_id, message_id, shards,
                         weapon_crates, boss_crates, xp, doubled_mask, confidence,
                         observations, conflicts, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
                    """,
                    (
                        snapshot.guild_id, snapshot.boss_key, snapshot.channel_id,
                        snapshot.message_id, snapshot.rewards.shards,
                        snapshot.rewards.weapon_crates, snapshot.rewards.boss_crates,
                        snapshot.rewards.xp, snapshot.rewards.doubled_mask,
                        snapshot.rewards.confidence, now,
                    ),
                )
                return snapshot, True
            existing = self._snapshot_from_row(row)
            # Confidence is diagnostic metadata and can vary slightly if Discord
            # recompresses the same card. Only the visible reward facts determine
            # whether two independent observations agree.
            consistent = (
                existing.rewards.shards == snapshot.rewards.shards
                and existing.rewards.weapon_crates == snapshot.rewards.weapon_crates
                and existing.rewards.boss_crates == snapshot.rewards.boss_crates
                and existing.rewards.xp == snapshot.rewards.xp
                and existing.rewards.doubled_mask == snapshot.rewards.doubled_mask
            )
            connection.execute(
                "UPDATE boss_reward_snapshots SET message_id = ?, observations = observations + 1, "
                "conflicts = conflicts + ?, updated_at = ? WHERE guild_id = ? AND boss_key = ?",
                (
                    max(existing.message_id, snapshot.message_id),
                    0 if consistent else 1, now, snapshot.guild_id, snapshot.boss_key,
                ),
            )
            return existing, consistent

    def get_snapshot(self, guild_id: int, boss_key: int) -> BossSnapshot | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM boss_reward_snapshots WHERE guild_id = ? AND boss_key = ?",
                (guild_id, boss_key),
            ).fetchone()
        return self._snapshot_from_row(row) if row else None

    def delivery_exists(self, guild_id: int, user_id: int, boss_key: int, kind: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM boss_notification_deliveries "
                "WHERE guild_id = ? AND user_id = ? AND boss_key = ? AND kind = ?",
                (guild_id, user_id, boss_key, kind),
            ).fetchone()
        return row is not None

    def mark_delivery(self, guild_id: int, user_id: int, boss_key: int, kind: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO boss_notification_deliveries "
                "(guild_id, user_id, boss_key, kind, status, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, boss_key, kind, status, int(time.time())),
            )

    def prune_old_observations(self, now: int | None = None) -> None:
        cutoff = int(now or time.time()) - OBSERVATION_RETENTION_SECONDS
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM boss_reward_snapshots WHERE updated_at < ?", (cutoff,)
            )
            connection.execute(
                "DELETE FROM boss_notification_deliveries WHERE sent_at < ?", (cutoff,)
            )

    def telemetry(self) -> dict[str, int]:
        with self.connect() as connection:
            subscriptions = int(connection.execute("SELECT COUNT(*) FROM boss_notification_subscriptions").fetchone()[0])
            users = int(connection.execute("SELECT COUNT(DISTINCT user_id) FROM boss_notification_subscriptions").fetchone()[0])
            snapshots = int(connection.execute("SELECT COUNT(*) FROM boss_reward_snapshots").fetchone()[0])
            conflicts = int(connection.execute("SELECT COALESCE(SUM(conflicts), 0) FROM boss_reward_snapshots").fetchone()[0])
            deliveries = int(connection.execute("SELECT COUNT(*) FROM boss_notification_deliveries WHERE status = 'sent'").fetchone()[0])
        return {
            "subscriptions": subscriptions, "users": users, "snapshots": snapshots,
            "conflicts": conflicts, "deliveries": deliveries,
        }


class BossNotificationConsentView(discord.ui.View):
    def __init__(self, cog: "BossNotifications", subscription: BossSubscription):
        super().__init__(timeout=120)
        self.cog = cog
        self.subscription = subscription

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.subscription.user_id:
            return True
        await interaction.response.send_message(
            "Only the member who requested this alert can confirm it.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Enable DM alerts", style=discord.ButtonStyle.success, emoji="🔔")
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        try:
            await interaction.user.send(
                "✅ You enabled OwO Boss Helper DMs. Disable individual rules or all alerts at any time with `/boss-notify`."
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "I could not DM you. Enable direct messages from server members, then press the button again.",
                ephemeral=True,
            )
            return
        await asyncio.to_thread(self.cog.store.set_consent, interaction.user.id)
        result = await self.cog.save_subscription(self.subscription)
        await interaction.response.edit_message(content=result, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Boss DM alert setup cancelled.", view=None)
        self.stop()


class BossNotifications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = BossNotificationStore()
        self.subscribed_guild_ids = self.store.guild_ids()
        self.http_session: aiohttp.ClientSession | None = None
        self.guild_locks: dict[int, asyncio.Lock] = {}
        self.last_prune_at = 0

    def cog_unload(self) -> None:
        if self.http_session and not self.http_session.closed:
            asyncio.create_task(self.http_session.close())

    def has_guild_subscribers(self, guild_id: int) -> bool:
        return guild_id in self.subscribed_guild_ids

    def active_boss_key(self, guild_id: int) -> int:
        tracker = self.bot.get_cog("BossGenerator")
        configs = getattr(tracker, "cooldown_config", {})
        config = configs.get(str(guild_id), {}) if isinstance(configs, dict) else {}
        key = int(config.get("active_boss_expires_at") or 0)
        if key > int(time.time()) and str(config.get("last_result") or "active") == "active":
            return key
        return 0

    async def fetch_reward_image(self, url: str) -> bytes | None:
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession()
        try:
            async with self.http_session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as response:
                if response.status != 200:
                    return None
                if response.content_length and response.content_length > MAX_REWARD_IMAGE_BYTES:
                    return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > MAX_REWARD_IMAGE_BYTES:
                        return None
                    chunks.append(chunk)
            return b"".join(chunks)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def handle_active_boss(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        boss_key: int,
        data: dict[str, Any],
    ) -> None:
        if not boss_key:
            return
        async with self.guild_locks.setdefault(guild_id, asyncio.Lock()):
            now = int(time.time())
            if now - self.last_prune_at >= 6 * 60 * 60:
                await asyncio.to_thread(self.store.prune_old_observations, now)
                self.last_prune_at = now
            await asyncio.to_thread(self.store.claim_pending_once, guild_id, boss_key)
            snapshot = await asyncio.to_thread(self.store.get_snapshot, guild_id, boss_key)
            needs_observation = snapshot is None or (
                snapshot.observations < 3 and message_id > snapshot.message_id
            )
            if needs_observation:
                url = extract_reward_media_url(data)
                if not url:
                    return
                body = await self.fetch_reward_image(url)
                rewards = await asyncio.to_thread(read_boss_rewards, body or b"")
                if rewards is None:
                    logger.warning(
                        "Could not reliably read reward card for guild %s boss %s",
                        guild_id,
                        boss_key,
                    )
                    return
                proposed = BossSnapshot(guild_id, boss_key, channel_id, message_id, rewards)
                snapshot, consistent = await asyncio.to_thread(self.store.save_snapshot, proposed)
                if not consistent:
                    logger.warning(
                        "Conflicting reward reads for guild %s boss %s; keeping first snapshot",
                        guild_id,
                        boss_key,
                    )
                    return
                snapshot = await asyncio.to_thread(
                    self.store.get_snapshot, guild_id, boss_key
                )
                if snapshot is None:
                    return
                logger.info(
                    "Boss rewards read for guild %s boss %s: %s shards, %s crates, %s boss crates, %s XP, x2=%s",
                    guild_id,
                    boss_key,
                    rewards.shards,
                    rewards.weapon_crates,
                    rewards.boss_crates,
                    rewards.xp,
                    ",".join(rewards.doubled_types()) or "none",
                )
            if self.has_guild_subscribers(guild_id):
                await self.evaluate_reward_subscriptions(snapshot)

    async def evaluate_reward_subscriptions(self, snapshot: BossSnapshot) -> None:
        subscriptions = await asyncio.to_thread(
            self.store.list_subscriptions, snapshot.guild_id
        )
        matches: dict[int, list[BossSubscription]] = {}
        for subscription in subscriptions:
            if subscription.reward_type == "end":
                continue
            if subscription.mode == "once" and subscription.boss_key != snapshot.boss_key:
                continue
            if reward_matches(subscription, snapshot.rewards):
                matches.setdefault(subscription.user_id, []).append(subscription)
        for user_id, matched in matches.items():
            delivered = await asyncio.to_thread(
                self.store.delivery_exists,
                snapshot.guild_id,
                user_id,
                snapshot.boss_key,
                "reward",
            )
            if delivered:
                continue
            sent = await self.send_reward_dm(user_id, snapshot, matched)
            await asyncio.to_thread(
                self.store.mark_delivery,
                snapshot.guild_id,
                user_id,
                snapshot.boss_key,
                "reward",
                "sent" if sent else "failed",
            )

    async def send_reward_dm(
        self,
        user_id: int,
        snapshot: BossSnapshot,
        matched: list[BossSubscription],
    ) -> bool:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            guild = self.bot.get_guild(snapshot.guild_id)
            server_name = guild.name if guild else f"Server {snapshot.guild_id}"
            rewards = snapshot.rewards
            doubled = ", ".join(
                REWARD_LABELS[key] for key in rewards.doubled_types()
            ) or "None"
            matched_text = ", ".join(REWARD_LABELS[item.reward_type] for item in matched)
            embed = discord.Embed(
                title="🎁 Guild Boss Reward Match",
                description=(
                    f"**{discord.utils.escape_markdown(server_name)}** has a boss "
                    f"matching: **{matched_text}**."
                ),
                color=0xF1C40F,
            )
            embed.add_field(name="Weapon shards", value=f"{rewards.shards:,}", inline=True)
            embed.add_field(name="Weapon crates", value=f"{rewards.weapon_crates:,}", inline=True)
            embed.add_field(name="Boss weapon crates", value=f"{rewards.boss_crates:,}", inline=True)
            embed.add_field(name="Experience", value=f"{rewards.xp:,}", inline=True)
            embed.add_field(name="x2 rewards", value=doubled, inline=False)
            embed.add_field(
                name="Boss card",
                value=(
                    f"[Open in Discord](https://discord.com/channels/"
                    f"{snapshot.guild_id}/{snapshot.channel_id}/{snapshot.message_id})"
                ),
                inline=False,
            )
            embed.set_footer(text="Opt-in alert • Manage with /boss-notify in that server")
            await user.send(embed=embed)
            return True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.info("Could not deliver boss reward DM to user %s", user_id)
            return False

    async def handle_boss_outcome(
        self,
        guild_id: int,
        boss_key: int,
        outcome: str,
        event_time: int,
    ) -> None:
        if not self.has_guild_subscribers(guild_id):
            return
        subscriptions = await asyncio.to_thread(self.store.list_subscriptions, guild_id)
        for subscription in subscriptions:
            if subscription.reward_type != "end":
                continue
            if subscription.mode == "once" and subscription.boss_key != boss_key:
                continue
            delivered = await asyncio.to_thread(
                self.store.delivery_exists,
                guild_id,
                subscription.user_id,
                boss_key,
                "end",
            )
            if delivered:
                continue
            sent = await self.send_outcome_dm(
                subscription.user_id, guild_id, outcome, event_time
            )
            await asyncio.to_thread(
                self.store.mark_delivery,
                guild_id,
                subscription.user_id,
                boss_key,
                "end",
                "sent" if sent else "failed",
            )
        await asyncio.to_thread(self.store.clear_completed_once, guild_id, boss_key)
        self.subscribed_guild_ids = await asyncio.to_thread(self.store.guild_ids)

    async def send_outcome_dm(
        self,
        user_id: int,
        guild_id: int,
        outcome: str,
        event_time: int,
    ) -> bool:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            guild = self.bot.get_guild(guild_id)
            server_name = guild.name if guild else f"Server {guild_id}"
            verb = "was defeated" if outcome == "defeated" else "escaped"
            await user.send(
                f"🏁 The guild boss in **{discord.utils.escape_markdown(server_name)}** "
                f"{verb} <t:{event_time}:R>.\n"
                "This is an opt-in alert. Manage it with `/boss-notify` in that server."
            )
            return True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.info("Could not deliver boss-end DM to user %s", user_id)
            return False

    async def save_subscription(self, subscription: BossSubscription) -> str:
        await asyncio.to_thread(self.store.upsert_subscription, subscription)
        self.subscribed_guild_ids.add(subscription.guild_id)
        mode_text = "every boss" if subscription.mode == "recurring" else (
            "the current boss only" if subscription.boss_key else "the next boss only"
        )
        minimum_text = (
            ""
            if subscription.reward_type in {"x2", "end"}
            else f" of at least **{subscription.minimum:,}**"
        )
        result = (
            f"✅ DM alert enabled for **{REWARD_LABELS[subscription.reward_type]}**"
            f"{minimum_text}, for **{mode_text}**."
        )
        if subscription.reward_type != "end" and subscription.boss_key:
            snapshot = await asyncio.to_thread(
                self.store.get_snapshot, subscription.guild_id, subscription.boss_key
            )
            if snapshot:
                await self.evaluate_reward_subscriptions(snapshot)
        return result

    async def request_subscription(
        self,
        guild_id: int,
        user: discord.abc.User,
        reward_type: str,
        minimum: int,
        mode: str,
    ) -> tuple[str, discord.ui.View | None]:
        boss_key = self.active_boss_key(guild_id) if mode == "once" else 0
        subscription = BossSubscription(
            guild_id, user.id, reward_type, minimum, mode, boss_key
        )
        consented = await asyncio.to_thread(self.store.has_consent, user.id)
        if consented:
            return await self.save_subscription(subscription), None
        return (
            "🔔 **Confirm direct-message alerts**\n"
            "By enabling this rule, you agree that OwO Boss Helper may DM you when "
            "it matches. No rule is saved until the test DM succeeds.",
            BossNotificationConsentView(self, subscription),
        )

    async def disable_subscription(
        self,
        guild_id: int,
        user_id: int,
        reward_type: str | None,
    ) -> str:
        removed = await asyncio.to_thread(
            self.store.remove_subscriptions, guild_id, user_id, reward_type
        )
        self.subscribed_guild_ids = await asyncio.to_thread(self.store.guild_ids)
        label = "all boss DM alerts" if reward_type is None else REWARD_LABELS[reward_type]
        if removed:
            return f"✅ Disabled **{label}**."
        return f"No enabled **{label}** rule was found."

    async def status_text(
        self,
        guild_id: int,
        user_id: int,
        helper_prefix: str = "h",
    ) -> str:
        rules = await asyncio.to_thread(
            self.store.list_subscriptions, guild_id, user_id
        )
        if not rules:
            return (
                "You have no boss DM alerts in this server.\n"
                f"Use `{helper_prefix} boss notify xp 20k`, "
                f"`{helper_prefix} boss notify bcrate 4`, "
                f"`{helper_prefix} boss notify x2`, or "
                f"`{helper_prefix} boss notify end current`."
            )
        lines = ["🔔 **Your boss DM alerts in this server**"]
        for rule in rules:
            threshold = (
                ""
                if rule.reward_type in {"x2", "end"}
                else f" ≥ {rule.minimum:,}"
            )
            mode = "recurring" if rule.mode == "recurring" else (
                "current boss" if rule.boss_key else "next boss"
            )
            lines.append(
                f"• **{REWARD_LABELS[rule.reward_type]}**{threshold} — {mode}"
            )
        lines.append(
            f"Disable one with `{helper_prefix} boss notify <reward> off`, "
            f"or all with `{helper_prefix} boss notify off`."
        )
        return "\n".join(lines)

    @app_commands.command(
        name="boss-notify",
        description="Manage your opt-in guild-boss reward and end DMs.",
    )
    @app_commands.describe(
        reward="Reward or event to watch; leave empty to view your rules",
        minimum="Minimum displayed amount (ignored for x2 and boss end)",
        mode="Repeat for every boss or watch the current/next boss once",
        enabled="Enable this rule, or disable the selected rule",
    )
    @app_commands.choices(
        reward=[
            app_commands.Choice(name="Weapon shards", value="shards"),
            app_commands.Choice(name="Weapon crates", value="weapon_crates"),
            app_commands.Choice(name="Boss weapon crates", value="boss_crates"),
            app_commands.Choice(name="Experience", value="xp"),
            app_commands.Choice(name="Any x2 reward", value="x2"),
            app_commands.Choice(name="Boss defeated or escaped", value="end"),
            app_commands.Choice(name="All my alerts", value="all"),
        ],
        mode=[
            app_commands.Choice(name="Every boss", value="recurring"),
            app_commands.Choice(name="Current or next boss only", value="once"),
        ],
    )
    @app_commands.guild_only()
    async def boss_notify(
        self,
        interaction: discord.Interaction,
        reward: app_commands.Choice[str] | None = None,
        minimum: app_commands.Range[int, 1, 2_000_000_000] = 1,
        mode: app_commands.Choice[str] | None = None,
        enabled: bool = True,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return
        helper_prefix = await get_guild_helper_prefix(interaction.guild_id)
        if reward is None:
            await interaction.response.send_message(
                await self.status_text(
                    interaction.guild_id, interaction.user.id, helper_prefix
                ),
                ephemeral=True,
            )
            return
        reward_type = reward.value
        if reward_type == "all":
            if enabled:
                text = (
                    "Choose a specific reward to enable. `All my alerts` is only "
                    "for disabling."
                )
            else:
                text = await self.disable_subscription(
                    interaction.guild_id, interaction.user.id, None
                )
            await interaction.response.send_message(text, ephemeral=True)
            return
        if not enabled:
            await interaction.response.send_message(
                await self.disable_subscription(
                    interaction.guild_id, interaction.user.id, reward_type
                ),
                ephemeral=True,
            )
            return
        effective_minimum = 1 if reward_type in {"x2", "end"} else int(minimum)
        text, view = await self.request_subscription(
            interaction.guild_id,
            interaction.user,
            reward_type,
            effective_minimum,
            mode.value if mode else "recurring",
        )
        await interaction.response.send_message(text, view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        helper_prefix = await get_guild_helper_prefix(message.guild.id)
        argument = parse_helper_command_argument(
            message.content, helper_prefix, PREFIX_ALIASES
        )
        if argument is None:
            return
        tokens = (
            re.sub(r"\s+", " ", argument.strip()).split()
            if argument.strip()
            else []
        )
        if not tokens or tokens[0].casefold() in {"status", "list", "help"}:
            await safe_reply(
                message,
                await self.status_text(
                    message.guild.id, message.author.id, helper_prefix
                ),
                mention_author=False,
            )
            return
        compact_reward = re.sub(r"[^a-z0-9]", "", tokens[0].casefold())
        if compact_reward in {"off", "disable", "clear"}:
            await safe_reply(
                message,
                await self.disable_subscription(
                    message.guild.id, message.author.id, None
                ),
                mention_author=False,
            )
            return
        reward_type = REWARD_ALIASES.get(compact_reward)
        if reward_type is None:
            await safe_reply(
                message,
                "Unknown reward. Use `shards`, `crate`, `bcrate`, `xp`, `x2`, or `end`.",
                mention_author=False,
            )
            return
        lowered = {token.casefold() for token in tokens[1:]}
        if lowered & {"off", "disable", "clear"}:
            await safe_reply(
                message,
                await self.disable_subscription(
                    message.guild.id, message.author.id, reward_type
                ),
                mention_author=False,
            )
            return
        mode = "once" if lowered & {"once", "current", "one"} else "recurring"
        amount_tokens = [
            token
            for token in tokens[1:]
            if token.casefold()
            not in {"once", "current", "one", "recurring", "always", "every"}
        ]
        if reward_type in {"x2", "end"}:
            minimum = 1
        else:
            minimum = parse_minimum(amount_tokens[0]) if amount_tokens else None
            if minimum is None:
                await safe_reply(
                    message,
                    f"Add a minimum amount, for example "
                    f"`{helper_prefix} boss notify {tokens[0]} 20k`.",
                    mention_author=False,
                )
                return
        text, view = await self.request_subscription(
            message.guild.id,
            message.author,
            reward_type,
            minimum,
            mode,
        )
        await safe_reply(message, text, view=view, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BossNotifications(bot))
