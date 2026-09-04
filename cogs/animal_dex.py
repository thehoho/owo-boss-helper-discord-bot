"""Public OwO animal-dex catalog, silent learning, and explicit lookup."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .game_catalog import (
    normalize_catalog_token,
    resolve_rank,
    resolve_special_animal,
    special_animals,
    special_catalog_updated_at,
)
from .helper_prefix import get_guild_helper_prefix, parse_helper_command_argument
from .owo_prefix import normalize_owo_prefix
from .ui_emojis import ui_emoji_text


logger = logging.getLogger(__name__)

OWO_BOT_ID = 408785106942164992
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "animal_dex.db"
MAX_DESCRIPTION_LENGTH = 1000

DEX_DETAIL_START_RE = re.compile(
    r"(?im)^\s*(?:[*_`>#-]+\s*)?"
    r"(?:count|rank|rarity|alias(?:es)?|points|sell|sacrifice)"
    r"(?:[*_`]+)?\s*:"
)

STAT_ALIASES: dict[str, tuple[str, ...]] = {
    "hp": ("hp",),
    "str": ("att", "str", "pa"),
    "pr": ("pr",),
    "wp": ("wp",),
    "mag": ("mag", "ma"),
    "mr": ("mr",),
}

RANK_ALIASES = {
    "c": "common",
    "u": "uncommon",
    "r": "rare",
    "e": "epic",
    "m": "mythical",
    "mythic": "mythical",
    "p": "patreon",
    "cpatreon": "custom_patreon",
    "custom patreon": "custom_patreon",
    "g": "gem",
    "l": "legendary",
    "f": "fabled",
    "b": "bot",
    "h": "hidden",
    "d": "distorted",
    "s": "special",
}


@dataclass(frozen=True)
class AnimalDexRecord:
    animal_key: str
    display_name: str
    rank: str
    description: str
    aliases: tuple[str, ...]
    total_caught: int | None
    rarity_text: str
    points: int | None
    sell_text: str
    sacrifice_text: str
    hp: int | None
    strength: int | None
    pr: int | None
    wp: int | None
    mag: int | None
    mr: int | None
    emoji_name: str
    emoji_id: int | None
    emoji_animated: bool
    image_url: str
    source: str
    updated_at: int


class ClosingConnection(sqlite3.Connection):
    def __enter__(self) -> "ClosingConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def normalize_animal_key(value: str) -> str:
    return normalize_catalog_token(value).replace(" ", "_")


def parse_int(value: str) -> int | None:
    match = re.search(r"\d[\d,]*", value or "")
    return int(match.group(0).replace(",", "")) if match else None


def normalize_rank(value: str) -> str:
    value = re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", " ", value or "")
    value = re.sub(r":[A-Za-z0-9_]+:", " ", value)
    cleaned = normalize_catalog_token(value)
    if " " in cleaned and len(set(cleaned.split())) == 1:
        cleaned = cleaned.split()[0]
    cleaned = RANK_ALIASES.get(cleaned, cleaned.replace(" ", "_"))
    resolved = resolve_rank(cleaned)
    return resolved.key if resolved else cleaned


def extract_message_text(message: discord.Message) -> str:
    chunks: list[str] = []
    if message.content:
        chunks.append(message.content)
    for embed in message.embeds:
        if embed.author and embed.author.name:
            chunks.append(str(embed.author.name))
        if embed.title:
            chunks.append(str(embed.title))
        if embed.description:
            chunks.append(str(embed.description))
        for field in embed.fields:
            chunks.extend((str(field.name), str(field.value)))
        if embed.footer and embed.footer.text:
            chunks.append(str(embed.footer.text))
    return "\n".join(item.strip() for item in chunks if item and item.strip())


def label_value(text: str, *labels: str) -> str:
    choices = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?im)^\s*(?:[*_`>#-]+\s*)?(?:{choices})\s*:\s*(.+?)\s*$",
        text,
    )
    return match.group(1).strip(" *_`") if match else ""


def custom_emojis(text: str) -> list[tuple[str, int, bool]]:
    return [
        (match.group(2), int(match.group(3)), bool(match.group(1)))
        for match in re.finditer(r"<(a?):([A-Za-z0-9_]+):(\d+)>", text or "")
    ]


def stat_value(text: str, aliases: tuple[str, ...]) -> int | None:
    choices = "|".join(re.escape(alias) for alias in aliases)
    patterns = (
        rf"<a?:(?:{choices}):\d+>\s*[*_`]*\s*(\d+)",
        rf":(?:{choices}):\s*[*_`]*\s*(\d+)",
        rf"(?im)^\s*(?:{choices})\s*[:=]\s*(\d+)\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def clean_title(value: str) -> str:
    value = re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", " ", value or "")
    value = re.sub(r":[A-Za-z0-9_]+:", " ", value)
    value = re.sub(r"^[^A-Za-z0-9]+", "", value).strip()
    return re.sub(r"\s+", " ", value).strip(" *_`#")


def clean_dex_description(value: str) -> str:
    """Keep OwO's prose while removing its personal/details block."""
    description = DEX_DETAIL_START_RE.split(value or "", maxsplit=1)[0]
    return description.strip()[:MAX_DESCRIPTION_LENGTH]


def likely_animal_emoji(text: str) -> tuple[str, int | None, bool]:
    blocked = {
        *STAT_ALIASES["hp"],
        *STAT_ALIASES["str"],
        *STAT_ALIASES["pr"],
        *STAT_ALIASES["wp"],
        *STAT_ALIASES["mag"],
        *STAT_ALIASES["mr"],
        "common",
        "uncommon",
        "rare",
        "epic",
        "mythical",
        "mythic",
        "legendary",
        "fabled",
        "special",
        "patreon",
        "cpatreon",
        "gem",
        "bot",
        "hidden",
        "distorted",
    }
    for name, emoji_id, animated in custom_emojis(text):
        if name.casefold() not in blocked:
            return name, emoji_id, animated
    pasted = re.search(r":([A-Za-z0-9_]+):", text or "")
    if pasted and pasted.group(1).casefold() not in blocked:
        return pasted.group(1), None, False
    return "", None, False


def first_embed_image(message: discord.Message) -> str:
    for embed in message.embeds:
        if embed.thumbnail and embed.thumbnail.url:
            return str(embed.thumbnail.url)
        if embed.image and embed.image.url:
            return str(embed.image.url)
    return ""


def parse_owo_animal_dex(message: discord.Message) -> AnimalDexRecord | None:
    """Parse both current OwO embeds and legacy/pasted dex layouts."""
    text = extract_message_text(message)
    rank_text = label_value(text, "Rank")
    aliases_text = label_value(text, "Alias", "Aliases")
    points_text = label_value(text, "Points")
    if not rank_text or not points_text:
        return None

    title_candidates: list[str] = []
    for embed in message.embeds:
        if embed.title:
            title_candidates.append(str(embed.title))
        if embed.author and embed.author.name:
            title_candidates.append(str(embed.author.name))
    display_name = next((clean_title(item) for item in title_candidates if clean_title(item)), "")
    if not display_name:
        return None

    aliases = [item.strip() for item in aliases_text.split(",") if item.strip()]
    if display_name.casefold() not in {item.casefold() for item in aliases}:
        aliases.insert(0, display_name)
    aliases = list(dict.fromkeys(aliases))
    animal_key = normalize_animal_key(aliases[0] if aliases else display_name)
    if not animal_key:
        return None

    description = ""
    for embed in message.embeds:
        candidate = str(embed.description or "").strip()
        if candidate:
            description = clean_dex_description(candidate)
            break

    # OwO's Count field is tied to the requesting zoo. Keep it out of the
    # public cache and derive the global total only from the Rarity line.
    rarity_text = label_value(text, "Rarity")
    total_caught = parse_int(rarity_text)
    emoji_name, emoji_id, emoji_animated = likely_animal_emoji(
        "\n".join(title_candidates) or text
    )
    now = int(time.time())
    return AnimalDexRecord(
        animal_key=animal_key,
        display_name=display_name,
        rank=normalize_rank(rank_text),
        description=description,
        aliases=tuple(aliases),
        total_caught=total_caught,
        rarity_text=rarity_text,
        points=parse_int(points_text),
        sell_text=label_value(text, "Sell"),
        sacrifice_text=label_value(text, "Sacrifice"),
        hp=stat_value(text, STAT_ALIASES["hp"]),
        strength=stat_value(text, STAT_ALIASES["str"]),
        pr=stat_value(text, STAT_ALIASES["pr"]),
        wp=stat_value(text, STAT_ALIASES["wp"]),
        mag=stat_value(text, STAT_ALIASES["mag"]),
        mr=stat_value(text, STAT_ALIASES["mr"]),
        emoji_name=emoji_name,
        emoji_id=emoji_id,
        emoji_animated=emoji_animated,
        image_url=first_embed_image(message),
        source="owo",
        updated_at=now,
    )


def parse_owo_dex_request(content: str, owo_prefix: str) -> str | None:
    first_line = next((line.strip() for line in (content or "").splitlines() if line.strip()), "")
    if not first_line:
        return None
    prefix = re.escape(normalize_owo_prefix(owo_prefix) or "w")
    match = re.match(
        rf"^(?:owo\s+(?:d|dex)|{prefix}\s*(?:d|dex))\s+(.+?)\s*$",
        first_line,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def is_owo_dex_refusal(text: str) -> bool:
    normalized = normalize_catalog_token(text)
    return bool(
        re.search(r"(?:could not|couldn t|couldnt|cannot|can t|cant|did not) find .* (?:zoo|animals?)", normalized)
        or "not in your zoo" in normalized
        or "don t have that animal" in normalized
        or "dont have that animal" in normalized
    )


class AnimalDexStore:
    def __init__(self, path: Path = DATABASE_FILE) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS animals (
                    animal_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    rank TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    total_caught INTEGER,
                    rarity_text TEXT NOT NULL DEFAULT '',
                    points INTEGER,
                    sell_text TEXT NOT NULL DEFAULT '',
                    sacrifice_text TEXT NOT NULL DEFAULT '',
                    hp INTEGER,
                    strength INTEGER,
                    pr INTEGER,
                    wp INTEGER,
                    mag INTEGER,
                    mr INTEGER,
                    emoji_name TEXT NOT NULL DEFAULT '',
                    emoji_id INTEGER,
                    emoji_animated INTEGER NOT NULL DEFAULT 0,
                    image_url TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS animal_aliases (
                    alias TEXT PRIMARY KEY,
                    animal_key TEXT NOT NULL REFERENCES animals(animal_key) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_animals_name ON animals(display_name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_animals_rank ON animals(rank);
                """
            )
        self.seed_special_catalog()

    def seed_special_catalog(self) -> None:
        now = special_catalog_updated_at() or int(time.time())
        with self._connect() as connection:
            for item in special_animals():
                stats = dict(item.get("stats") or {})
                aliases = tuple(str(value) for value in item.get("aliases", []) if str(value).strip())
                record = AnimalDexRecord(
                    animal_key=str(item.get("key") or normalize_animal_key(str(item.get("name", "")))),
                    display_name=str(item.get("name", "")),
                    rank="special",
                    description=str(item.get("event", "")),
                    aliases=aliases,
                    total_caught=parse_int(str(item.get("caught", ""))),
                    rarity_text=str(item.get("rarity", "")),
                    points=500,
                    sell_text="6000 Cowoncy",
                    sacrifice_text="5000 Essence",
                    hp=stats.get("hp"),
                    strength=stats.get("str"),
                    pr=stats.get("pr"),
                    wp=stats.get("wp"),
                    mag=stats.get("mag"),
                    mr=stats.get("mr"),
                    emoji_name=f"pet_{item.get('emoji_stem', '')}",
                    emoji_id=None,
                    emoji_animated=False,
                    image_url="",
                    source="owo_wiki_seed",
                    updated_at=now,
                )
                self._upsert(connection, record, preserve_newer=True)

    @staticmethod
    def _upsert(
        connection: sqlite3.Connection,
        record: AnimalDexRecord,
        *,
        preserve_newer: bool = False,
    ) -> None:
        if preserve_newer:
            existing = connection.execute(
                "SELECT updated_at, source FROM animals WHERE animal_key = ?",
                (record.animal_key,),
            ).fetchone()
            if existing and str(existing["source"]) == "owo":
                return
        connection.execute(
            """
            INSERT INTO animals (
                animal_key, display_name, rank, description, aliases_json,
                total_caught, rarity_text, points, sell_text, sacrifice_text,
                hp, strength, pr, wp, mag, mr, emoji_name, emoji_id,
                emoji_animated, image_url, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(animal_key) DO UPDATE SET
                display_name=excluded.display_name,
                rank=excluded.rank,
                description=CASE WHEN excluded.description != '' THEN excluded.description ELSE animals.description END,
                aliases_json=excluded.aliases_json,
                total_caught=COALESCE(excluded.total_caught, animals.total_caught),
                rarity_text=CASE WHEN excluded.rarity_text != '' THEN excluded.rarity_text ELSE animals.rarity_text END,
                points=COALESCE(excluded.points, animals.points),
                sell_text=CASE WHEN excluded.sell_text != '' THEN excluded.sell_text ELSE animals.sell_text END,
                sacrifice_text=CASE WHEN excluded.sacrifice_text != '' THEN excluded.sacrifice_text ELSE animals.sacrifice_text END,
                hp=COALESCE(excluded.hp, animals.hp),
                strength=COALESCE(excluded.strength, animals.strength),
                pr=COALESCE(excluded.pr, animals.pr),
                wp=COALESCE(excluded.wp, animals.wp),
                mag=COALESCE(excluded.mag, animals.mag),
                mr=COALESCE(excluded.mr, animals.mr),
                emoji_name=CASE WHEN excluded.emoji_name != '' THEN excluded.emoji_name ELSE animals.emoji_name END,
                emoji_id=COALESCE(excluded.emoji_id, animals.emoji_id),
                emoji_animated=excluded.emoji_animated,
                image_url=CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE animals.image_url END,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                record.animal_key,
                record.display_name,
                record.rank,
                record.description,
                json.dumps(record.aliases, ensure_ascii=False),
                record.total_caught,
                record.rarity_text,
                record.points,
                record.sell_text,
                record.sacrifice_text,
                record.hp,
                record.strength,
                record.pr,
                record.wp,
                record.mag,
                record.mr,
                record.emoji_name,
                record.emoji_id,
                int(record.emoji_animated),
                record.image_url,
                record.source,
                record.updated_at,
            ),
        )
        connection.execute("DELETE FROM animal_aliases WHERE animal_key = ?", (record.animal_key,))
        for alias in dict.fromkeys((record.display_name, record.animal_key, *record.aliases)):
            normalized = normalize_catalog_token(alias)
            if normalized:
                connection.execute(
                    "INSERT OR IGNORE INTO animal_aliases(alias, animal_key) VALUES (?, ?)",
                    (normalized, record.animal_key),
                )

    def upsert(self, record: AnimalDexRecord) -> None:
        with self._connect() as connection:
            self._upsert(connection, record)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AnimalDexRecord:
        return AnimalDexRecord(
            animal_key=str(row["animal_key"]),
            display_name=str(row["display_name"]),
            rank=str(row["rank"]),
            description=str(row["description"]),
            aliases=tuple(json.loads(str(row["aliases_json"]))),
            total_caught=row["total_caught"],
            rarity_text=str(row["rarity_text"]),
            points=row["points"],
            sell_text=str(row["sell_text"]),
            sacrifice_text=str(row["sacrifice_text"]),
            hp=row["hp"],
            strength=row["strength"],
            pr=row["pr"],
            wp=row["wp"],
            mag=row["mag"],
            mr=row["mr"],
            emoji_name=str(row["emoji_name"]),
            emoji_id=row["emoji_id"],
            emoji_animated=bool(row["emoji_animated"]),
            image_url=str(row["image_url"]),
            source=str(row["source"]),
            updated_at=int(row["updated_at"]),
        )

    def all_records(self) -> tuple[AnimalDexRecord, ...]:
        with self._connect() as connection:
            return tuple(self._from_row(row) for row in connection.execute("SELECT * FROM animals ORDER BY animal_key"))

    def find(self, query: str) -> AnimalDexRecord | None:
        normalized = normalize_catalog_token(query)
        if not normalized:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT animals.* FROM animal_aliases
                JOIN animals USING(animal_key)
                WHERE animal_aliases.alias = ?
                """,
                (normalized,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT * FROM animals
                    WHERE display_name LIKE ? OR animal_key LIKE ?
                    ORDER BY CASE WHEN display_name LIKE ? THEN 0 ELSE 1 END, updated_at DESC
                    LIMIT 1
                    """,
                    (f"%{query}%", f"%{normalize_animal_key(query)}%", query),
                ).fetchone()
        return self._from_row(row) if row else None

    def suggest(self, query: str, limit: int = 25) -> list[tuple[str, str]]:
        normalized = normalize_catalog_token(query)
        like = f"%{normalized}%"
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT animals.display_name, animals.animal_key
                FROM animals LEFT JOIN animal_aliases USING(animal_key)
                WHERE ? = '' OR lower(animals.display_name) LIKE ? OR animal_aliases.alias LIKE ?
                ORDER BY animals.display_name COLLATE NOCASE
                LIMIT ?
                """,
                (normalized, like, like, int(limit)),
            ).fetchall()
        return [(str(row["display_name"]), str(row["animal_key"])) for row in rows]


class AnimalDex(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = AnimalDexStore()
        setattr(bot, "animal_dex_store", self.store)

    async def cog_load(self) -> None:
        await asyncio.to_thread(self.store.initialize)
        logger.info("Animal dex storage ready at %s", DATABASE_FILE)

    def cog_unload(self) -> None:
        if getattr(self.bot, "animal_dex_store", None) is self.store:
            delattr(self.bot, "animal_dex_store")

    def build_embed(self, record: AnimalDexRecord) -> discord.Embed:
        special = resolve_special_animal(record.display_name)
        emoji_key = str(special.get("emoji_stem", "")) if special else ""
        if not emoji_key:
            emoji_key = normalize_animal_key(record.display_name)
        pet = ui_emoji_text(self.bot, f"pet_{emoji_key}", "🐾")
        rank = resolve_rank(record.rank)
        rank_text = ui_emoji_text(self.bot, rank.emoji_key, "") if rank else ""
        description_parts: list[str] = []
        prose = clean_dex_description(record.description)
        if prose:
            description_parts.append(f"{prose}\n")

        rank_label = record.rank.replace("_", " ").title() or "Unknown"
        rank_value = " ".join(part for part in (rank_text, rank_label) if part)
        rarity = record.rarity_text.strip()
        if not rarity and record.total_caught is not None:
            rarity = f"{record.total_caught:,} total caught"
        aliases = record.aliases[:12] or (record.animal_key,)
        alias_text = ", ".join(f"`{alias}`" for alias in aliases)
        description_parts.extend(
            (
                f"**Rank:** {rank_value}",
                f"**Rarity:** {rarity or 'Unknown'}",
                f"**Aliases:** {alias_text}",
                f"**Points:** {record.points:,}" if record.points is not None else "**Points:** Unknown",
                f"**Sell:** {record.sell_text or 'Unknown'}",
                f"**Sacrifice:** {record.sacrifice_text or 'Unknown'}",
            )
        )
        stat_pairs = (
            ("hp", record.hp, "HP"),
            ("att", record.strength, "ATT"),
            ("pr", record.pr, "PR"),
            ("wp", record.wp, "WP"),
            ("mag", record.mag, "MAG"),
            ("mr", record.mr, "MR"),
        )
        stat_cells = [
            f"{ui_emoji_text(self.bot, f'stat_{key}', label)} `{value if value is not None else '?'}`"
            for key, value, label in stat_pairs
        ]
        description_parts.append(
            " ".join(stat_cells[:3]) + "\n" + " ".join(stat_cells[3:])
        )
        embed = discord.Embed(
            title=f"{pet} {record.display_name}",
            description="\n".join(description_parts)[:4096],
            color=0x57F287 if record.rank == "special" else 0x5865F2,
            timestamp=datetime.fromtimestamp(record.updated_at, tz=timezone.utc),
        )
        if record.image_url:
            embed.set_thumbnail(url=record.image_url)
        return embed

    async def send_lookup(
        self,
        destination: discord.abc.Messageable,
        query: str,
        *,
        reference: discord.Message | None = None,
        silent_if_missing: bool = False,
    ) -> bool:
        record = await asyncio.to_thread(self.store.find, query)
        if record is None:
            if silent_if_missing:
                return False
            await destination.send(
                f"I do not have a Dex record for `{query}` yet. Dex it with OwO in any shared server to teach the catalog.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        await destination.send(
            embed=self.build_embed(record),
            reference=reference.to_reference(fail_if_not_exists=False) if reference else None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if not message.author.bot:
            helper_prefix = await get_guild_helper_prefix(message.guild.id)
            direct = parse_helper_command_argument(
                message.content or "",
                helper_prefix,
                {
                    "h animal dex",
                    "hanimal dex",
                    "h pet dex",
                    "hpet dex",
                    "h adex",
                    "hadex",
                    "h ad",
                },
            )
            if direct is not None:
                if direct:
                    await self.send_lookup(message.channel, direct, reference=message)
                else:
                    await message.reply(
                        f"Use `{helper_prefix} animal dex <animal>` to search the public animal catalog.",
                        mention_author=False,
                    )
                return

            compact = parse_helper_command_argument(
                message.content or "",
                helper_prefix,
                {"had"},
            )
            if compact is not None:
                if compact:
                    await self.send_lookup(
                        message.channel,
                        compact,
                        reference=message,
                        silent_if_missing=True,
                    )
                return

            # Ordinary OwO Dex requests are observed only through OwO's reply.
            # Do not answer or schedule fallbacks that would duplicate other
            # dedicated Dex bots in the channel.
            return

        if message.author.id != OWO_BOT_ID:
            return
        record = parse_owo_animal_dex(message)
        if record:
            await asyncio.to_thread(self.store.upsert, record)
            logger.info("Updated public OwO Dex record for %s", record.animal_key)
            return

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.author.id != OWO_BOT_ID:
            return
        record = parse_owo_animal_dex(after)
        if record:
            await asyncio.to_thread(self.store.upsert, record)

    @app_commands.command(name="animal-dex", description="Search the public OwO animal Dex catalog.")
    @app_commands.describe(animal="Animal name or alias")
    async def animal_dex(self, interaction: discord.Interaction, animal: str) -> None:
        record = await asyncio.to_thread(self.store.find, animal)
        if record is None:
            await interaction.response.send_message(
                f"I do not have a Dex record for `{animal}` yet. Dex it with OwO in any shared server to teach the catalog.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=self.build_embed(record))

    @animal_dex.autocomplete("animal")
    async def animal_dex_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        suggestions = await asyncio.to_thread(self.store.suggest, current, 25)
        return [app_commands.Choice(name=name[:100], value=key[:100]) for name, key in suggestions]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnimalDex(bot))
