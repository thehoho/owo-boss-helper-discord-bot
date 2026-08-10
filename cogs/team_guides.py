"""Trusted, versioned, UI-authored public battle-team guides."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from .game_catalog import (
    PASSIVES,
    RANKS,
    WEAPONS,
    CatalogEntry,
    normalize_catalog_token,
    resolve_passive,
    resolve_rank,
    resolve_special_animal,
    resolve_weapon,
)
from .helper_prefix import get_guild_helper_prefix, parse_helper_command_argument
from .team_templates import STANDARD_ANIMAL_NAMES, normalize_animal_emoji_alias
from .ui_emojis import ui_emoji_text


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "team_guides.db"
EDITOR_TIMEOUT_SECONDS = 20 * 60
MAX_GUIDE_NAME = 80
MAX_GUIDE_DESCRIPTION = 3000
MAX_FULL_GUIDE = 4000
MAX_GUIDE_ALIASES = 12
MAX_GUIDE_CATEGORIES = 8
FULL_GUIDE_PAGE_LENGTH = 3800
GUIDE_VARIABLE_RE = re.compile(r"\{([A-Za-z0-9_ -]{1,64})\}")
GUIDE_STAT_ALIASES = {
    "hp": "hp",
    "health": "hp",
    "hpstat": "hp",
    "att": "att",
    "attack": "att",
    "str": "att",
    "strength": "att",
    "attstat": "att",
    "pr": "pr",
    "physicalresistance": "pr",
    "prstat": "pr",
    "wp": "wp",
    "weaponpoint": "wp",
    "weaponpoints": "wp",
    "wpstat": "wp",
    "mag": "mag",
    "magic": "mag",
    "magstat": "mag",
    "mr": "mr",
    "magicresistance": "mr",
    "magicalresistance": "mr",
    "mrstat": "mr",
}
GUIDE_WEAPON_VARIABLE_ALIASES = {
    "pdagger": "pd",
}
GUIDE_PASSIVE_VARIABLE_ALIASES = {
    "manamtap": "mtap",
    "snailpassive": "snail",
    "pshgen": "hgen",
}


class GuideAliasConflict(ValueError):
    pass


@dataclass(frozen=True)
class GuideSlot:
    position: int
    animal: str
    level: int | None = 50
    animal_rank: str = ""
    weapons: str = ""
    notes: str = ""


@dataclass(frozen=True)
class TeamGuide:
    guide_id: int
    name: str
    aliases: tuple[str, ...]
    categories: tuple[str, ...]
    authors: str
    description: str
    full_guide: str
    viability: int
    ease: int
    slots: tuple[GuideSlot, ...]
    creator_id: int
    updated_by: int
    version: int
    created_at: int
    updated_at: int


@dataclass
class GuideDraft:
    editor_id: int
    guide_id: int | None = None
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    authors: str = ""
    description: str = ""
    full_guide: str = ""
    viability: int = 3
    ease: int = 3
    slots: dict[int, GuideSlot] = field(default_factory=dict)


class ClosingConnection(sqlite3.Connection):
    def __enter__(self) -> "ClosingConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def split_values(value: str, limit: int) -> list[str]:
    parts = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"[,\n]", value or "")]
    return list(dict.fromkeys(item for item in parts if item))[:limit]


def normalize_guide_alias(value: str) -> str:
    return normalize_catalog_token(value).replace(" ", "_")


def clamp_rating(value: str, default: int = 3) -> int:
    try:
        return max(1, min(5, int(value.strip())))
    except (TypeError, ValueError):
        return default


class TeamGuideStore:
    def __init__(self, path: Path = DATABASE_FILE) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS guide_experts (
                    user_id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    granted_by INTEGER NOT NULL,
                    granted_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS team_guides (
                    guide_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    authors TEXT NOT NULL,
                    description TEXT NOT NULL,
                    full_guide TEXT NOT NULL DEFAULT '',
                    viability INTEGER NOT NULL,
                    ease INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    updated_by INTEGER NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS team_guide_aliases (
                    alias TEXT PRIMARY KEY,
                    guide_id INTEGER NOT NULL REFERENCES team_guides(guide_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS team_guide_slots (
                    guide_id INTEGER NOT NULL REFERENCES team_guides(guide_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    animal TEXT NOT NULL,
                    level INTEGER,
                    animal_rank TEXT NOT NULL DEFAULT '',
                    weapons TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (guide_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_team_guides_name ON team_guides(name COLLATE NOCASE);
                """
            )
            guide_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(team_guides)").fetchall()
            }
            if "full_guide" not in guide_columns:
                connection.execute(
                    "ALTER TABLE team_guides ADD COLUMN full_guide TEXT NOT NULL DEFAULT ''"
                )

    def set_expert(self, user_id: int, display_name: str, enabled: bool, granted_by: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO guide_experts(user_id, display_name, enabled, granted_by, granted_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    enabled=excluded.enabled,
                    granted_by=excluded.granted_by,
                    granted_at=excluded.granted_at
                """,
                (int(user_id), display_name[:100], int(enabled), int(granted_by), int(time.time())),
            )

    def is_expert(self, user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM guide_experts WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        return bool(row and row["enabled"])

    @staticmethod
    def _from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> TeamGuide:
        slot_rows = connection.execute(
            "SELECT * FROM team_guide_slots WHERE guide_id = ? ORDER BY position",
            (int(row["guide_id"]),),
        ).fetchall()
        slots = tuple(
            GuideSlot(
                position=int(slot["position"]),
                animal=str(slot["animal"]),
                level=slot["level"],
                animal_rank=str(slot["animal_rank"]),
                weapons=str(slot["weapons"]),
                notes=str(slot["notes"]),
            )
            for slot in slot_rows
        )
        return TeamGuide(
            guide_id=int(row["guide_id"]),
            name=str(row["name"]),
            aliases=tuple(json.loads(str(row["aliases_json"]))),
            categories=tuple(json.loads(str(row["categories_json"]))),
            authors=str(row["authors"]),
            description=str(row["description"]),
            full_guide=str(row["full_guide"]),
            viability=int(row["viability"]),
            ease=int(row["ease"]),
            slots=slots,
            creator_id=int(row["creator_id"]),
            updated_by=int(row["updated_by"]),
            version=int(row["version"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def save(self, draft: GuideDraft, editor_id: int) -> TeamGuide:
        aliases = list(dict.fromkeys(
            normalize_guide_alias(item)
            for item in [*draft.aliases, draft.name]
            if normalize_guide_alias(item)
        ))[: MAX_GUIDE_ALIASES + 1]
        if not aliases:
            raise ValueError("At least one searchable alias is required.")
        full_guide = (draft.full_guide or "")[:MAX_FULL_GUIDE]
        now = int(time.time())
        with self._connect() as connection:
            for alias in aliases:
                row = connection.execute(
                    "SELECT guide_id FROM team_guide_aliases WHERE alias = ?",
                    (alias,),
                ).fetchone()
                if row and int(row["guide_id"]) != int(draft.guide_id or 0):
                    raise GuideAliasConflict(alias)

            if draft.guide_id:
                current = connection.execute(
                    "SELECT * FROM team_guides WHERE guide_id = ?",
                    (int(draft.guide_id),),
                ).fetchone()
                if current is None:
                    raise ValueError("That guide no longer exists.")
                version = int(current["version"]) + 1
                connection.execute(
                    """
                    UPDATE team_guides SET
                        name=?, aliases_json=?, categories_json=?, authors=?, description=?, full_guide=?,
                        viability=?, ease=?, updated_by=?, version=?, updated_at=?
                    WHERE guide_id=?
                    """,
                    (
                        draft.name,
                        json.dumps(aliases),
                        json.dumps(draft.categories),
                        draft.authors,
                        draft.description,
                        full_guide,
                        draft.viability,
                        draft.ease,
                        int(editor_id),
                        version,
                        now,
                        int(draft.guide_id),
                    ),
                )
                guide_id = int(draft.guide_id)
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO team_guides(
                        name, aliases_json, categories_json, authors, description, full_guide,
                        viability, ease, creator_id, updated_by, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        draft.name,
                        json.dumps(aliases),
                        json.dumps(draft.categories),
                        draft.authors,
                        draft.description,
                        full_guide,
                        draft.viability,
                        draft.ease,
                        int(editor_id),
                        int(editor_id),
                        now,
                        now,
                    ),
                )
                guide_id = int(cursor.lastrowid)

            connection.execute("DELETE FROM team_guide_aliases WHERE guide_id = ?", (guide_id,))
            connection.executemany(
                "INSERT INTO team_guide_aliases(alias, guide_id) VALUES (?, ?)",
                ((alias, guide_id) for alias in aliases),
            )
            connection.execute("DELETE FROM team_guide_slots WHERE guide_id = ?", (guide_id,))
            connection.executemany(
                """
                INSERT INTO team_guide_slots(
                    guide_id, position, animal, level, animal_rank, weapons, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        guide_id,
                        slot.position,
                        slot.animal,
                        slot.level,
                        slot.animal_rank,
                        slot.weapons,
                        slot.notes,
                    )
                    for slot in sorted(draft.slots.values(), key=lambda item: item.position)
                ),
            )
            row = connection.execute("SELECT * FROM team_guides WHERE guide_id = ?", (guide_id,)).fetchone()
            assert row is not None
            return self._from_row(connection, row)

    def find(self, query: str) -> TeamGuide | None:
        normalized = normalize_guide_alias(query)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT team_guides.* FROM team_guide_aliases
                JOIN team_guides USING(guide_id)
                WHERE team_guide_aliases.alias = ?
                """,
                (normalized,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM team_guides WHERE lower(name) = ? LIMIT 1",
                    (query.casefold().strip(),),
                ).fetchone()
            return self._from_row(connection, row) if row else None

    def list_guides(self, query: str = "", limit: int = 25) -> list[TeamGuide]:
        normalized = normalize_guide_alias(query)
        text_query = query.casefold().strip()
        with self._connect() as connection:
            if normalized:
                rows = connection.execute(
                    """
                    SELECT DISTINCT team_guides.* FROM team_guides
                    LEFT JOIN team_guide_aliases USING(guide_id)
                    WHERE lower(team_guides.name) LIKE ?
                       OR lower(team_guides.authors) LIKE ?
                       OR lower(team_guides.categories_json) LIKE ?
                       OR team_guide_aliases.alias LIKE ?
                    ORDER BY team_guides.updated_at DESC LIMIT ?
                    """,
                    (
                        f"%{text_query}%",
                        f"%{text_query}%",
                        f"%{text_query}%",
                        f"%{normalized}%",
                        int(limit),
                    ),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM team_guides ORDER BY updated_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            return [self._from_row(connection, row) for row in rows]

    def related(self, guide: TeamGuide, limit: int = 5) -> list[TeamGuide]:
        candidates = self.list_guides(limit=100)
        categories = {normalize_catalog_token(value) for value in guide.categories}
        ranked = [
            candidate
            for candidate in candidates
            if candidate.guide_id != guide.guide_id
            and categories.intersection(normalize_catalog_token(value) for value in candidate.categories)
        ]
        return ranked[:limit]


def draft_from_guide(guide: TeamGuide, editor_id: int) -> GuideDraft:
    return GuideDraft(
        editor_id=editor_id,
        guide_id=guide.guide_id,
        name=guide.name,
        aliases=list(guide.aliases),
        categories=list(guide.categories),
        authors=guide.authors,
        description=guide.description,
        full_guide=guide.full_guide,
        viability=guide.viability,
        ease=guide.ease,
        slots={slot.position: slot for slot in guide.slots},
    )


def star_rating(value: int) -> str:
    return "⭐" * max(1, min(5, value)) + "☆" * (5 - max(1, min(5, value)))


def animal_emoji_key(animal: str) -> str:
    special = resolve_special_animal(animal)
    if special:
        return f"pet_{special.get('emoji_stem', '')}"
    return f"pet_{normalize_catalog_token(animal).replace(' ', '')}"


def resolve_compact_catalog_entry(
    entries: tuple[CatalogEntry, ...],
    value: str,
) -> CatalogEntry | None:
    compact = normalize_catalog_token(value).replace(" ", "")
    for entry in entries:
        if any(
            normalize_catalog_token(alias).replace(" ", "") == compact
            for alias in entry.aliases
        ):
            return entry
    return None


def guide_variable_emoji_key(value: str) -> str | None:
    compact = normalize_catalog_token(value).replace(" ", "")
    if compact.startswith("fp"):
        passive_value = compact[2:]
        passive_value = GUIDE_PASSIVE_VARIABLE_ALIASES.get(
            passive_value,
            passive_value,
        )
        passive = resolve_passive(passive_value) or resolve_compact_catalog_entry(
            PASSIVES,
            passive_value,
        )
        return passive.emoji_key if passive else None
    if compact.startswith("w"):
        weapon_value = compact[1:]
        weapon_value = GUIDE_WEAPON_VARIABLE_ALIASES.get(
            weapon_value,
            weapon_value,
        )
        weapon = resolve_weapon(weapon_value) or resolve_compact_catalog_entry(
            WEAPONS,
            weapon_value,
        )
        return weapon.emoji_key if weapon else None
    if compact.startswith("a"):
        animal_value = compact[1:]
        special = resolve_special_animal(animal_value)
        if special:
            return f"pet_{special.get('emoji_stem', '')}"
        animal = normalize_animal_emoji_alias(animal_value)
        if animal in STANDARD_ANIMAL_NAMES:
            return animal_emoji_key(animal)
        return None
    if compact.startswith("s"):
        stat = GUIDE_STAT_ALIASES.get(compact[1:])
        return f"stat_{stat}" if stat else None
    if compact.startswith("r"):
        rank_value = compact[1:]
        rank = resolve_rank(rank_value) or resolve_compact_catalog_entry(
            RANKS,
            rank_value,
        )
        return rank.emoji_key if rank else None
    return None


def render_guide_markdown(bot: commands.Bot, value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        emoji_key = guide_variable_emoji_key(match.group(1))
        if emoji_key is None:
            return match.group(0)
        return ui_emoji_text(bot, emoji_key, match.group(0))

    return GUIDE_VARIABLE_RE.sub(replace, value or "")


def unresolved_guide_variables(value: str) -> tuple[str, ...]:
    unresolved = [
        match.group(1)
        for match in GUIDE_VARIABLE_RE.finditer(value or "")
        if guide_variable_emoji_key(match.group(1)) is None
    ]
    return tuple(dict.fromkeys(unresolved))


def truncate_rendered_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    candidate = value[: max(1, limit - 1)]
    if candidate.rfind("<") > candidate.rfind(">"):
        candidate = candidate[:candidate.rfind("<")]
    return candidate.rstrip() + "…"


def paginate_guide_markdown(value: str) -> tuple[str, ...]:
    remaining = value.strip()
    if not remaining:
        return ()
    pages: list[str] = []
    while len(remaining) > FULL_GUIDE_PAGE_LENGTH:
        cut = FULL_GUIDE_PAGE_LENGTH
        if remaining[:cut].rfind("<") > remaining[:cut].rfind(">"):
            cut = remaining[:cut].rfind("<")
        newline = remaining.rfind("\n", 0, cut)
        space = remaining.rfind(" ", 0, cut)
        if newline >= FULL_GUIDE_PAGE_LENGTH // 2:
            cut = newline
        elif space >= FULL_GUIDE_PAGE_LENGTH // 2:
            cut = space
        cut = max(1, cut)
        pages.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        pages.append(remaining)
    return tuple(pages)


def build_full_guide_embeds(bot: commands.Bot, guide: TeamGuide) -> tuple[discord.Embed, ...]:
    rendered = render_guide_markdown(bot, guide.full_guide)
    pages = paginate_guide_markdown(rendered)
    embeds: list[discord.Embed] = []
    for index, page in enumerate(pages, start=1):
        embed = discord.Embed(
            title=f"📖 {guide.name} — Full guide"[:256],
            description=page,
            color=0x5865F2,
        )
        footer = f"Guide v{guide.version}"
        if len(pages) > 1:
            footer += f" • Page {index}/{len(pages)}"
        embed.set_footer(text=footer)
        embeds.append(embed)
    return tuple(embeds)


def build_emoji_variable_help_embed(bot: commands.Bot) -> discord.Embed:
    tick = chr(96)

    def example(variable: str) -> str:
        rendered = render_guide_markdown(bot, variable)
        return f"{tick}{variable}{tick} → {rendered}"

    description = (
        "Use optional Neon-style variables anywhere in the summary, full guide, "
        "or slot notes. Preview resolves known names into the bot's portable "
        "application emojis. Existing Discord Markdown, Unicode emojis, and custom "
        "emoji markup stay unchanged.\n\n"
        f"**Weapons**\n{example('{wsword}')}  {example('{wpdagger}')}  {example('{wascept}')}\n"
        f"**Passives**\n{example('{fpstr}')}  {example('{fplifesteal}')}  {example('{fpmana_mtap}')}\n"
        f"**Animals**\n{example('{afish}')}  {example('{agfish}')}  {example('{abeeday}')}\n"
        f"**Base stats**\n{example('{shp}')}  {example('{satt}')}  {example('{swp}')}  {example('{smag}')}\n"
        f"**Ranks**\n{example('{rlegendary}')}  {example('{rfabled}')}\n\n"
        "-# Prefixes: w = weapon, fp = passive, a = animal, s = base stat, r = rank. "
        "Aliases such as dagger, ascept, lifesteal, gfish, and beeday are accepted."
    )
    return discord.Embed(title="💡 Team-guide emoji variables", description=description, color=0xFEE75C)


def render_weapon_specs(bot: commands.Bot, value: str) -> str:
    rendered: list[str] = []
    for raw_spec in [item.strip() for item in (value or "").split(";") if item.strip()][:3]:
        body, _, rank_text = raw_spec.partition("@")
        tokens = [item.strip() for item in body.split("+") if item.strip()]
        if not tokens:
            continue
        weapon = resolve_weapon(tokens[0])
        chunk = ui_emoji_text(bot, weapon.emoji_key, f"`{tokens[0]}`") if weapon else f"`{tokens[0]}`"
        for token in tokens[1:]:
            passive = resolve_passive(token)
            chunk += " " + (ui_emoji_text(bot, passive.emoji_key, f"`{token}`") if passive else f"`{token}`")
        rank = resolve_rank(rank_text) if rank_text else None
        if rank:
            chunk += " " + ui_emoji_text(bot, rank.emoji_key, rank.name)
        rendered.append(chunk)
    return "  ".join(rendered) or "No weapon requirement"


def build_guide_embed(bot: commands.Bot, guide: TeamGuide) -> discord.Embed:
    primary_alias = guide.aliases[0] if guide.aliases else normalize_guide_alias(guide.name)
    rendered_description = render_guide_markdown(bot, guide.description)
    embed = discord.Embed(
        title=f"{guide.name} — {primary_alias}",
        description=truncate_rendered_text(rendered_description, MAX_GUIDE_DESCRIPTION),
        color=0x57F287,
    )
    properties = (
        f"🔹 **Categories:** {', '.join(guide.categories)}\n"
        f"🔹 **Aliases:** {', '.join(guide.aliases[:MAX_GUIDE_ALIASES])}\n"
        f"🔹 **Authors:** {guide.authors}\n"
        f"🔹 **Viability:** {star_rating(guide.viability)}\n"
        f"🔹 **Ease of creation:** {star_rating(guide.ease)}"
    )
    embed.add_field(name="Properties", value=properties[:1024], inline=False)
    composition: list[str] = []
    for slot in sorted(guide.slots, key=lambda item: item.position):
        pet = ui_emoji_text(bot, animal_emoji_key(slot.animal), "🐾")
        rank = resolve_rank(slot.animal_rank)
        rank_icon = ui_emoji_text(bot, rank.emoji_key, "") if rank else ""
        level = f"L.{slot.level}" if slot.level is not None else "Any level"
        line = f"**[{slot.position}]** {level} {pet} **{slot.animal}** {rank_icon}\n{render_weapon_specs(bot, slot.weapons)}"
        if slot.notes:
            rendered_notes = render_guide_markdown(bot, slot.notes)
            line += f"\n-# {truncate_rendered_text(rendered_notes, 250)}"
        composition.append(line.strip())
    embed.add_field(
        name="Composition",
        value=truncate_rendered_text("\n\n".join(composition), 1024),
        inline=False,
    )
    embed.set_footer(text=f"Guide v{guide.version} • Created by Discord user {guide.creator_id} • Last editor {guide.updated_by}")
    return embed


def build_editor_embed(draft: GuideDraft) -> discord.Embed:
    completed_slots = len([slot for slot in draft.slots.values() if slot.animal.strip()])
    missing: list[str] = []
    if not draft.name:
        missing.append("basics")
    if not draft.aliases:
        missing.append("alias")
    if not draft.categories:
        missing.append("category")
    if not draft.description:
        missing.append("description")
    if completed_slots < 3:
        missing.append(f"{3 - completed_slots} composition slot(s)")
    variable_sources = [
        draft.description,
        draft.full_guide,
        *(slot.notes for slot in draft.slots.values()),
    ]
    unresolved = tuple(
        dict.fromkeys(
            variable
            for source in variable_sources
            for variable in unresolved_guide_variables(source)
        )
    )
    description = (
        "Use the buttons below to build a visual, versioned team guide. "
        "Nothing is published until you press **Publish**.\n"
        "-# **Basics**, **Full guide**, and slot notes accept Discord Markdown. "
        "Optional Neon-style emoji variables such as {wsword}, {fpstr}, and "
        "{afish} resolve through **Preview**. Open **Emoji variables** for examples.\n\n"
        f"**Name:** {draft.name or 'Not set'}\n"
        f"**Aliases:** {', '.join(draft.aliases) or 'Not set'}\n"
        f"**Categories:** {', '.join(draft.categories) or 'Not set'}\n"
        f"**Authors:** {draft.authors or 'Not set'}\n"
        f"**Ratings:** Viability {draft.viability}/5 • Ease {draft.ease}/5\n"
        f"**Composition:** {completed_slots}/3 slots\n"
        f"**Full guide:** {len(draft.full_guide)}/{MAX_FULL_GUIDE} characters"
        f"{' • optional' if not draft.full_guide else ''}\n\n"
        f"**Still required:** {', '.join(missing) if missing else 'Ready to publish'}"
    )
    if unresolved:
        description += (
            "\n\n⚠️ **Unknown emoji variables:** "
            + ", ".join(f"{{{value}}}" for value in unresolved[:8])
        )
    return discord.Embed(title="🛠️ Trusted Team Guide Editor", description=description, color=0xFEE75C)


class GuideBasicsModal(discord.ui.Modal, title="Team guide basics"):
    name = discord.ui.TextInput(label="Guide name", max_length=MAX_GUIDE_NAME)
    aliases = discord.ui.TextInput(label="Search aliases (comma-separated)", max_length=300)
    categories = discord.ui.TextInput(label="Categories (comma-separated)", max_length=300)
    authors = discord.ui.TextInput(label="Displayed authors / short names", max_length=300)
    description = discord.ui.TextInput(
        label="How to use this team (Markdown supported)",
        style=discord.TextStyle.paragraph,
        max_length=MAX_GUIDE_DESCRIPTION,
    )

    def __init__(self, view: "GuideEditorView") -> None:
        super().__init__(timeout=300)
        self.view = view
        self.name.default = view.draft.name or None
        self.aliases.default = ", ".join(view.draft.aliases) or None
        self.categories.default = ", ".join(view.draft.categories) or None
        self.authors.default = view.draft.authors or None
        self.description.default = view.draft.description or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.view.draft.name = str(self.name).strip()
        self.view.draft.aliases = split_values(str(self.aliases), MAX_GUIDE_ALIASES)
        self.view.draft.categories = split_values(str(self.categories), MAX_GUIDE_CATEGORIES)
        self.view.draft.authors = str(self.authors).strip()
        self.view.draft.description = str(self.description).strip()
        await interaction.response.edit_message(embed=build_editor_embed(self.view.draft), view=self.view)


class GuideFullTextModal(discord.ui.Modal, title="Optional full team guide"):
    full_guide = discord.ui.TextInput(
        label="Detailed guide (Markdown + emoji variables)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=MAX_FULL_GUIDE,
        placeholder="Add detailed notes, alternatives, matchup advice, and weapon quality guidance.",
    )

    def __init__(self, view: "GuideEditorView") -> None:
        super().__init__(timeout=300)
        self.view = view
        self.full_guide.default = view.draft.full_guide or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.view.draft.full_guide = str(self.full_guide).strip()
        await interaction.response.edit_message(
            embed=build_editor_embed(self.view.draft),
            view=self.view,
        )


class GuideRatingsModal(discord.ui.Modal, title="Team guide ratings"):
    viability = discord.ui.TextInput(label="Viability (1-5)", min_length=1, max_length=1)
    ease = discord.ui.TextInput(label="Ease of creation (1-5)", min_length=1, max_length=1)

    def __init__(self, view: "GuideEditorView") -> None:
        super().__init__(timeout=300)
        self.view = view
        self.viability.default = str(view.draft.viability)
        self.ease.default = str(view.draft.ease)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.view.draft.viability = clamp_rating(str(self.viability), self.view.draft.viability)
        self.view.draft.ease = clamp_rating(str(self.ease), self.view.draft.ease)
        await interaction.response.edit_message(embed=build_editor_embed(self.view.draft), view=self.view)


class GuideSlotModal(discord.ui.Modal):
    animal = discord.ui.TextInput(label="Animal name or alias", max_length=100)
    level = discord.ui.TextInput(label="Level (optional)", required=False, max_length=3)
    animal_rank = discord.ui.TextInput(label="Animal rank/tier (optional)", required=False, max_length=30)
    weapons = discord.ui.TextInput(
        label="Weapons: weapon + passives @ tier; ...",
        required=False,
        max_length=500,
        placeholder="pd + mtap + crit @ legendary; crune + res @ fabled",
    )
    notes = discord.ui.TextInput(
        label="Slot notes (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, view: "GuideEditorView", position: int) -> None:
        super().__init__(title=f"Composition slot {position}", timeout=300)
        self.view = view
        self.position = position
        current = view.draft.slots.get(position)
        if current:
            self.animal.default = current.animal
            self.level.default = str(current.level) if current.level is not None else None
            self.animal_rank.default = current.animal_rank or None
            self.weapons.default = current.weapons or None
            self.notes.default = current.notes or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        level_text = str(self.level).strip()
        level = int(level_text) if level_text.isdigit() else None
        if level is not None:
            level = max(1, min(999, level))
        self.view.draft.slots[self.position] = GuideSlot(
            position=self.position,
            animal=str(self.animal).strip(),
            level=level,
            animal_rank=str(self.animal_rank).strip(),
            weapons=str(self.weapons).strip(),
            notes=str(self.notes).strip(),
        )
        await interaction.response.edit_message(embed=build_editor_embed(self.view.draft), view=self.view)


class GuideEditorView(discord.ui.View):
    def __init__(self, cog: "TeamGuides", draft: GuideDraft) -> None:
        super().__init__(timeout=EDITOR_TIMEOUT_SECONDS)
        self.cog = cog
        self.draft = draft

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.draft.editor_id:
            await interaction.response.send_message("This guide editor belongs to another expert.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Basics", emoji="📝", style=discord.ButtonStyle.primary, row=0)
    async def basics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(GuideBasicsModal(self))

    @discord.ui.button(label="Ratings", emoji="⭐", style=discord.ButtonStyle.secondary, row=0)
    async def ratings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(GuideRatingsModal(self))

    @discord.ui.button(label="Full guide", emoji="📖", style=discord.ButtonStyle.secondary, row=0)
    async def full_guide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(GuideFullTextModal(self))

    @discord.ui.button(label="Slot 1", style=discord.ButtonStyle.secondary, row=1)
    async def slot_1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(GuideSlotModal(self, 1))

    @discord.ui.button(label="Slot 2", style=discord.ButtonStyle.secondary, row=1)
    async def slot_2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(GuideSlotModal(self, 2))

    @discord.ui.button(label="Slot 3", style=discord.ButtonStyle.secondary, row=1)
    async def slot_3(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(GuideSlotModal(self, 3))

    @discord.ui.button(label="Preview", emoji="👁️", style=discord.ButtonStyle.secondary, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        preview = TeamGuide(
            guide_id=self.draft.guide_id or 0,
            name=self.draft.name or "Untitled guide",
            aliases=tuple(self.draft.aliases),
            categories=tuple(self.draft.categories or ["Uncategorized"]),
            authors=self.draft.authors or interaction.user.display_name,
            description=self.draft.description or "Description not set.",
            full_guide=self.draft.full_guide,
            viability=self.draft.viability,
            ease=self.draft.ease,
            slots=tuple(self.draft.slots.values()),
            creator_id=interaction.user.id,
            updated_by=interaction.user.id,
            version=1,
            created_at=int(time.time()),
            updated_at=int(time.time()),
        )
        variable_sources = [
            preview.description,
            preview.full_guide,
            *(slot.notes for slot in preview.slots),
        ]
        unresolved = tuple(
            dict.fromkeys(
                variable
                for source in variable_sources
                for variable in unresolved_guide_variables(source)
            )
        )
        notice = None
        if unresolved:
            notice = (
                "⚠️ Unknown emoji variables stay as text until corrected: "
                + ", ".join(f"{{{value}}}" for value in unresolved[:8])
            )
        await interaction.response.send_message(
            content=notice,
            embed=build_guide_embed(self.cog.bot, preview),
            view=PublicGuideView(self.cog, preview),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Publish", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not all((self.draft.name, self.draft.aliases, self.draft.categories, self.draft.description)):
            await interaction.response.send_message("Complete the basics, aliases, category, and description first.", ephemeral=True)
            return
        if len([slot for slot in self.draft.slots.values() if slot.animal]) != 3:
            await interaction.response.send_message("Complete all three composition slots first.", ephemeral=True)
            return
        try:
            guide = await asyncio.to_thread(self.cog.store.save, self.draft, interaction.user.id)
        except GuideAliasConflict as exc:
            await interaction.response.send_message(f"The alias `{exc}` already belongs to another guide.", ephemeral=True)
            return
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"The guide could not be published: {exc}", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="✅ Guide published.",
            embed=build_guide_embed(self.cog.bot, guide),
            view=PublicGuideView(self.cog, guide),
        )
        self.stop()

    @discord.ui.button(label="Emoji variables", emoji="💡", style=discord.ButtonStyle.secondary, row=3)
    async def emoji_variables(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            embed=build_emoji_variable_help_embed(self.cog.bot),
            ephemeral=True,
        )

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Guide editor closed without publishing.", view=self)
        self.stop()


class FullGuideView(discord.ui.View):
    def __init__(
        self,
        pages: tuple[discord.Embed, ...],
        user_id: int,
    ) -> None:
        super().__init__(timeout=10 * 60)
        self.pages = pages
        self.user_id = user_id
        self.index = 0
        self.sync_buttons()

    def sync_buttons(self) -> None:
        self.previous.disabled = self.index <= 0
        self.next.disabled = self.index >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This private full-guide view belongs to another member.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index = max(0, self.index - 1)
        self.sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        self.sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


class PublicGuideView(discord.ui.View):
    def __init__(self, cog: "TeamGuides", guide: TeamGuide) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guide = guide
        if guide.full_guide.strip():
            full_guide = discord.ui.Button(
                label="Full guide",
                emoji="📖",
                style=discord.ButtonStyle.secondary,
            )
            full_guide.callback = self.open_full_guide
            self.add_item(full_guide)

    async def open_full_guide(self, interaction: discord.Interaction) -> None:
        pages = build_full_guide_embeds(self.cog.bot, self.guide)
        if not pages:
            await interaction.response.send_message(
                "This team does not have a full guide yet.",
                ephemeral=True,
            )
            return
        view = FullGuideView(pages, interaction.user.id) if len(pages) > 1 else None
        await interaction.response.send_message(
            embed=pages[0],
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Related teams", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def related(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guides = await asyncio.to_thread(self.cog.store.related, self.guide, 5)
        if not guides:
            await interaction.response.send_message("No other guides share this guide's categories yet.", ephemeral=True)
            return
        lines = [
            f"• **{guide.name}** — `{guide.aliases[0] if guide.aliases else normalize_guide_alias(guide.name)}`"
            for guide in guides
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class TeamGuides(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = TeamGuideStore()
        try:
            self.owner_id = int(os.getenv("BOT_OWNER_ID", "0") or 0)
        except ValueError:
            self.owner_id = 0

    async def cog_load(self) -> None:
        await asyncio.to_thread(self.store.initialize)
        logger.info("Trusted team-guide storage ready at %s", DATABASE_FILE)

    async def is_bot_owner(self, user: discord.abc.User) -> bool:
        if self.owner_id and user.id == self.owner_id:
            return True
        return await self.bot.is_owner(user)

    async def is_expert(self, user: discord.abc.User) -> bool:
        if await self.is_bot_owner(user):
            return True
        return await asyncio.to_thread(self.store.is_expert, user.id)

    async def send_guide(self, destination: discord.abc.Messageable, query: str) -> bool:
        guide = await asyncio.to_thread(self.store.find, query)
        if guide is not None:
            await destination.send(
                embed=build_guide_embed(self.bot, guide),
                view=PublicGuideView(self, guide),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        matches = await asyncio.to_thread(self.store.list_guides, query, 15)
        if not matches:
            await destination.send(
                f"No trusted team guide matched `{query}`. Use `/team-guide` without a search to see recent guides.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        await destination.send(embed=self.list_embed(matches, query=query))
        return True

    def list_embed(self, guides: list[TeamGuide], *, query: str = "") -> discord.Embed:
        if not guides:
            return discord.Embed(
                title="📚 Trusted Team Guides",
                description="No expert guides have been published yet.",
                color=0x5865F2,
            )
        lines = [
            f"**{guide.name}** — `{guide.aliases[0] if guide.aliases else normalize_guide_alias(guide.name)}`\n"
            f"-# by {guide.authors} • {', '.join(guide.categories)} • viability {guide.viability}/5 • v{guide.version}"
            for guide in guides[:15]
        ]
        return discord.Embed(
            title=f"📚 Trusted Team Guides{f' — {query}' if query else ''}"[:256],
            description="\n\n".join(lines),
            color=0x5865F2,
        )

    @app_commands.command(name="guide-expert", description="Owner-only: grant or revoke trusted guide-author access.")
    @app_commands.describe(member="Expert to update", enabled="True grants access; False revokes it")
    async def guide_expert(
        self,
        interaction: discord.Interaction,
        member: discord.User,
        enabled: bool,
    ) -> None:
        if not await self.is_bot_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can manage trusted guide experts.", ephemeral=True)
            return
        await asyncio.to_thread(
            self.store.set_expert,
            member.id,
            member.display_name,
            enabled,
            interaction.user.id,
        )
        state = "granted" if enabled else "revoked"
        await interaction.response.send_message(f"✅ Trusted guide access {state} for {member.mention}.", ephemeral=True)

    @app_commands.command(name="team-guide", description="Search or browse trusted battle-team guides.")
    @app_commands.describe(query="Name, alias, category, or author; leave empty to browse")
    async def team_guide(self, interaction: discord.Interaction, query: str | None = None) -> None:
        if query:
            guide = await asyncio.to_thread(self.store.find, query)
            if guide is not None:
                await interaction.response.send_message(
                    embed=build_guide_embed(self.bot, guide),
                    view=PublicGuideView(self, guide),
                )
                return
            matches = await asyncio.to_thread(self.store.list_guides, query, 15)
            if not matches:
                await interaction.response.send_message(f"No trusted guide matched `{query}`.", ephemeral=True)
                return
            await interaction.response.send_message(embed=self.list_embed(matches, query=query))
            return
        guides = await asyncio.to_thread(self.store.list_guides, "", 15)
        await interaction.response.send_message(embed=self.list_embed(guides))

    @team_guide.autocomplete("query")
    async def team_guide_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        guides = await asyncio.to_thread(self.store.list_guides, current, 25)
        return [
            app_commands.Choice(
                name=f"{guide.name} — {guide.aliases[0] if guide.aliases else 'guide'}"[:100],
                value=(guide.aliases[0] if guide.aliases else str(guide.guide_id))[:100],
            )
            for guide in guides
        ]

    @app_commands.command(name="team-guide-create", description="Trusted experts: create a visual battle-team guide.")
    async def team_guide_create(self, interaction: discord.Interaction) -> None:
        if not await self.is_expert(interaction.user):
            await interaction.response.send_message("Only trusted guide experts can create public guides.", ephemeral=True)
            return
        draft = GuideDraft(editor_id=interaction.user.id, authors=interaction.user.display_name)
        view = GuideEditorView(self, draft)
        await interaction.response.send_message(embed=build_editor_embed(draft), view=view, ephemeral=True)

    @app_commands.command(name="team-guide-edit", description="Trusted experts: revise and version an existing guide.")
    @app_commands.describe(query="Existing guide name or alias")
    async def team_guide_edit(self, interaction: discord.Interaction, query: str) -> None:
        if not await self.is_expert(interaction.user):
            await interaction.response.send_message("Only trusted guide experts can revise guides.", ephemeral=True)
            return
        guide = await asyncio.to_thread(self.store.find, query)
        if guide is None:
            await interaction.response.send_message(f"No trusted guide matched `{query}`.", ephemeral=True)
            return
        draft = draft_from_guide(guide, interaction.user.id)
        view = GuideEditorView(self, draft)
        await interaction.response.send_message(embed=build_editor_embed(draft), view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        helper_prefix = await get_guild_helper_prefix(message.guild.id)
        argument = parse_helper_command_argument(
            message.content or "",
            helper_prefix,
            {"h guide", "hguide"},
        )
        if argument is None:
            return
        lowered = argument.casefold().strip()
        if lowered in {"create", "new", "edit", "update"}:
            command = "/team-guide-create" if lowered in {"create", "new"} else "/team-guide-edit"
            await message.reply(
                f"Use `{command}` to open the private visual guide editor.",
                mention_author=False,
            )
            return
        if not argument:
            guides = await asyncio.to_thread(self.store.list_guides, "", 15)
            await message.channel.send(embed=self.list_embed(guides))
            return
        await self.send_guide(message.channel, argument)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeamGuides(bot))
