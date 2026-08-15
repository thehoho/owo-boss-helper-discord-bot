"""Neon weapon-page scanner and OwO weapon dex helper.

The scanner watches public NeonUtil weapon inventory pages, records weapons where
Neon still shows max-quality as an estimate, and helps the owning member dex those
weapons through OwO's `ww <weapon_id>` command.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from .helper_prefix import (
    HELPER_PREFIX_DEFAULT,
    canonicalize_helper_command,
    get_guild_helper_prefix,
    helper_alias,
    helper_command,
)
from .message_utils import safe_reply
from .owo_prefix import get_guild_owo_prefix
from .ui_emojis import ui_emoji_text

logger = logging.getLogger(__name__)

NEON_BOT_ID = 851436490415931422
OWO_BOT_ID = 408785106942164992
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "neon_weapons.db"
SCAN_REACTION = "🧾"
PENDING_WW_SECONDS = 90
DEX_STEP_SECONDS = 2
DEX_DEFAULT_PAGE_SIZE = 20
DEX_MAX_SESSION_SIZE = 1000
DEX_SESSION_MAX_SECONDS = 6 * 60 * 60
DEX_COMMAND_PREFIXES = ("ww", "wuse")
WEAPON_ID_RE = re.compile(r"\b([A-Z0-9]{6})\b", re.IGNORECASE)
CUSTOM_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):(\d{15,22})>")
NEON_HEADER_RE = re.compile(
    r"(?:^|\n)\s*#+\s*⚔️\s*<@!?(\d{15,22})>'s\s+([\d,]+)\s+(saved\s+)?weapons\b",
    re.IGNORECASE,
)
FILTER_LINE_RE = re.compile(r"^\s*\*\*Filters:\*\*\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
WEAPON_ROW_RE = re.compile(r"^\s*`([A-Z0-9]{6})`\s+(.+)$", re.IGNORECASE | re.MULTILINE)
QUALITY_RE = re.compile(r"\*\*\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*\*\*")
BACKTICK_NUMBER_RE = re.compile(r"`\s*([0-9]+(?:\.[0-9]+)?)\s*%?\s*`")
BLUEPRINT_RE = re.compile(r"`([^`]*[A-Za-z][^`]*)`")
MAX_QUALITY_HEADER_RE = re.compile(r"Max\s+qualities\s+for\s+\d+", re.IGNORECASE)

STATUS_EMOJIS = {
    "max_possible": "1064755333484007535",
    "exact": "1064746427147890758",
    "saved": "1064746427147890758",
}

# Neon encodes weapon/passive context in emoji names such as ``raedge``,
# ``cswarm``, ``mresonance``, and ``eawand``. The leading character is often a
# rarity/wear/frame marker, not part of the canonical weapon/passive alias.
NEON_CONTEXT_PREFIXES = (
    "common",
    "uncommon",
    "rare",
    "epic",
    "mythic",
    "mythical",
    "legendary",
    "fabled",
    "hidden",
    "distorted",
    "patreon",
    "gem",
    "bot",
    "pristine",
    "fine",
    "decent",
    "worn",
    "unknown",
    "empowered",
    "pr",
    "c",
    "u",
    "r",
    "e",
    "m",
    "l",
    "f",
    "d",
    "p",
    "g",
    "b",
    "h",
)

WEAPON_ALIASES: dict[str, str] = {
    "sword": "sword",
    "gsword": "sword",
    "greatsword": "sword",
    "great_sword": "sword",
    "ugreatsword": "sword",
    "rgreatsword": "sword",
    "egreatsword": "sword",
    "mgreatsword": "sword",
    "great": "sword",
    "hstaff": "hstaff",
    "healstaff": "hstaff",
    "healingstaff": "hstaff",
    "bow": "bow",
    "rune": "rune",
    "shield": "shield",
    "aegis": "shield",
    "orb": "orb",
    "vstaff": "vstaff",
    "vampstaff": "vstaff",
    "dagger": "dagger",
    "pdagger": "dagger",
    "pdag": "dagger",
    "pd": "dagger",
    "wand": "wand",
    "awand": "wand",
    "fstaff": "fstaff",
    "estaff": "estaff",
    "sstaff": "sstaff",
    "ascept": "ascept",
    "arcane": "ascept",
    "scepter": "ascept",
    "rstaff": "rstaff",
    "axe": "axe",
    "gaxe": "axe",
    "vban": "vban",
    "banner": "vban",
    "sythe": "sythe",
    "csyth": "sythe",
    "csy": "sythe",
    "scythe": "sythe",
    "crune": "crune",
    "cel": "crune",
    "pstaff": "pstaff",
    "lsyth": "lsyth",
    "lsy": "lsyth",
    "lscythe": "lsyth",
    "ffish": "ffish",
    "lrune": "lrune",
    "cstaff": "cstaff",
    "stithe": "stithe",
    "soul": "stithe",
    "tithe": "stithe",
    "bhstaff": "bhstaff",
    "aedge": "aedge",
    "edge": "aedge",
    "woundb": "woundb",
    "crossbow": "woundb",
    "wbow": "woundb",
    "wcbow": "woundb",
    "xbow": "woundb",
    "bgaz": "bgaz",
    "gaze": "bgaz",
    "bgaze": "bgaz",
    "cclaw": "cclaw",
    "claw": "cclaw",
}

PASSIVE_ALIASES: dict[str, str] = {
    "att": "str",
    "str": "str",
    "strength": "str",
    "mag": "mag",
    "magic": "mag",
    "hp": "hp",
    "wp": "wp",
    "pr": "pr",
    "mr": "mr",
    "lifesteal": "ls",
    "ls": "ls",
    "thorns": "th",
    "th": "th",
    "mtap": "mtap",
    "manatap": "mtap",
    "mana_tap": "mtap",
    "absolve": "absv",
    "absv": "absv",
    "safeguard": "sg",
    "sg": "sg",
    "critical": "crit",
    "crit": "crit",
    "discharge": "dc",
    "dc": "dc",
    "kkaze": "kk",
    "kamikaze": "kk",
    "kk": "kk",
    "hgen": "hgen",
    "regen": "hgen",
    "regeneration": "hgen",
    "wgen": "wgen",
    "energ": "wgen",
    "energize": "wgen",
    "sprout": "sprout",
    "sprt": "sprout",
    "enrage": "enrage",
    "enra": "enrage",
    "sac": "sac",
    "sacrifice": "sac",
    "snail": "snail",
    "knowledge": "kno",
    "kno": "kno",
    "n": "kno",
    "gslay": "gslay",
    "slay": "gslay",
    "slayer": "gslay",
    "adapt": "adapt",
    "adaptation": "adapt",
    "resonance": "res",
    "res": "res",
    "reso": "res",
    "swarm": "swarm",
    "hive": "swarm",
    "lhive": "swarm",
    "lwolf": "lwolf",
    "wolf": "lwolf",
    "dstrike": "ds",
    "strike": "ds",
    "ds": "ds",
    "frarm": "fr",
    "armor": "fr",
    "fr": "fr",
}

WEAPON_LABELS = {
    "sword": "Great Sword",
    "hstaff": "Healing Staff",
    "bow": "Bow",
    "rune": "Rune",
    "shield": "Defender's Aegis",
    "orb": "Orb",
    "vstaff": "Vampiric Staff",
    "dagger": "Poison Dagger",
    "wand": "Wand",
    "fstaff": "Flame Staff",
    "estaff": "Energy Staff",
    "sstaff": "Spirit Staff",
    "ascept": "Arcane Scepter",
    "rstaff": "Resurrection Staff",
    "axe": "Glacial Axe",
    "vban": "Vanguard's Banner",
    "sythe": "Culling Scythe",
    "crune": "Rune of Celebration",
    "pstaff": "Staff of Purity",
    "lsyth": "Leeching Scythe",
    "ffish": "Foul Fish",
    "lrune": "Rune of Luck",
    "cstaff": "Staff of Corruption",
    "stithe": "Soul Tithe",
    "bhstaff": "Briar-Heart Staff",
    "aedge": "Arbiter's Edge",
    "woundb": "Wounding Crossbow",
    "bgaz": "Bleeding Gaze",
    "cclaw": "Conduit Claw",
}

PASSIVE_LABELS = {
    "str": "Strength",
    "mag": "Magic",
    "hp": "Health Point",
    "wp": "Weapon Point",
    "pr": "Physical Resistance",
    "mr": "Magic Resistance",
    "ls": "Lifesteal",
    "th": "Thorns",
    "mtap": "Mana Tap",
    "absv": "Absolve",
    "sg": "Safeguard",
    "crit": "Critical",
    "dc": "Discharge",
    "kk": "Kamikaze",
    "hgen": "Regeneration",
    "wgen": "Energize",
    "sprout": "Sprout",
    "enrage": "Enrage",
    "sac": "Sacrifice",
    "snail": "Snail",
    "kno": "Knowledge",
    "gslay": "Giant Slayer",
    "adapt": "Adaptation",
    "res": "Resonance",
    "swarm": "Living Hive",
    "lwolf": "Lone Wolf",
    "ds": "Double Strike",
    "fr": "Frost Armor",
}


@dataclass(frozen=True)
class NeonWeaponEntry:
    owner_user_id: int
    weapon_id: str
    current_quality: float | None
    max_quality: float | None
    needs_dex: bool
    saved: bool
    exact: bool
    weapon_type: str
    passive_types: tuple[str, ...]
    blueprint: str
    last_seen_at: int
    dexed_at: int


@dataclass(frozen=True)
class ParsedWeaponRow:
    weapon_id: str
    raw_line: str
    current_quality: float | None
    max_quality: float | None
    max_possible: bool
    exact: bool
    saved: bool
    weapon_type: str
    passive_types: tuple[str, ...]
    emoji_ids: tuple[str, ...]
    empowered_unresolved: bool


@dataclass(frozen=True)
class ParsedNeonPage:
    owner_user_id: int
    total_weapons: int
    saved_inventory: bool
    weapon_type: str
    passive_types: tuple[str, ...]
    rows: tuple[ParsedWeaponRow, ...]


@dataclass(frozen=True)
class ParsedMaxQualityRow:
    weapon_id: str
    raw_line: str
    max_quality: float | None
    exact: bool
    max_possible: bool
    weapon_type: str
    passive_types: tuple[str, ...]

    @property
    def needs_dex(self) -> bool:
        return not self.exact


@dataclass(frozen=True)
class ParsedMaxQualityReport:
    rows: tuple[ParsedMaxQualityRow, ...]


@dataclass
class PendingWeaponCommand:
    user_id: int
    channel_id: int
    weapon_id: str
    created_at: float


class ClosingConnection(sqlite3.Connection):
    def __enter__(self) -> "ClosingConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def normalize_weapon(value: str) -> str:
    return WEAPON_ALIASES.get(normalize_alias(value), "")


def normalize_passive(value: str) -> str:
    return PASSIVE_ALIASES.get(normalize_alias(value), "")


def alias_candidates_from_emoji_name(name: str) -> tuple[str, ...]:
    """Return safe canonicalization candidates for a Neon emoji name.

    Examples:
    - ``raedge`` -> ``raedge``, ``aedge``
    - ``mresonance`` -> ``mresonance``, ``resonance``
    - ``prlsyth`` -> suffix match ``lsyth``
    """
    normalized = normalize_alias(name)
    if not normalized:
        return ()
    candidates: list[str] = [normalized]
    for prefix in NEON_CONTEXT_PREFIXES:
        if normalized.startswith(prefix) and len(normalized) > len(prefix) + 1:
            stripped = normalized[len(prefix) :]
            if stripped and stripped not in candidates:
                candidates.append(stripped)

    suffix_aliases = sorted(
        {
            alias
            for alias in (*WEAPON_ALIASES.keys(), *PASSIVE_ALIASES.keys())
            if len(alias) >= 3
        },
        key=len,
        reverse=True,
    )
    for alias in suffix_aliases:
        if normalized.endswith(alias) and alias not in candidates:
            candidates.append(alias)
    return tuple(candidates)


def classify_neon_emoji_names(names: tuple[str, ...] | list[str]) -> tuple[str, tuple[str, ...]]:
    """Infer weapon/passive context from Neon/OwO emoji names.

    The first recognized weapon emoji becomes the weapon type. Any recognized
    passive emojis are kept in order without duplicates. Unknown or pure rarity
    emojis are ignored.
    """
    weapon_type = ""
    passive_types: list[str] = []
    for name in names:
        candidates = alias_candidates_from_emoji_name(name)
        if not weapon_type:
            for candidate in candidates:
                weapon = normalize_weapon(candidate)
                if weapon:
                    weapon_type = weapon
                    break
        for candidate in candidates:
            passive = normalize_passive(candidate)
            if passive and passive not in passive_types:
                passive_types.append(passive)
                break
    return weapon_type, tuple(passive_types)


def merge_passive_context(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        for passive in group:
            if passive and passive not in merged:
                merged.append(passive)
    return tuple(merged)


def format_float(value: float | None) -> str:
    if value is None:
        return "?"
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text}%"


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
    _walk_text(getattr(message, "components", []), chunks, set())
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
        logger.warning("Could not fetch Neon weapon message %s: %s", message_id, exc)
        return None


def is_neon_author(user: Any) -> bool:
    if int(getattr(user, "id", 0) or 0) == NEON_BOT_ID:
        return True
    names = " ".join(
        str(getattr(user, attr, "") or "")
        for attr in ("name", "display_name", "global_name")
    ).casefold()
    return "neonutil" in names or "neon util" in names or "[neon]" in names


def parse_filters(text: str) -> tuple[str, tuple[str, ...]]:
    match = FILTER_LINE_RE.search(text or "")
    if match is None:
        return "", ()
    filter_text = match.group(1)
    weapon_type = ""
    passives: list[str] = []
    for name, _emoji_id in CUSTOM_EMOJI_RE.findall(filter_text):
        weapon = normalize_weapon(name)
        passive = normalize_passive(name)
        if weapon and not weapon_type:
            weapon_type = weapon
        elif passive and passive not in passives:
            passives.append(passive)
    return weapon_type, tuple(passives)




def emoji_names_before_quality(body: str) -> tuple[str, ...]:
    quality_match = QUALITY_RE.search(body or "")
    prefix = body[: quality_match.start()] if quality_match else body
    return tuple(name for name, _emoji_id in CUSTOM_EMOJI_RE.findall(prefix))


def emoji_ids_before_quality(body: str) -> tuple[str, ...]:
    """Return custom emoji IDs that appear before the visible quality percent.

    Neon's unfiltered empowered rows do not use the M/max_possible marker, but
    they do show the weapon plus multiple passive emojis before the quality. A
    non-saved empowered row is therefore still a dex candidate.
    """
    quality_match = QUALITY_RE.search(body or "")
    prefix = body[: quality_match.start()] if quality_match else body
    return tuple(emoji_id for _name, emoji_id in CUSTOM_EMOJI_RE.findall(prefix))


def is_empowered_unresolved_row(body: str, *, saved: bool, exact: bool, max_possible: bool) -> bool:
    """Detect unsaved empowered weapon rows from unfiltered Neon pages.

    Standard max-quality rows are handled by the M/max_possible marker. Empowered
    weapon-crate and boss-weapon rows may omit that marker entirely; if they are
    not saved/exact and they show a weapon plus two or more passives before the
    quality, they still need a `ww <weapon_id>` dex command.
    """
    if saved or exact or max_possible:
        return False
    # Rows with a backtick max-quality value are standard estimate rows, not the
    # empowered no-marker shape we are targeting here.
    if BACKTICK_NUMBER_RE.search(body or ""):
        return False
    # Optional leading source markers such as the blue diamond, distorted, or
    # fabled icon can appear before the real weapon/passive icons. Counting at
    # least three custom emojis before quality safely covers weapon+2 passives;
    # orb-style empowered rows can contain even more.
    return len(emoji_ids_before_quality(body)) >= 3


def has_exact_or_saved_marker(text: str) -> bool:
    lowered = (text or "").casefold()
    return (
        "<:saved:" in lowered
        or "<:exact:" in lowered
        or ":saved:" in lowered
        or ":exact:" in lowered
        or "✅" in text
        or "✔" in text
        or "✓" in text
    )


def has_max_possible_marker(text: str) -> bool:
    lowered = (text or "").casefold()
    return (
        "<:max_possible:" in lowered
        or ":max_possible:" in lowered
        or STATUS_EMOJIS["max_possible"] in text
    )

def parse_weapon_row(line: str, *, saved_inventory: bool) -> ParsedWeaponRow | None:
    match = WEAPON_ROW_RE.match(line)
    if match is None:
        return None
    weapon_id = match.group(1).upper()
    body = match.group(2)
    quality_match = QUALITY_RE.search(body)
    current_quality = float(quality_match.group(1)) if quality_match else None
    max_numbers = BACKTICK_NUMBER_RE.findall(body)
    max_quality = float(max_numbers[-1]) if max_numbers else None
    emoji_names = emoji_names_before_quality(body)
    row_weapon_type, row_passive_types = classify_neon_emoji_names(emoji_names)
    emojis = tuple(emoji_id for _name, emoji_id in CUSTOM_EMOJI_RE.findall(body))
    max_possible = has_max_possible_marker(body)
    saved = saved_inventory or has_exact_or_saved_marker(body)
    exact = has_exact_or_saved_marker(body)
    empowered_unresolved = is_empowered_unresolved_row(
        body,
        saved=saved,
        exact=exact,
        max_possible=max_possible,
    )
    return ParsedWeaponRow(
        weapon_id=weapon_id,
        raw_line=line.strip(),
        current_quality=current_quality,
        max_quality=max_quality,
        max_possible=max_possible,
        exact=exact,
        saved=saved,
        weapon_type=row_weapon_type,
        passive_types=row_passive_types,
        emoji_ids=emojis,
        empowered_unresolved=empowered_unresolved,
    )


def parse_neon_weapon_page(text: str) -> ParsedNeonPage | None:
    header = NEON_HEADER_RE.search(text or "")
    if header is None:
        return None
    owner_user_id = int(header.group(1))
    total_weapons = int(header.group(2).replace(",", ""))
    saved_inventory = bool(header.group(3))
    weapon_type, passive_types = parse_filters(text)
    rows: list[ParsedWeaponRow] = []
    for line in (text or "").splitlines():
        row = parse_weapon_row(line, saved_inventory=saved_inventory)
        if row is not None:
            rows.append(row)
    if not rows:
        return None
    return ParsedNeonPage(
        owner_user_id=owner_user_id,
        total_weapons=total_weapons,
        saved_inventory=saved_inventory,
        weapon_type=weapon_type,
        passive_types=passive_types,
        rows=tuple(rows),
    )


def parse_neon_max_quality_report(text: str) -> ParsedMaxQualityReport | None:
    source = text or ""
    if not MAX_QUALITY_HEADER_RE.search(source) and "Saved inventory weapons" not in source:
        return None
    rows: list[ParsedMaxQualityRow] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line or "%" not in line:
            continue
        id_match = WEAPON_ID_RE.search(line)
        if id_match is None:
            continue
        weapon_id = id_match.group(1).upper()
        emoji_names = tuple(
            name for name, _emoji_id in CUSTOM_EMOJI_RE.findall(line[: id_match.start()])
        )
        row_weapon_type, row_passive_types = classify_neon_emoji_names(emoji_names)
        quality_match = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", line)
        max_quality = float(quality_match[-1]) if quality_match else None
        exact = has_exact_or_saved_marker(line)
        max_possible = has_max_possible_marker(line)
        rows.append(
            ParsedMaxQualityRow(
                weapon_id,
                line,
                max_quality,
                exact,
                max_possible,
                row_weapon_type,
                row_passive_types,
            )
        )
    if not rows:
        return None
    return ParsedMaxQualityReport(rows=tuple(rows))


def parse_ww_weapon_command(content: str) -> str | None:
    first_line = next(
        (line.strip() for line in (content or "").splitlines() if line.strip()),
        "",
    )
    match = re.fullmatch(
        r"(?:owo\s+)?(?:ww|wuse|w\s+use)\s+([A-Z0-9]{6})",
        first_line,
        re.IGNORECASE,
    )
    return match.group(1).upper() if match else None


def parse_blueprint(text: str) -> str | None:
    candidates = [candidate.strip() for candidate in BLUEPRINT_RE.findall(text or "")]
    for candidate in reversed(candidates):
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_'-]*", candidate)
        if not tokens:
            continue
        if any(normalize_weapon(token) for token in tokens) and (
            any(normalize_passive(token) for token in tokens) or len(tokens) >= 1
        ):
            return candidate
    return None


def classify_blueprint(blueprint: str) -> tuple[str, tuple[str, ...]]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_'-]*", blueprint or "")
    weapon_type = ""
    passive_types: list[str] = []
    for token in tokens:
        if not weapon_type:
            weapon = normalize_weapon(token)
            if weapon:
                weapon_type = weapon
                continue
        passive = normalize_passive(token)
        if passive and passive not in passive_types:
            passive_types.append(passive)
    return weapon_type, tuple(passive_types)


def parse_neon_command(
    content: str,
    helper_prefix: str = HELPER_PREFIX_DEFAULT,
) -> tuple[str, str] | None:
    content = canonicalize_helper_command(content, helper_prefix)
    first_line = next(
        (line.strip() for line in (content or "").splitlines() if line.strip()),
        "",
    )
    lowered = first_line.casefold()
    compact = re.sub(r"\s+", " ", lowered).strip()
    compact_no_space = re.sub(r"\s+", "", lowered)

    if compact in {"h weapons", "h weapon", "hw"} or compact_no_space in {"hweapons", "hweapon"}:
        return "guide", ""

    stop_commands = {
        "h stop",
        "hstop",
        "hs",
        "h dex stop",
        "hwd stop",
        "h weapon stop",
        "h weapon dex stop",
    }
    if compact in stop_commands or compact_no_space in {"hstop", "hs"}:
        return "stop", ""

    skip_commands = {
        "h dex skip",
        "hwd skip",
        "h weapon skip",
        "h weapon dex skip",
        "hweapondex skip",
    }
    if compact in skip_commands or compact_no_space in {"hwdskip", "hdexskip", "hweapondexskip"}:
        return "skip", ""

    dex_prefixes = (
        "h weapon dex",
        "h weapondex",
        "h dex",
        "hw dex",
        "hwd",
        "hwdex",
    )
    for prefix in dex_prefixes:
        if compact == prefix:
            return "dex", ""
        if compact.startswith(prefix + " "):
            return "dex", first_line[len(prefix):].strip()

    if compact_no_space == "hweapondex":
        return "dex", ""
    if compact_no_space.startswith("hweapondex") and len(first_line) > len("hweapondex"):
        return "dex", first_line[len("hweapondex"):].strip()

    for prefix in ("h weapon stats", "h dex stats", "hw stats", "hwd stats"):
        if compact == prefix:
            return "stats", ""
    for prefix in ("h weapon clear", "h dex clear", "hw clear", "hwd clear"):
        if compact == prefix:
            return "clear", ""
    return None


def parse_filter_terms(query: str) -> tuple[tuple[str, ...], tuple[str, ...], list[str]]:
    weapon_types: list[str] = []
    passives: list[str] = []
    unknown: list[str] = []
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_'-]*", query or "")
    for token in tokens:
        weapon = normalize_weapon(token)
        passive = normalize_passive(token)
        if weapon and weapon not in weapon_types:
            weapon_types.append(weapon)
        elif passive and passive not in passives:
            passives.append(passive)
        elif not weapon and not passive:
            unknown.append(token)
    return tuple(weapon_types), tuple(passives), unknown


def parse_dex_query_options(query: str) -> tuple[str, int, bool]:
    """Extract optional session length from a dex query.

    Examples:
    - ``mtap 100`` -> ``mtap``, 100
    - ``all`` -> empty filter, max cap
    - ``dagger mtap all`` -> ``dagger mtap``, max cap
    """
    tokens = re.findall(r"<@!?\d{15,22}>|\d{1,5}|[A-Za-z][A-Za-z0-9_'-]*", query or "")
    limit = DEX_DEFAULT_PAGE_SIZE
    requested_all = False
    remaining: list[str] = []
    for token in tokens:
        lowered = token.casefold()
        if lowered in {"all", "max", "long", "full"}:
            limit = DEX_MAX_SESSION_SIZE
            requested_all = True
            continue
        if token.isdigit():
            limit = max(1, min(int(token), DEX_MAX_SESSION_SIZE))
            continue
        remaining.append(token)
    return " ".join(remaining).strip(), limit, requested_all


class NeonWeaponStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()

    def _connect(self) -> ClosingConnection:
        connection = sqlite3.connect(
            self.path,
            timeout=10,
            factory=ClosingConnection,
        )
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
                CREATE TABLE IF NOT EXISTS neon_weapon_entries (
                    owner_user_id INTEGER NOT NULL,
                    weapon_id TEXT NOT NULL,
                    guild_id INTEGER NOT NULL DEFAULT 0,
                    channel_id INTEGER NOT NULL DEFAULT 0,
                    source_message_id INTEGER NOT NULL DEFAULT 0,
                    raw_line TEXT NOT NULL DEFAULT '',
                    current_quality REAL,
                    max_quality REAL,
                    needs_dex INTEGER NOT NULL DEFAULT 0,
                    saved INTEGER NOT NULL DEFAULT 0,
                    exact INTEGER NOT NULL DEFAULT 0,
                    weapon_type TEXT NOT NULL DEFAULT '',
                    passive_types_json TEXT NOT NULL DEFAULT '[]',
                    blueprint TEXT NOT NULL DEFAULT '',
                    emoji_ids_json TEXT NOT NULL DEFAULT '[]',
                    first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    dexed_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (owner_user_id, weapon_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_neon_weapon_owner_need "
                "ON neon_weapon_entries(owner_user_id, needs_dex, weapon_type)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS neon_weapon_scans (
                    owner_user_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL DEFAULT 0,
                    channel_id INTEGER NOT NULL DEFAULT 0,
                    parsed_count INTEGER NOT NULL DEFAULT 0,
                    needs_dex_count INTEGER NOT NULL DEFAULT 0,
                    weapon_type TEXT NOT NULL DEFAULT '',
                    passive_types_json TEXT NOT NULL DEFAULT '[]',
                    scanned_at INTEGER NOT NULL,
                    PRIMARY KEY (owner_user_id, message_id)
                )
                """
            )

    async def upsert_page(
        self,
        page: ParsedNeonPage,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
    ) -> tuple[int, int]:
        async with self.lock:
            return await asyncio.to_thread(
                self._upsert_page_sync,
                page,
                guild_id,
                channel_id,
                message_id,
            )

    def _upsert_page_sync(
        self,
        page: ParsedNeonPage,
        guild_id: int,
        channel_id: int,
        message_id: int,
    ) -> tuple[int, int]:
        now = int(time.time())
        needs_count = 0
        with self._connect() as connection:
            for row in page.rows:
                needs_dex = bool(not page.saved_inventory and not row.exact and not row.saved)
                if needs_dex:
                    needs_count += 1
                row_weapon_type = row.weapon_type or page.weapon_type
                row_passive_types = merge_passive_context(page.passive_types, row.passive_types)
                passive_types_json = json.dumps(list(row_passive_types))
                emoji_ids_json = json.dumps(list(row.emoji_ids))
                connection.execute(
                    """
                    INSERT INTO neon_weapon_entries (
                        owner_user_id, weapon_id, guild_id, channel_id,
                        source_message_id, raw_line, current_quality, max_quality,
                        needs_dex, saved, exact, weapon_type, passive_types_json,
                        blueprint, emoji_ids_json, first_seen_at, last_seen_at, dexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, 0)
                    ON CONFLICT(owner_user_id, weapon_id) DO UPDATE SET
                        guild_id = excluded.guild_id,
                        channel_id = excluded.channel_id,
                        source_message_id = excluded.source_message_id,
                        raw_line = excluded.raw_line,
                        current_quality = COALESCE(excluded.current_quality, neon_weapon_entries.current_quality),
                        max_quality = COALESCE(excluded.max_quality, neon_weapon_entries.max_quality),
                        needs_dex = CASE
                            WHEN excluded.saved = 1 OR excluded.exact = 1 THEN 0
                            WHEN excluded.needs_dex = 1 THEN 1
                            ELSE neon_weapon_entries.needs_dex
                        END,
                        saved = MAX(neon_weapon_entries.saved, excluded.saved),
                        exact = MAX(neon_weapon_entries.exact, excluded.exact),
                        weapon_type = CASE
                            WHEN excluded.weapon_type <> '' THEN excluded.weapon_type
                            ELSE neon_weapon_entries.weapon_type
                        END,
                        passive_types_json = CASE
                            WHEN excluded.passive_types_json <> '[]' THEN excluded.passive_types_json
                            ELSE neon_weapon_entries.passive_types_json
                        END,
                        emoji_ids_json = excluded.emoji_ids_json,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        page.owner_user_id,
                        row.weapon_id,
                        guild_id,
                        channel_id,
                        message_id,
                        row.raw_line,
                        row.current_quality,
                        row.max_quality,
                        int(needs_dex),
                        int(row.saved),
                        int(row.exact),
                        row_weapon_type,
                        passive_types_json,
                        emoji_ids_json,
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO neon_weapon_scans (
                    owner_user_id, message_id, guild_id, channel_id, parsed_count,
                    needs_dex_count, weapon_type, passive_types_json, scanned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, message_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    channel_id = excluded.channel_id,
                    parsed_count = excluded.parsed_count,
                    needs_dex_count = excluded.needs_dex_count,
                    weapon_type = excluded.weapon_type,
                    passive_types_json = excluded.passive_types_json,
                    scanned_at = excluded.scanned_at
                """,
                (
                    page.owner_user_id,
                    message_id,
                    guild_id,
                    channel_id,
                    len(page.rows),
                    needs_count,
                    page.weapon_type,
                    json.dumps(list(page.passive_types)),
                    now,
                ),
            )
        return len(page.rows), needs_count

    async def mark_dexed(
        self,
        owner_user_id: int,
        weapon_id: str,
        blueprint: str,
        weapon_type: str,
        passive_types: tuple[str, ...],
    ) -> bool:
        async with self.lock:
            return await asyncio.to_thread(
                self._mark_dexed_sync,
                owner_user_id,
                weapon_id,
                blueprint,
                weapon_type,
                passive_types,
            )

    def _mark_dexed_sync(
        self,
        owner_user_id: int,
        weapon_id: str,
        blueprint: str,
        weapon_type: str,
        passive_types: tuple[str, ...],
    ) -> bool:
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE neon_weapon_entries
                SET needs_dex = 0,
                    saved = 1,
                    exact = 1,
                    blueprint = ?,
                    weapon_type = CASE WHEN ? <> '' THEN ? ELSE weapon_type END,
                    passive_types_json = CASE WHEN ? <> '[]' THEN ? ELSE passive_types_json END,
                    dexed_at = ?,
                    last_seen_at = ?
                WHERE owner_user_id = ? AND weapon_id = ?
                """,
                (
                    blueprint[:300],
                    weapon_type,
                    weapon_type,
                    json.dumps(list(passive_types)),
                    json.dumps(list(passive_types)),
                    now,
                    now,
                    owner_user_id,
                    weapon_id.upper(),
                ),
            )
        return cursor.rowcount > 0

    async def mark_dexed_weapon_any_owner(
        self,
        weapon_id: str,
        blueprint: str,
        weapon_type: str,
        passive_types: tuple[str, ...],
    ) -> int:
        async with self.lock:
            return await asyncio.to_thread(
                self._mark_dexed_weapon_any_owner_sync,
                weapon_id,
                blueprint,
                weapon_type,
                passive_types,
            )

    def _mark_dexed_weapon_any_owner_sync(
        self,
        weapon_id: str,
        blueprint: str,
        weapon_type: str,
        passive_types: tuple[str, ...],
    ) -> int:
        now = int(time.time())
        passive_json = json.dumps(list(passive_types))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE neon_weapon_entries
                SET needs_dex = 0,
                    saved = 1,
                    exact = 1,
                    blueprint = ?,
                    weapon_type = CASE WHEN ? <> '' THEN ? ELSE weapon_type END,
                    passive_types_json = CASE WHEN ? <> '[]' THEN ? ELSE passive_types_json END,
                    dexed_at = ?,
                    last_seen_at = ?
                WHERE weapon_id = ?
                """,
                (
                    blueprint[:300],
                    weapon_type,
                    weapon_type,
                    passive_json,
                    passive_json,
                    now,
                    now,
                    weapon_id.upper(),
                ),
            )
        return cursor.rowcount

    async def apply_max_quality_report(self, report: ParsedMaxQualityReport) -> tuple[int, int, int]:
        async with self.lock:
            return await asyncio.to_thread(self._apply_max_quality_report_sync, report)

    def _apply_max_quality_report_sync(self, report: ParsedMaxQualityReport) -> tuple[int, int, int]:
        now = int(time.time())
        exact_updates = 0
        need_updates = 0
        with self._connect() as connection:
            for row in report.rows:
                passive_json = json.dumps(list(row.passive_types))
                if row.exact:
                    cursor = connection.execute(
                        """
                        UPDATE neon_weapon_entries
                        SET needs_dex = 0,
                            saved = 1,
                            exact = 1,
                            max_quality = COALESCE(?, max_quality),
                            weapon_type = CASE WHEN ? <> '' THEN ? ELSE weapon_type END,
                            passive_types_json = CASE WHEN ? <> '[]' THEN ? ELSE passive_types_json END,
                            dexed_at = CASE WHEN dexed_at > 0 THEN dexed_at ELSE ? END,
                            last_seen_at = ?
                        WHERE weapon_id = ?
                        """,
                        (
                            row.max_quality,
                            row.weapon_type,
                            row.weapon_type,
                            passive_json,
                            passive_json,
                            now,
                            now,
                            row.weapon_id.upper(),
                        ),
                    )
                    exact_updates += cursor.rowcount
                else:
                    cursor = connection.execute(
                        """
                        UPDATE neon_weapon_entries
                        SET needs_dex = 1,
                            saved = 0,
                            exact = 0,
                            max_quality = COALESCE(?, max_quality),
                            weapon_type = CASE WHEN ? <> '' THEN ? ELSE weapon_type END,
                            passive_types_json = CASE WHEN ? <> '[]' THEN ? ELSE passive_types_json END,
                            last_seen_at = ?
                        WHERE weapon_id = ? AND saved = 0 AND exact = 0
                        """,
                        (
                            row.max_quality,
                            row.weapon_type,
                            row.weapon_type,
                            passive_json,
                            passive_json,
                            now,
                            row.weapon_id.upper(),
                        ),
                    )
                    need_updates += cursor.rowcount
        return len(report.rows), exact_updates, need_updates

    async def list_dex_queue(
        self,
        owner_user_id: int,
        *,
        weapon_types: tuple[str, ...] = (),
        passive_types: tuple[str, ...] = (),
        limit: int = 25,
    ) -> list[NeonWeaponEntry]:
        async with self.lock:
            return await asyncio.to_thread(
                self._list_dex_queue_sync,
                owner_user_id,
                weapon_types,
                passive_types,
                limit,
            )

    def _list_dex_queue_sync(
        self,
        owner_user_id: int,
        weapon_types: tuple[str, ...],
        passive_types: tuple[str, ...],
        limit: int,
    ) -> list[NeonWeaponEntry]:
        clauses = ["owner_user_id = ?", "needs_dex = 1"]
        params: list[Any] = [owner_user_id]
        if weapon_types:
            placeholders = ",".join("?" for _ in weapon_types)
            clauses.append(f"weapon_type IN ({placeholders})")
            params.extend(weapon_types)
        query = (
            "SELECT * FROM neon_weapon_entries WHERE "
            + " AND ".join(clauses)
            + " ORDER BY COALESCE(max_quality, current_quality, 0) DESC, weapon_id ASC LIMIT ?"
        )
        fetch_limit = max(1, limit)
        if passive_types:
            # Passive filtering happens after JSON decoding, so overfetch enough rows
            # that filters like `HWD mtap` or `HWD dagger shield mtap` do not
            # miss matching queued weapons that fall outside the first quality-sorted page.
            fetch_limit = max(fetch_limit, 1000)
        params.append(fetch_limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        entries = [self._entry_from_row(row) for row in rows]
        if passive_types:
            wanted = set(passive_types)
            entries = [entry for entry in entries if wanted.issubset(set(entry.passive_types))]
        return entries[: max(1, limit)]

    async def stats(self, owner_user_id: int) -> dict[str, int]:
        async with self.lock:
            return await asyncio.to_thread(self._stats_sync, owner_user_id)

    def _stats_sync(self, owner_user_id: int) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN needs_dex = 1 THEN 1 ELSE 0 END) AS need,
                    SUM(CASE WHEN saved = 1 OR exact = 1 OR dexed_at > 0 THEN 1 ELSE 0 END) AS saved,
                    MAX(last_seen_at) AS last_seen
                FROM neon_weapon_entries
                WHERE owner_user_id = ?
                """,
                (owner_user_id,),
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "need": int(row["need"] or 0),
            "saved": int(row["saved"] or 0),
            "last_seen": int(row["last_seen"] or 0),
        }

    async def clear_user(self, owner_user_id: int) -> int:
        async with self.lock:
            return await asyncio.to_thread(self._clear_user_sync, owner_user_id)

    def _clear_user_sync(self, owner_user_id: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM neon_weapon_entries WHERE owner_user_id = ?",
                (owner_user_id,),
            )
            connection.execute(
                "DELETE FROM neon_weapon_scans WHERE owner_user_id = ?",
                (owner_user_id,),
            )
        return cursor.rowcount

    async def mark_done(self, owner_user_id: int, weapon_id: str) -> bool:
        async with self.lock:
            return await asyncio.to_thread(self._mark_done_sync, owner_user_id, weapon_id)

    def _mark_done_sync(self, owner_user_id: int, weapon_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE neon_weapon_entries
                SET needs_dex = 0, saved = 1, dexed_at = ?, last_seen_at = ?
                WHERE owner_user_id = ? AND weapon_id = ?
                """,
                (int(time.time()), int(time.time()), owner_user_id, weapon_id.upper()),
            )
        return cursor.rowcount > 0

    async def delete_weapon(self, owner_user_id: int, weapon_id: str) -> bool:
        async with self.lock:
            return await asyncio.to_thread(self._delete_weapon_sync, owner_user_id, weapon_id)

    def _delete_weapon_sync(self, owner_user_id: int, weapon_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM neon_weapon_entries WHERE owner_user_id = ? AND weapon_id = ?",
                (owner_user_id, weapon_id.upper()),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> NeonWeaponEntry:
        try:
            passives = tuple(str(item) for item in json.loads(row["passive_types_json"] or "[]"))
        except (TypeError, json.JSONDecodeError):
            passives = ()
        return NeonWeaponEntry(
            owner_user_id=int(row["owner_user_id"]),
            weapon_id=str(row["weapon_id"]),
            current_quality=float(row["current_quality"]) if row["current_quality"] is not None else None,
            max_quality=float(row["max_quality"]) if row["max_quality"] is not None else None,
            needs_dex=bool(int(row["needs_dex"])),
            saved=bool(int(row["saved"])),
            exact=bool(int(row["exact"])),
            weapon_type=str(row["weapon_type"] or ""),
            passive_types=passives,
            blueprint=str(row["blueprint"] or ""),
            last_seen_at=int(row["last_seen_at"] or 0),
            dexed_at=int(row["dexed_at"] or 0),
        )


@dataclass
class ActiveDexSession:
    runner_user_id: int
    owner_user_id: int
    channel_id: int
    entries: list[NeonWeaponEntry]
    owner_display_name: str
    runner_display_name: str
    helper_prefix: str = HELPER_PREFIX_DEFAULT
    index: int = 0
    guide_message_id: int = 0
    started_at: float = field(default_factory=time.monotonic)
    advancing: bool = False


class WeaponDexView(discord.ui.View):
    def __init__(
        self,
        cog: "NeonWeapons",
        runner_user_id: int,
        owner_user_id: int,
        owner_display_name: str,
        runner_display_name: str,
        entries: list[NeonWeaponEntry],
        helper_prefix: str,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.cog = cog
        self.runner_user_id = runner_user_id
        self.owner_user_id = owner_user_id
        self.owner_display_name = owner_display_name
        self.runner_display_name = runner_display_name
        self.entries = entries
        self.helper_prefix = helper_prefix

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.runner_user_id:
            await interaction.response.send_message(
                "This dex-session button belongs to another helper.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Start dexing session", style=discord.ButtonStyle.success)
    async def start_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message(
                "I cannot start a dexing session in this channel.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.cog.start_dex_session(
            user=interaction.user,
            channel=channel,
            owner_user_id=self.owner_user_id,
            owner_display_name=self.owner_display_name,
            runner_display_name=self.runner_display_name,
            entries=self.entries,
            helper_prefix=self.helper_prefix,
        )
        stop_command = helper_command(self.helper_prefix, "stop")
        stop_alias = helper_alias(self.helper_prefix, "hs")
        await interaction.followup.send(
            f"Started a dexing session for **{self.owner_display_name}**. I will post one alternating `ww` / `wuse` command at a time. "
            "Copy the command shown in the channel and send it there, then I will wait for Neon to confirm it before moving to the next one. "
            f"Use `{stop_command}` or `{stop_alias}` to pause and continue later.",
            ephemeral=True,
        )


class ActiveDexStepView(discord.ui.View):
    def __init__(self, cog: "NeonWeapons", runner_user_id: int) -> None:
        super().__init__(timeout=DEX_SESSION_MAX_SECONDS)
        self.cog = cog
        self.runner_user_id = runner_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.runner_user_id:
            await interaction.response.send_message(
                "This dex-session prompt belongs to another helper.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Skip weapon", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        skipped = await self.cog.skip_dex_session_interaction(interaction)
        if skipped:
            await interaction.followup.send("Skipped this weapon and moved on.", ephemeral=True)
        else:
            await interaction.followup.send("No active dex weapon was skipped.", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        stopped = await self.cog.stop_dex_session_interaction(interaction)
        if stopped:
            await interaction.followup.send("Stopped your weapon dex session.", ephemeral=True)
        else:
            await interaction.followup.send("You do not have an active dex session here.", ephemeral=True)


class NeonWeapons(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = NeonWeaponStore(DATABASE_FILE)
        self.pending_commands: dict[tuple[int, int], PendingWeaponCommand] = {}
        self.pending_command_messages: dict[int, PendingWeaponCommand] = {}
        self.pending_owo_replies: dict[int, PendingWeaponCommand] = {}
        self.active_dex_sessions: dict[tuple[int, int], ActiveDexSession] = {}
        self._ready = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._ready:
            return
        self._ready = True
        await self.store.initialize()
        logger.info("Neon weapon scanner ready")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if message.author.bot:
            if is_neon_author(message.author):
                await self.handle_neon_message(message)
            elif message.author.id == OWO_BOT_ID:
                await self.handle_owo_message(message)
            return
        helper_prefix = await get_guild_helper_prefix(message.guild.id)
        parsed_command = parse_neon_command(message.content or "", helper_prefix)
        if parsed_command is not None:
            action, argument = parsed_command
            if action == "guide":
                await self.send_guide(message)
            elif action == "dex":
                await self.send_dex(message, argument)
            elif action == "stats":
                await self.send_stats(message)
            elif action == "clear":
                await self.clear_queue(message)
            elif action == "stop":
                await self.stop_dex_session(message)
            elif action == "skip":
                await self.skip_dex_session(message)
            return
        weapon_id = parse_ww_weapon_command(message.content or "")
        if weapon_id:
            pending = PendingWeaponCommand(
                user_id=message.author.id,
                channel_id=message.channel.id,
                weapon_id=weapon_id,
                created_at=time.monotonic(),
            )
            self.pending_commands[(message.channel.id, message.author.id)] = pending
            self.pending_command_messages[message.id] = pending

    async def handle_owo_message(self, message: discord.Message) -> None:
        reference = getattr(message, "reference", None)
        command_message_id = int(getattr(reference, "message_id", 0) or 0)
        if not command_message_id:
            return
        self.cleanup_pending_weapon_commands()
        pending = self.pending_command_messages.get(command_message_id)
        if pending is None:
            return
        self.pending_owo_replies[message.id] = pending
        logger.debug(
            "Linked OwO weapon reply %s to pending weapon %s from runner %s",
            message.id,
            pending.weapon_id,
            pending.user_id,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or not is_neon_author(after.author):
            return
        await self.handle_neon_message(after)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.guild_id is None:
            return
        data = dict(payload.data or {})
        raw_text = extract_raw_text(data)
        author = data.get("author") or {}
        try:
            author_id = int(author.get("id") or 0)
        except (TypeError, ValueError):
            author_id = 0
        try:
            application_id = int(data.get("application_id") or 0)
        except (TypeError, ValueError):
            application_id = 0

        author_name = " ".join(
            str(author.get(key) or "")
            for key in ("username", "global_name", "name")
        ).casefold()
        looks_like_neon = (
            author_id == NEON_BOT_ID
            or application_id == NEON_BOT_ID
            or "neonutil" in author_name
            or parse_neon_weapon_page(raw_text) is not None
            or parse_neon_max_quality_report(raw_text) is not None
            or parse_blueprint(raw_text) is not None
        )
        if not looks_like_neon:
            return
        await self.handle_neon_raw(
            payload.guild_id,
            payload.channel_id,
            payload.message_id,
            data,
        )

    async def handle_neon_message(self, message: discord.Message) -> None:
        await self.store.initialize()
        text = extract_message_text(message)
        page = parse_neon_weapon_page(text)
        report = parse_neon_max_quality_report(text) if page is None else None
        blueprint = parse_blueprint(text) if page is None and report is None else None

        if page is None and report is None and blueprint is None:
            raw = await fetch_raw_message(self.bot, message.channel.id, message.id)
            if raw:
                return await self.handle_neon_raw(
                    message.guild.id if message.guild else 0,
                    message.channel.id,
                    message.id,
                    raw,
                    reaction_message=message,
                )

        reference = getattr(message, "reference", None)
        reply_reference_id = int(getattr(reference, "message_id", 0) or 0)
        await self.process_neon_text(
            text,
            guild_id=message.guild.id if message.guild else 0,
            channel_id=message.channel.id,
            message_id=message.id,
            reaction_message=message,
            reply_reference_id=reply_reference_id,
        )

    async def handle_neon_raw(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        data: dict[str, Any],
        *,
        reaction_message: discord.Message | None = None,
    ) -> None:
        await self.store.initialize()
        text = extract_raw_text(data)
        if reaction_message is None:
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                fetch_message = getattr(channel, "fetch_message", None)
                if callable(fetch_message):
                    reaction_message = await fetch_message(message_id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound, AttributeError):
                reaction_message = None
        message_reference = data.get("message_reference") or {}
        try:
            reply_reference_id = int(message_reference.get("message_id") or 0)
        except (TypeError, ValueError):
            reply_reference_id = 0
        await self.process_neon_text(
            text,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            reaction_message=reaction_message,
            reply_reference_id=reply_reference_id,
        )

    async def process_neon_text(
        self,
        text: str,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        reaction_message: discord.Message | None = None,
        reply_reference_id: int = 0,
    ) -> None:
        page = parse_neon_weapon_page(text)
        if page is not None:
            parsed, needs = await self.store.upsert_page(
                page,
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
            )
            if reaction_message is not None:
                try:
                    await reaction_message.add_reaction(SCAN_REACTION)
                except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                    pass
            logger.info(
                "Scanned Neon weapons page %s for owner %s: %s rows, %s need dex",
                message_id,
                page.owner_user_id,
                parsed,
                needs,
            )
            return

        report = parse_neon_max_quality_report(text)
        if report is not None:
            parsed, exact_updates, need_updates = await self.store.apply_max_quality_report(report)
            if reaction_message is not None:
                try:
                    await reaction_message.add_reaction(SCAN_REACTION)
                except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                    pass
            pending = self.find_pending_for_neon_reply(channel_id, reply_reference_id)
            confirmed_ids = {row.weapon_id for row in report.rows if row.exact}
            if pending is not None and pending.weapon_id in confirmed_ids:
                await self.advance_dex_session_after_confirmation(channel_id, pending.user_id, pending.weapon_id)
            logger.info(
                "Scanned Neon max-quality report %s: %s rows, %s exact updates, %s needs-dex updates",
                message_id,
                parsed,
                exact_updates,
                need_updates,
            )
            return

        blueprint = parse_blueprint(text)
        if blueprint is None:
            return
        pending = self.find_pending_for_neon_reply(channel_id, reply_reference_id)
        if pending is None:
            return
        weapon_type, passive_types = classify_blueprint(blueprint)
        updated_count = await self.store.mark_dexed_weapon_any_owner(
            pending.weapon_id,
            blueprint,
            weapon_type,
            passive_types,
        )
        if updated_count:
            await self.advance_dex_session_after_confirmation(channel_id, pending.user_id, pending.weapon_id)
            logger.info(
                "Marked weapon %s dexed in %s owner queue(s) from Neon blueprint %s; runner user %s",
                pending.weapon_id,
                updated_count,
                blueprint,
                pending.user_id,
            )

    def cleanup_pending_weapon_commands(self) -> None:
        now = time.monotonic()
        stale_command_keys = [
            key
            for key, pending in self.pending_commands.items()
            if now - pending.created_at > PENDING_WW_SECONDS
        ]
        for key in stale_command_keys:
            self.pending_commands.pop(key, None)

        stale_message_ids = [
            message_id
            for message_id, pending in self.pending_command_messages.items()
            if now - pending.created_at > PENDING_WW_SECONDS
        ]
        for message_id in stale_message_ids:
            self.pending_command_messages.pop(message_id, None)

        stale_reply_ids = [
            message_id
            for message_id, pending in self.pending_owo_replies.items()
            if now - pending.created_at > PENDING_WW_SECONDS
        ]
        for message_id in stale_reply_ids:
            self.pending_owo_replies.pop(message_id, None)

    def find_pending_for_neon_reply(
        self, channel_id: int, reply_reference_id: int = 0
    ) -> PendingWeaponCommand | None:
        self.cleanup_pending_weapon_commands()
        if reply_reference_id:
            pending = self.pending_owo_replies.get(reply_reference_id)
            if pending is not None:
                return pending
            pending = self.pending_command_messages.get(reply_reference_id)
            if pending is not None:
                return pending
        candidates = [
            pending
            for (pending_channel_id, _user_id), pending in self.pending_commands.items()
            if pending_channel_id == channel_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.created_at)

    async def send_guide(self, message: discord.Message) -> None:
        helper_prefix = await get_guild_helper_prefix(
            message.guild.id if message.guild else None
        )
        owo_prefix = await get_guild_owo_prefix(
            message.guild.id if message.guild else None
        )
        dex_short = helper_alias(helper_prefix, "hwd")
        calculate_emoji = ui_emoji_text(self.bot, "neon_calculate", "🧮")
        embed = discord.Embed(
            title="🧾 Neon weapon dex helper",
            description=(
                "To upload and clean up your weapon data for Neon:\n\n"
                "**1.** Run `nw inv public`.\n"
                "**2.** Run `ww`.\n"
                f"**3.** Click Neon's reaction {calculate_emoji} on the `ww` message.\n"
                "**4.** Click the right arrow ➡️ through every weapon page.\n"
                "-# The helper can only scan the pages that Neon actually displays.\n"
                f"**5.** If your weapons are already uploaded to Neon, run `{owo_prefix}inv`, "
                "then use Neon's `/weapon inv check` in the same channel. It compares "
                "your current OwO inventory with Neon's saved inventory and shows any "
                "weapons that still need to be uploaded.\n\n"
                "OwO Boss Helper scans Neon weapon pages from NeonUtil and saves weapons where Neon shows **M / max possible** or any scanned row without a green saved tick. "
                f"Then run `{dex_short}`, `{helper_command(helper_prefix, 'dex')}`, "
                f"or `{helper_command(helper_prefix, 'weapon dex')}` to get guided "
                "alternating `ww <weapon_id>` / `wuse <weapon_id>` commands.\n\n"
                "Battle helpers can scan another member's Neon filtered pages; the queue "
                "is saved under the member shown in Neon's title. Helpers can run "
                f"`{dex_short} @member` to dex that member's queue, combine weapon filters "
                f"such as `{dex_short} dagger shield 100`, and any confirmed Neon blueprint "
                "removes the weapon from every matching owner's queue across all servers "
                "the helper can see."
            ),
            color=0xFEE75C,
        )
        embed.set_footer(text="Guide based on Pencilvester's Neon weapon setup notes.")
        await safe_reply(message, embed=embed, mention_author=False)

    @staticmethod
    def user_display_name(user: discord.abc.User) -> str:
        return discord.utils.escape_markdown(
            str(getattr(user, "display_name", None) or getattr(user, "name", None) or user.id)
        )

    def resolve_dex_target(self, message: discord.Message, query: str) -> tuple[int, str, str]:
        clean_query = query or ""
        for mentioned in message.mentions:
            mention_tokens = (f"<@{mentioned.id}>", f"<@!{mentioned.id}>")
            if any(token in clean_query for token in mention_tokens):
                for token in mention_tokens:
                    clean_query = clean_query.replace(token, " ")
                return mentioned.id, self.user_display_name(mentioned), re.sub(r"\s+", " ", clean_query).strip()

        id_match = re.search(r"\b(\d{15,22})\b", clean_query)
        if id_match:
            target_id = int(id_match.group(1))
            clean_query = (clean_query[: id_match.start()] + " " + clean_query[id_match.end() :]).strip()
            return target_id, f"user {target_id}", re.sub(r"\s+", " ", clean_query).strip()

        return message.author.id, self.user_display_name(message.author), clean_query.strip()

    def describe_entry(self, entry: NeonWeaponEntry) -> str:
        weapon = WEAPON_LABELS.get(entry.weapon_type, entry.weapon_type or "unknown weapon")
        passives = ", ".join(
            PASSIVE_LABELS.get(passive, passive) for passive in entry.passive_types
        ) or "unknown passive"
        return (
            f"**Type:** {weapon}\n"
            f"**Passive:** {passives}\n"
            f"**Quality:** {format_float(entry.current_quality)} → max {format_float(entry.max_quality)}"
        )

    async def send_dex(self, message: discord.Message, query: str) -> None:
        helper_prefix = await get_guild_helper_prefix(
            message.guild.id if message.guild else None
        )
        weapon_short = helper_alias(helper_prefix, "hw")
        dex_short = helper_alias(helper_prefix, "hwd")
        stop_command = helper_command(helper_prefix, "stop")
        target_user_id, target_display_name, clean_query = self.resolve_dex_target(message, query)
        runner_display_name = self.user_display_name(message.author)
        filter_query, session_limit, requested_all = parse_dex_query_options(clean_query)
        weapon_types, passive_types, unknown = parse_filter_terms(filter_query)
        if unknown:
            await safe_reply(
                message,
                "I did not recognize this dex filter: "
                + ", ".join(f"`{item}`" for item in unknown)
                + f". Try `{weapon_short}` or `{helper_command(helper_prefix, 'weapons')}` "
                f"for supported aliases, or `{stop_command}` to pause a dexing session.",
                mention_author=False,
            )
            return
        entries = await self.store.list_dex_queue(
            target_user_id,
            weapon_types=weapon_types,
            passive_types=passive_types,
            limit=session_limit,
        )
        if not entries:
            suffix = f" for `{filter_query}`" if filter_query else ""
            await safe_reply(
                message,
                f"I do not have any queued Neon weapon dex commands for **{target_display_name}**{suffix}. "
                f"Scan Neon weapon pages first with `ww` / `nw`, then run `{dex_short}` "
                f"or `{helper_command(helper_prefix, 'dex')}`.",
                mention_author=False,
            )
            return
        lines: list[str] = []
        for index, entry in enumerate(entries[:10], start=1):
            tags = []
            if entry.weapon_type:
                tags.append(entry.weapon_type)
            tags.extend(entry.passive_types)
            tag_text = f" — `{', '.join(tags)}`" if tags else ""
            lines.append(
                f"**{index}.** `ww {entry.weapon_id}`{tag_text} · "
                f"{format_float(entry.current_quality)} → {format_float(entry.max_quality)}"
            )
        embed = discord.Embed(
            title=f"🧾 Weapons needing Neon dex for {target_display_name}",
            description=(
                "These are the next queued weapons.\n\n"
                "• Select **Start dexing session** to begin.\n"
                "• Copy each command from the session message and send it in this channel.\n"
                "• The helper advances only "
                "after OwO and Neon confirm that weapon.\n"
                f"• Sessions alternate `ww` and `wuse`, support `{dex_short} 100` / "
                f"`{dex_short} all`, and allow combined filters such as "
                f"`{dex_short} dagger shield 100`.\n\n"
                + "\n".join(lines)
            ),
            color=0xFEE75C,
        )
        if filter_query:
            embed.add_field(name="Filter", value=f"`{filter_query}`", inline=False)
        embed.add_field(
            name="Session length",
            value=(
                f"`{len(entries)}` command{'s' if len(entries) != 1 else ''}"
                + (" requested by `all`" if requested_all else "")
                + f" · cap `{DEX_MAX_SESSION_SIZE}`"
            ),
            inline=False,
        )
        if len(entries) > 10:
            embed.set_footer(text=f"Showing first 10 of {len(entries)} queued weapons for this session.")
        await safe_reply(
            message,
            embed=embed,
            view=WeaponDexView(
                self,
                runner_user_id=message.author.id,
                owner_user_id=target_user_id,
                owner_display_name=target_display_name,
                runner_display_name=runner_display_name,
                entries=entries,
                helper_prefix=helper_prefix,
            ),
            mention_author=False,
        )


    def dex_session_key(self, channel_id: int, user_id: int) -> tuple[int, int]:
        return (channel_id, user_id)

    def dex_command_for_session(self, session: ActiveDexSession) -> str | None:
        if session.index >= len(session.entries):
            return None
        entry = session.entries[session.index]
        prefix = DEX_COMMAND_PREFIXES[session.index % len(DEX_COMMAND_PREFIXES)]
        return f"{prefix} {entry.weapon_id}"

    def build_session_message(self, session: ActiveDexSession) -> str:
        command = self.dex_command_for_session(session)
        if command is None:
            return (
                f"✅ **{session.owner_display_name}**'s weapon dex session is complete. "
                f"Run `{helper_alias(session.helper_prefix, 'hwd')}` anytime to continue "
                "with any remaining queue."
            )
        runner_note = ""
        if session.runner_user_id != session.owner_user_id:
            runner_note = f" — **{session.runner_display_name}** running"
        return (
            f"**{session.owner_display_name}**{runner_note} — "
            f"{session.index + 1}/{len(session.entries)}\n"
            "-# Copy this command and send it in this channel:\n"
            f"# `{command}`"
        )

    async def delete_session_prompt(self, channel: discord.abc.Messageable, session: ActiveDexSession) -> None:
        if not session.guide_message_id:
            return
        fetch_message = getattr(channel, "fetch_message", None)
        if not callable(fetch_message):
            return
        try:
            old_message = await fetch_message(session.guide_message_id)
            await old_message.delete()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, AttributeError):
            pass
        finally:
            session.guide_message_id = 0

    async def send_session_prompt(self, channel: discord.abc.Messageable, session: ActiveDexSession) -> None:
        await self.delete_session_prompt(channel, session)
        sent = await channel.send(
            self.build_session_message(session),
            view=ActiveDexStepView(self, session.runner_user_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        session.guide_message_id = sent.id

    async def start_dex_session(
        self,
        *,
        user: discord.abc.User,
        channel: discord.abc.Messageable,
        owner_user_id: int,
        owner_display_name: str,
        runner_display_name: str,
        entries: list[NeonWeaponEntry],
        helper_prefix: str,
    ) -> None:
        if not entries:
            return
        key = self.dex_session_key(getattr(channel, "id", 0), user.id)
        old_session = self.active_dex_sessions.pop(key, None)
        if old_session is not None:
            await self.delete_session_prompt(channel, old_session)
        session = ActiveDexSession(
            runner_user_id=user.id,
            owner_user_id=owner_user_id,
            channel_id=getattr(channel, "id", 0),
            entries=list(entries),
            owner_display_name=owner_display_name,
            runner_display_name=runner_display_name,
            helper_prefix=helper_prefix,
        )
        self.active_dex_sessions[key] = session
        await self.send_session_prompt(channel, session)

    async def advance_dex_session_after_confirmation(
        self,
        channel_id: int,
        runner_user_id: int,
        weapon_id: str,
    ) -> None:
        key = self.dex_session_key(channel_id, runner_user_id)
        session = self.active_dex_sessions.get(key)
        if session is None:
            return
        if time.monotonic() - session.started_at > DEX_SESSION_MAX_SECONDS:
            self.active_dex_sessions.pop(key, None)
            channel = self.bot.get_channel(channel_id)
            if channel is not None:
                await self.delete_session_prompt(channel, session)
                await channel.send(
                    f"Your weapon dex session expired. Run "
                    f"`{helper_alias(session.helper_prefix, 'hwd')} 100` or "
                    f"`{helper_alias(session.helper_prefix, 'hwd')} all` to start a fresh long session.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return
        if session.advancing or session.index >= len(session.entries):
            return
        current = session.entries[session.index]
        if current.weapon_id.upper() != weapon_id.upper():
            return
        session.advancing = True
        try:
            await asyncio.sleep(DEX_STEP_SECONDS)
            session.index += 1
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                    channel = None
            if channel is None:
                return
            if session.index >= len(session.entries):
                await self.delete_session_prompt(channel, session)
                self.active_dex_sessions.pop(key, None)
                await channel.send(
                    f"✅ Weapon dex session complete. Run "
                    f"`{helper_alias(session.helper_prefix, 'hwd')}` again if more weapons remain queued.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            await self.send_session_prompt(channel, session)
        finally:
            session.advancing = False

    async def stop_dex_session(self, message: discord.Message) -> None:
        key = self.dex_session_key(message.channel.id, message.author.id)
        session = self.active_dex_sessions.pop(key, None)
        if session is None:
            await safe_reply(
                message,
                "You do not have an active weapon dex session in this channel.",
                mention_author=False,
            )
            return
        await self.delete_session_prompt(message.channel, session)
        helper_prefix = await get_guild_helper_prefix(
            message.guild.id if message.guild else None
        )
        await safe_reply(
            message,
            f"Stopped your weapon dex session. Run "
            f"`{helper_alias(helper_prefix, 'hwd')}` to continue later from the remaining queue.",
            mention_author=False,
        )

    async def skip_active_session(
        self, channel: discord.abc.Messageable, runner_user_id: int
    ) -> tuple[bool, str]:
        key = self.dex_session_key(getattr(channel, "id", 0), runner_user_id)
        session = self.active_dex_sessions.get(key)
        if session is None or session.index >= len(session.entries):
            return False, ""
        entry = session.entries.pop(session.index)
        await self.store.delete_weapon(session.owner_user_id, entry.weapon_id)
        if session.index >= len(session.entries):
            await self.delete_session_prompt(channel, session)
            self.active_dex_sessions.pop(key, None)
            await channel.send(
                f"Skipped `{entry.weapon_id}`. ✅ Weapon dex session complete.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True, entry.weapon_id
        await self.send_session_prompt(channel, session)
        return True, entry.weapon_id

    async def skip_dex_session(self, message: discord.Message) -> None:
        skipped, weapon_id = await self.skip_active_session(message.channel, message.author.id)
        if not skipped:
            await safe_reply(
                message,
                "You do not have an active weapon dex prompt to skip in this channel.",
                mention_author=False,
            )
            return
        await safe_reply(
            message,
            f"Skipped and removed `{weapon_id}` from this dex queue. It can be re-added if Neon scans it again later.",
            mention_author=False,
        )

    async def skip_dex_session_interaction(self, interaction: discord.Interaction) -> bool:
        channel = interaction.channel
        if channel is None:
            return False
        skipped, _weapon_id = await self.skip_active_session(channel, interaction.user.id)
        return skipped

    async def stop_dex_session_interaction(self, interaction: discord.Interaction) -> bool:
        channel = interaction.channel
        if channel is None:
            return False
        key = self.dex_session_key(getattr(channel, "id", 0), interaction.user.id)
        session = self.active_dex_sessions.pop(key, None)
        if session is None:
            return False
        await self.delete_session_prompt(channel, session)
        return True

    async def send_stats(self, message: discord.Message) -> None:
        stats = await self.store.stats(message.author.id)
        last_seen = stats["last_seen"]
        other = max(0, stats['total'] - stats['need'] - stats['saved'])
        description = (
            f"**Scanned weapons:** {stats['total']:,}\n"
            f"**Need dex:** {stats['need']:,}\n"
            f"**Already saved/exact/dexed:** {stats['saved']:,}\n"
            f"**Scanned / no action needed:** {other:,}"
        )
        if last_seen:
            description += f"\n**Last scan:** <t:{last_seen}:R>"
        await safe_reply(
            message,
            embed=discord.Embed(
                title="🧾 Neon weapon stats",
                description=description,
                color=0xFEE75C,
            ),
            mention_author=False,
        )

    async def clear_queue(self, message: discord.Message) -> None:
        removed = await self.store.clear_user(message.author.id)
        await safe_reply(
            message,
            f"Cleared **{removed:,}** scanned Neon weapon entr{'y' if removed == 1 else 'ies'} for you.",
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NeonWeapons(bot))
