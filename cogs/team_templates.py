"""OwO team-template storage, shortcuts, and guided team restoration.

Templates are stored per Discord user in SQLite with stable slots 1-100.
Animal identity comes from the OwO emoji alias rather than the renameable pet label.
Guided mode alternates animal/weapon commands, waits for OwO confirmations,
supports skip controls, safely handles concurrent users, and equips saved
weapons to the animal identity instead of a fragile team position.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import discord
from discord.ext import commands

from .helper_prefix import (
    HELPER_PREFIX_DEFAULT,
    get_guild_helper_prefix,
    helper_alias,
    helper_aliases,
    helper_command,
)

logger = logging.getLogger(__name__)

OWO_BOT_ID = 408785106942164992
TEAM_SAVE_EMOJI = "💾"
MAX_TEMPLATES_PER_USER = 100
TEMPLATE_PAGE_SIZE = 25
MAX_TEMPLATE_NAME_LENGTH = 40
DELETE_CONFIRMED_USER_COMMANDS = True
GUIDED_SESSION_TIMEOUT_SECONDS = 15 * 60
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "team_templates.db"
OWO_PREFIX_DEFAULT = "w"
MAX_OWO_PREFIX_LENGTH = 5

TEAM_COMMAND_PREFIXES = ("h teams", "h team", "hteam", "htm", "ht")

# Standard OwO animal names that may appear in emoji aliases with a one-letter
# rarity prefix, such as :gfish:, :llion:, :deagle:, or :hlizard:.
# Custom pets are intentionally not guessed: unknown emoji aliases are preserved
# exactly so users can reference those pets by their real OwO identifier.
STANDARD_ANIMAL_NAMES = frozenset(
    {
        "bee",
        "bug",
        "snail",
        "beetle",
        "butterfly",
        "chick",
        "mouse",
        "chicken",
        "rabbit",
        "chipmunk",
        "sheep",
        "pig",
        "cow",
        "dog",
        "cat",
        "crocodile",
        "tiger",
        "penguin",
        "elephant",
        "whale",
        "dragon",
        "unicorn",
        "snowman",
        "ghost",
        "dove",
        "pbird",
        "pdolphin",
        "pogre",
        "pscorpion",
        "ptiger",
        "camel",
        "fish",
        "panda",
        "shrimp",
        "spider",
        "deer",
        "fox",
        "lion",
        "owl",
        "squid",
        "boar",
        "eagle",
        "frog",
        "gorilla",
        "wolf",
        "dinobot",
        "giraffbot",
        "slothbot",
        "hedgebot",
        "lobbot",
        "koala",
        "lizard",
        "monkey",
        "snake",
        "octopus",
        "glitchparrot",
        "glitchotter",
        "glitchraccoon",
        "glitchflamingo",
        "glitchzebra",
    }
)

# OwO's emoji aliases commonly prefix standard animals with their tier/rank.
# Both short and readable forms are accepted because aliases have changed over
# time and community payloads are not always formatted identically.
ANIMAL_RANK_EMOJI_PREFIXES = frozenset(
    {
        "c",
        "u",
        "r",
        "e",
        "m",
        "p",
        "g",
        "l",
        "d",
        "f",
        "b",
        "h",
        "common",
        "uncommon",
        "rare",
        "epic",
        "mythic",
        "mythical",
        "patreon",
        "gem",
        "legendary",
        "fabled",
        "bot",
        "hidden",
        "distorted",
    }
)


def parse_team_helper_command(
    content: str,
    helper_prefix: str = HELPER_PREFIX_DEFAULT,
) -> tuple[str, str | None] | None:
    """Parse long and compact team-helper commands.

    Supported examples:
    - H team / HT / HTM
    - H team create name / HT C name / HTC name / HTM C name
    - HT3 / HTM3 / H team 3
    - HT ddc tank / H team ddc tank
    - H team delete 3 / HT D 3 / HTD team-name
    - H team update 3 / HT U 3 / HTU team-name
    - H team edit 3 / HTE 3 / HT edit team-name
    - H team rename 3 new-name / HT rename old-name to new-name
    - HT move 8 1 / HT swap boss farm / HT order
    - HT prefix o / H team prefix w
    - H team help / HT help
    - HS / H skip / H escape / HT skip
    - HT cancel
    """
    first_line = next(
        (line.strip() for line in (content or "").splitlines() if line.strip()),
        "",
    )
    if re.match(r"^https?://", first_line, re.IGNORECASE):
        return None

    text = re.sub(r"\s+", " ", first_line).strip()
    lowered = text.lower()
    compact = re.sub(r"\s+", "", lowered)

    # Fast guided-step shortcuts are intentionally separate from HTS because
    # HTS already means "save" in the compact team command family.
    fast_skip_aliases = {
        re.sub(r"\s+", "", alias)
        for alias in helper_aliases(helper_prefix, {"hs", "hskip", "hescape"})
    }
    if compact in fast_skip_aliases:
        return "skip", None

    command_prefixes = helper_aliases(helper_prefix, TEAM_COMMAND_PREFIXES)
    prefix = next(
        (item for item in command_prefixes if lowered.startswith(item)),
        None,
    )
    if prefix is None:
        return None

    rest = text[len(prefix):].strip()
    if not rest:
        return "list", None
    if rest.isdigit():
        return "open", rest

    action_match = re.match(
        r"^(create|save|c|s|update|u|edit|e|rename|move|mv|swap|order|o|prefix|owoprefix|owo-prefix|p|delete|remove|d|r|help|h|skip|escape|cancel|stop|x)(?:\s+(.*))?$",
        rest,
        re.IGNORECASE,
    )
    if not action_match:
        return "open_query", rest

    action = action_match.group(1).lower()
    argument = (action_match.group(2) or "").strip() or None
    if action in {"create", "save", "c", "s"}:
        return "create", argument
    if action in {"update", "u"}:
        return "update", argument

    if action in {"edit", "e"}:
        return "edit", argument
    if action in {"rename"}:
        return "rename", argument
    if action in {"move", "mv"}:
        return "move", argument
    if action in {"swap"}:
        return "swap", argument
    if action in {"order", "o"}:
        return "order", argument
    if action in {"prefix", "owoprefix", "owo-prefix", "p"}:
        return "prefix", argument
    if action in {"delete", "remove", "d", "r"}:
        return "delete", argument
    if action in {"help", "h"}:
        return "help", None
    if action in {"skip", "escape"}:
        return "skip", None
    return "cancel", None

TEAM_MARKERS = (
    "owo team add",
    "owo team remove",
    "owo setteam",
    "current streak:",
)


@dataclass(frozen=True)
class TeamMember:
    position: int
    animal: str
    weapon_id: str


@dataclass(frozen=True)
class ParsedTeamMessage:
    source_title: str
    members: tuple[TeamMember, ...]
    missing_positions: tuple[int, ...]
    missing_weapon_positions: tuple[int, ...]


@dataclass(frozen=True)
class TeamTemplate:
    template_id: int
    user_id: int
    slot: int
    name: str
    source_title: str
    members: tuple[TeamMember, ...]
    created_at: int
    updated_at: int


@dataclass
class GuidedTeamSession:
    user_id: int
    guild_id: int
    channel_id: int
    template_id: int
    template_slot: int
    template_name: str
    identity_tokens: tuple[str, ...]
    mode: str
    commands: tuple[str, ...]
    owo_prefix: str = OWO_PREFIX_DEFAULT
    helper_prefix: str = HELPER_PREFIX_DEFAULT
    next_index: int = 0
    ready_for_user: bool = False
    waiting_for_owo: bool = False
    command_message_id: int | None = None
    prompt_message_id: int | None = None
    command_sent_at: float = 0.0
    last_activity: float = 0.0

    @property
    def expected_command(self) -> str | None:
        if 0 <= self.next_index < len(self.commands):
            return self.commands[self.next_index]
        return None


def _walk_text(value: Any, chunks: list[str], seen: set[int]) -> None:
    """Collect visible strings from raw Discord JSON or discord.py components."""
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
        for key, child in value.items():
            if key in {"content", "title", "description", "label", "value", "name"}:
                if isinstance(child, str) and child.strip():
                    chunks.append(child)
            elif isinstance(child, (dict, list, tuple)):
                _walk_text(child, chunks, seen)
        return

    if isinstance(value, (list, tuple, set)):
        for child in value:
            _walk_text(child, chunks, seen)
        return

    # discord.py Components V2 objects expose a mixture of these attributes.
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


def extract_all_text(value: Any) -> str:
    chunks: list[str] = []
    _walk_text(value, chunks, set())
    # Preserve component boundaries because team parsing is line-oriented.
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())


def extract_message_text(message: discord.Message) -> str:
    chunks: list[str] = []
    if message.content:
        chunks.append(message.content)
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


def _clean_display_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Keep the emoji alias because it is the authoritative animal identifier.
    # Discord may provide either <:gfish:123456> or the pasted :gfish: form.
    text = re.sub(
        r"<a?:([A-Za-z0-9_]+):\d+>",
        lambda match: f":{match.group(1)}:",
        text,
    )
    text = text.replace("`", "")
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_first_emoji_alias(text: str) -> str | None:
    """Return the first Discord custom-emoji alias from a team member line."""
    match = re.search(r":([A-Za-z0-9_]+):", text or "")
    return match.group(1) if match else None


def normalize_animal_emoji_alias(alias: str) -> str:
    """Turn ranked standard aliases into normal names and preserve custom pets.

    Examples:
    - gspider -> spider
    - gfish -> fish
    - hlizard -> lizard
    - deagle -> eagle
    - custompet231 -> custompet231
    """
    original = (alias or "").strip()
    lowered = original.lower()
    if not lowered:
        return original

    # Some animals already include a leading tier-like letter in their official
    # name (for example pbird), so exact matches must win before prefix stripping.
    if lowered in STANDARD_ANIMAL_NAMES:
        return lowered

    for animal in sorted(STANDARD_ANIMAL_NAMES, key=len, reverse=True):
        if not lowered.endswith(animal):
            continue
        prefix = lowered[: -len(animal)]
        if prefix in ANIMAL_RANK_EMOJI_PREFIXES:
            return animal

    # Unknown aliases are custom/event pets. Do not damage or guess their names.
    return original


def parse_team_message_detailed(text: str) -> ParsedTeamMessage | None:
    """Parse a team page without silently dropping incomplete positions.

    Animal identity comes from the OwO emoji alias. Missing animals are reported as
    missing positions, while animals without an equipped weapon are retained with an
    empty weapon ID so the user can explicitly choose whether to save them.
    """
    cleaned = _clean_display_text(text)
    lowered = cleaned.lower()
    if not any(marker in lowered for marker in TEAM_MARKERS):
        return None

    # The animal label may be empty for an unused team slot, so the text after [1-3]
    # is optional. Section boundaries still let us inspect the slot independently.
    section_re = re.compile(r"(?m)^[ \t]*\[([1-3])\](?:[ \t]+([^\n]*?))?[ \t]*$")
    matches = list(section_re.finditer(cleaned))
    if not matches:
        return None

    members: list[TeamMember] = []
    seen_positions: set[int] = set()
    missing_positions: set[int] = set()
    missing_weapon_positions: set[int] = set()

    for index, match in enumerate(matches):
        position = int(match.group(1))
        if position in seen_positions:
            continue
        seen_positions.add(position)

        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        section = cleaned[match.end():section_end]
        animal_line = re.sub(r"[*~]", "", match.group(2) or "").strip()

        emoji_alias = extract_first_emoji_alias(animal_line)
        if emoji_alias:
            animal = normalize_animal_emoji_alias(emoji_alias)
        else:
            animal_tokens = re.findall(r"[A-Za-z0-9_'-]+", animal_line)
            # Empty slots and labels containing only decorations have no usable animal.
            animal = animal_tokens[-1] if animal_tokens else ""

        if not animal:
            missing_positions.add(position)
            continue

        weapon_match = re.search(
            r"(?m)^\s*([A-Z0-9]{6})\b(?=.*(?:%|$))",
            section,
        )
        if weapon_match:
            weapon_id = weapon_match.group(1).upper()
        else:
            candidates = re.findall(r"(?<![A-Z0-9])([A-Z0-9]{6})(?![A-Z0-9])", section)
            candidates = [candidate for candidate in candidates if not candidate.isdigit()]
            weapon_id = candidates[-1].upper() if candidates else ""

        if not weapon_id:
            missing_weapon_positions.add(position)

        members.append(TeamMember(position=position, animal=animal, weapon_id=weapon_id))

    # Any position absent from the payload is also incomplete.
    missing_positions.update({1, 2, 3} - seen_positions)
    members.sort(key=lambda member: member.position)

    first_section_start = matches[0].start()
    header_lines = [
        line.strip()
        for line in cleaned[:first_section_start].splitlines()
        if line.strip()
        and not line.lower().startswith("owo team ")
        and not line.lower().startswith("owo rename ")
        and not line.lower().startswith("owo setteam")
    ]
    source_title = header_lines[0] if header_lines else "OwO team"

    return ParsedTeamMessage(
        source_title=source_title[:100],
        members=tuple(members),
        missing_positions=tuple(sorted(missing_positions)),
        missing_weapon_positions=tuple(sorted(missing_weapon_positions)),
    )


def parse_team_message(text: str) -> tuple[str, tuple[TeamMember, ...]] | None:
    """Compatibility wrapper used by team-page detection and reactions."""
    parsed = parse_team_message_detailed(text)
    if parsed is None or not parsed.members:
        return None
    return parsed.source_title, parsed.members


def normalize_owo_prefix(value: str | None) -> str | None:
    """Validate the OwO prefix configured for this Discord server."""
    prefix = re.sub(r"\s+", "", value or "").strip()
    if not prefix:
        return None
    if len(prefix) > MAX_OWO_PREFIX_LENGTH:
        return None
    # Keep the prefix copy/paste safe for generated Discord messages. Most OwO
    # server prefixes are one visible character such as w, o, or g.
    if not re.fullmatch(r"[A-Za-z0-9_!?$./~#%&+\-=]{1,5}", prefix):
        return None
    return prefix.lower()


def owo_team_command(prefix: str, *parts: object) -> str:
    suffix = " ".join(str(part).strip() for part in parts if str(part).strip())
    base = f"{prefix or OWO_PREFIX_DEFAULT}tm"
    return f"{base} {suffix}" if suffix else base


def owo_weapon_command(prefix: str, weapon_id: str, animal: str) -> str:
    return f"{prefix or OWO_PREFIX_DEFAULT}w {weapon_id} {animal}"


def interleaved_member_commands(template: TeamTemplate, owo_prefix: str = OWO_PREFIX_DEFAULT) -> list[str]:
    """Alternate team edits and weapon equips to avoid same-action cooldowns."""
    commands: list[str] = []
    for member in template.members:
        commands.append(owo_team_command(owo_prefix, "a", member.animal, member.position))
        if member.weapon_id:
            commands.append(owo_weapon_command(owo_prefix, member.weapon_id, member.animal))
    return commands


def exact_reset_commands(template: TeamTemplate, owo_prefix: str = OWO_PREFIX_DEFAULT) -> list[str]:
    commands = [owo_team_command(owo_prefix, "d", position) for position in (1, 2, 3)]
    commands.extend(interleaved_member_commands(template, owo_prefix))
    return commands


def quick_replace_commands(template: TeamTemplate, owo_prefix: str = OWO_PREFIX_DEFAULT) -> list[str]:
    return interleaved_member_commands(template, owo_prefix)


def format_command_packet(
    title: str,
    commands: Iterable[str],
    note: str,
    *,
    owo_prefix: str = OWO_PREFIX_DEFAULT,
    helper_prefix: str = HELPER_PREFIX_DEFAULT,
) -> str:
    command_list = list(commands)
    lines = [f"**{title}**", ""]
    lines.extend(f"`{command}`" for command in command_list)
    lines.extend(
        (
            "",
            "⚠️ **Guided mode is active.** The helper posts one command at a time. "
            "As soon as OwO confirms a command, the next command appears immediately.",
            "Animal additions and weapon equips alternate to avoid unnecessary same-action cooldowns.",
            "Saved weapons are equipped to the animal name, not the team position, so position changes are safer.",
            note,
            f"Use `{helper_alias(helper_prefix, 'hs')}` / "
            f"`{helper_command(helper_prefix, 'skip')}` or the **Skip step** button "
            "when a saved animal or weapon is already correct. "
            f"Use `{helper_alias(helper_prefix, 'ht')} cancel` to stop.",
            "The full packet stays here as a backup. At the end, the helper sends "
            f"`{owo_team_command(owo_prefix)}` so you can verify all three animals and weapon IDs before battling.",
        )
    )
    return "\n".join(lines)


def format_plain_command_list(title: str, commands: Iterable[str]) -> str:
    command_list = list(commands)
    lines = [f"**{title}**", ""]
    lines.extend(f"`{command}`" for command in command_list)
    return "\n".join(lines)


def normalize_template_query(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def template_matches_query(template: TeamTemplate, query: str) -> bool:
    normalized_query = normalize_template_query(query)
    if not normalized_query:
        return False
    normalized_name = normalize_template_query(template.name)
    if normalized_name == normalized_query:
        return True
    if normalized_name.startswith(normalized_query):
        return True
    return normalized_query in normalized_name


def normalize_owo_command(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def parse_team_add_target(command: str) -> tuple[str, int] | None:
    """Return the animal and target position from a guided `wtm a` command."""
    match = re.fullmatch(
        r"\S+tm\s+a\s+(.+?)\s+([1-3])",
        normalize_owo_command(command),
    )
    if not match:
        return None
    return match.group(1), int(match.group(2))


def classify_team_confirmation(text: str, command: str) -> str | None:
    """Classify OwO success, cooldown, and team-conflict responses."""
    lowered = re.sub(r"\s+", " ", text or "").lower()
    normalized = normalize_owo_command(command)

    retry_phrases = (
        "slow down",
        "cooldown",
        "please wait",
        "try again",
        "too fast",
        "use this command again",
    )
    if any(phrase in lowered for phrase in retry_phrases):
        return "retry"

    if re.match(r"^\S+w\s+", normalized):
        if any(
            phrase in lowered
            for phrase in (
                "is now wielding",
                "now wielding",
                "already wielding",
                "already equipped",
                "equipped",
            )
        ):
            return "success"
        return None

    if re.match(r"^\S+tm\s+d\s+", normalized):
        if any(
            phrase in lowered
            for phrase in (
                "team has been updated",
                "your team has been updated",
                "no animal",
                "already empty",
                "position is empty",
            )
        ):
            return "success"
        return None

    if re.match(r"^\S+tm\s+a\s+", normalized):
        if any(
            phrase in lowered
            for phrase in (
                "this animal is already in your team",
                "animal is already in your team",
                "already on your team",
            )
        ):
            return "animal_already_in_team"
        if any(
            phrase in lowered
            for phrase in (
                "position is occupied",
                "position is already occupied",
                "already an animal in",
                "already a pet in",
            )
        ):
            return "position_occupied"
        if any(phrase in lowered for phrase in ("team has been updated", "your team has been updated")):
            return "success"
        return None
    return None


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
        logger.warning("Could not fetch referenced OwO team message: %s", exc)
        return None


class TeamTemplateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
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
                CREATE TABLE IF NOT EXISTS team_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    slot INTEGER,
                    name TEXT NOT NULL COLLATE NOCASE,
                    source_title TEXT NOT NULL DEFAULT '',
                    members_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(user_id, name)
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS team_guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    owo_prefix TEXT NOT NULL DEFAULT 'w',
                    helper_prefix TEXT NOT NULL DEFAULT 'h',
                    updated_by INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            config_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(team_guild_config)")
            }
            if "helper_prefix" not in config_columns:
                connection.execute(
                    "ALTER TABLE team_guild_config "
                    "ADD COLUMN helper_prefix TEXT NOT NULL DEFAULT 'h'"
                )

            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(team_templates)")
            }
            if "slot" not in columns:
                connection.execute("ALTER TABLE team_templates ADD COLUMN slot INTEGER")

            # Existing v0.6 databases did not have stable team numbers. Assign them
            # once, in original creation order, and preserve valid slots thereafter.
            user_rows = connection.execute(
                "SELECT DISTINCT user_id FROM team_templates"
            ).fetchall()
            for user_row in user_rows:
                user_id = int(user_row["user_id"])
                rows = connection.execute(
                    """
                    SELECT id, slot FROM team_templates
                    WHERE user_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (user_id,),
                ).fetchall()
                used: set[int] = set()
                for row in rows:
                    current = int(row["slot"] or 0)
                    if 1 <= current <= MAX_TEMPLATES_PER_USER and current not in used:
                        used.add(current)
                        continue
                    slot = next(
                        number
                        for number in range(1, MAX_TEMPLATES_PER_USER + 1)
                        if number not in used
                    )
                    connection.execute(
                        "UPDATE team_templates SET slot = ? WHERE id = ?",
                        (slot, int(row["id"])),
                    )
                    used.add(slot)

            connection.execute("DROP INDEX IF EXISTS idx_team_templates_user")
            connection.execute(
                "CREATE INDEX idx_team_templates_user "
                "ON team_templates(user_id, slot ASC)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_team_templates_user_slot "
                "ON team_templates(user_id, slot)"
            )

    async def get_guild_owo_prefix(self, guild_id: int) -> str:
        async with self.lock:
            return await asyncio.to_thread(self._get_guild_owo_prefix_sync, guild_id)

    def _get_guild_owo_prefix_sync(self, guild_id: int) -> str:
        if not guild_id:
            return OWO_PREFIX_DEFAULT
        with self._connect() as connection:
            row = connection.execute(
                "SELECT owo_prefix FROM team_guild_config WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        prefix = normalize_owo_prefix(str(row["owo_prefix"])) if row else OWO_PREFIX_DEFAULT
        return prefix or OWO_PREFIX_DEFAULT

    async def set_guild_owo_prefix(
        self, guild_id: int, owo_prefix: str, updated_by: int
    ) -> str:
        prefix = normalize_owo_prefix(owo_prefix) or OWO_PREFIX_DEFAULT
        async with self.lock:
            return await asyncio.to_thread(
                self._set_guild_owo_prefix_sync, guild_id, prefix, updated_by
            )

    def _set_guild_owo_prefix_sync(
        self, guild_id: int, owo_prefix: str, updated_by: int
    ) -> str:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO team_guild_config (guild_id, owo_prefix, updated_by, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    owo_prefix = excluded.owo_prefix,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (guild_id, owo_prefix, updated_by, now),
            )
        return owo_prefix

    async def save(
        self,
        user_id: int,
        name: str,
        source_title: str,
        members: tuple[TeamMember, ...],
    ) -> tuple[TeamTemplate | None, str | None]:
        async with self.lock:
            return await asyncio.to_thread(
                self._save_sync, user_id, name, source_title, members
            )

    def _save_sync(
        self,
        user_id: int,
        name: str,
        source_title: str,
        members: tuple[TeamMember, ...],
    ) -> tuple[TeamTemplate | None, str | None]:
        now = int(time.time())
        members_json = json.dumps(
            [
                {
                    "position": member.position,
                    "animal": member.animal,
                    "weapon_id": member.weapon_id,
                }
                for member in members
            ],
            separators=(",", ":"),
        )
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id, slot, created_at FROM team_templates
                WHERE user_id = ? AND name = ?
                """,
                (user_id, name),
            ).fetchone()
            if existing is None:
                used_slots = {
                    int(row["slot"])
                    for row in connection.execute(
                        "SELECT slot FROM team_templates WHERE user_id = ? AND slot IS NOT NULL",
                        (user_id,),
                    )
                    if row["slot"] is not None
                }
                available_slots = [
                    slot
                    for slot in range(1, MAX_TEMPLATES_PER_USER + 1)
                    if slot not in used_slots
                ]
                if not available_slots:
                    return None, (
                        f"You already have {MAX_TEMPLATES_PER_USER} templates. "
                        "Delete one from the saved-team menu before saving another."
                    )
                slot = available_slots[0]
                cursor = connection.execute(
                    """
                    INSERT INTO team_templates
                        (user_id, slot, name, source_title, members_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, slot, name, source_title, members_json, now, now),
                )
                template_id = int(cursor.lastrowid)
                created_at = now
            else:
                template_id = int(existing["id"])
                slot = int(existing["slot"])
                created_at = int(existing["created_at"])
                connection.execute(
                    """
                    UPDATE team_templates
                    SET source_title = ?, members_json = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (source_title, members_json, now, template_id, user_id),
                )

        return (
            TeamTemplate(
                template_id=template_id,
                user_id=user_id,
                slot=slot,
                name=name,
                source_title=source_title,
                members=members,
                created_at=created_at,
                updated_at=now,
            ),
            None,
        )

    async def list_for_user(self, user_id: int) -> list[TeamTemplate]:
        async with self.lock:
            return await asyncio.to_thread(self._list_for_user_sync, user_id)

    def _list_for_user_sync(self, user_id: int) -> list[TeamTemplate]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM team_templates
                WHERE user_id = ?
                ORDER BY slot ASC
                """,
                (user_id,),
            ).fetchall()
        return [self._row_to_template(row) for row in rows]

    async def get(self, user_id: int, template_id: int) -> TeamTemplate | None:
        async with self.lock:
            return await asyncio.to_thread(self._get_sync, user_id, template_id)

    def _get_sync(self, user_id: int, template_id: int) -> TeamTemplate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM team_templates WHERE id = ? AND user_id = ?",
                (template_id, user_id),
            ).fetchone()
        return self._row_to_template(row) if row else None

    async def get_by_slot(self, user_id: int, slot: int) -> TeamTemplate | None:
        async with self.lock:
            return await asyncio.to_thread(self._get_by_slot_sync, user_id, slot)

    def _get_by_slot_sync(self, user_id: int, slot: int) -> TeamTemplate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND slot = ?",
                (user_id, slot),
            ).fetchone()
        return self._row_to_template(row) if row else None

    async def get_by_name(self, user_id: int, name: str) -> TeamTemplate | None:
        async with self.lock:
            return await asyncio.to_thread(self._get_by_name_sync, user_id, name)

    def _get_by_name_sync(self, user_id: int, name: str) -> TeamTemplate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND name = ?",
                (user_id, name),
            ).fetchone()
        return self._row_to_template(row) if row else None

    async def update_existing(
        self,
        user_id: int,
        template_id: int,
        source_title: str,
        members: tuple[TeamMember, ...],
    ) -> TeamTemplate | None:
        async with self.lock:
            return await asyncio.to_thread(
                self._update_existing_sync,
                user_id,
                template_id,
                source_title,
                members,
            )

    def _update_existing_sync(
        self,
        user_id: int,
        template_id: int,
        source_title: str,
        members: tuple[TeamMember, ...],
    ) -> TeamTemplate | None:
        now = int(time.time())
        members_json = json.dumps(
            [
                {
                    "position": member.position,
                    "animal": member.animal,
                    "weapon_id": member.weapon_id,
                }
                for member in members
            ],
            separators=(",", ":"),
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE team_templates
                SET source_title = ?, members_json = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (source_title, members_json, now, template_id, user_id),
            )
            if cursor.rowcount <= 0:
                return None
            row = connection.execute(
                "SELECT * FROM team_templates WHERE id = ? AND user_id = ?",
                (template_id, user_id),
            ).fetchone()
        return self._row_to_template(row) if row else None


    @staticmethod
    def _members_to_json(members: Iterable[TeamMember]) -> str:
        return json.dumps(
            [
                {
                    "position": member.position,
                    "animal": member.animal,
                    "weapon_id": member.weapon_id,
                }
                for member in sorted(members, key=lambda item: item.position)
            ],
            separators=(",", ":"),
        )

    async def rename_template(
        self, user_id: int, template_id: int, new_name: str
    ) -> tuple[TeamTemplate | None, str | None]:
        async with self.lock:
            return await asyncio.to_thread(
                self._rename_template_sync, user_id, template_id, new_name
            )

    def _rename_template_sync(
        self, user_id: int, template_id: int, new_name: str
    ) -> tuple[TeamTemplate | None, str | None]:
        name = re.sub(r"\s+", " ", new_name or "").strip()
        if not name:
            return None, "Use a non-empty team name."
        if len(name) > MAX_TEMPLATE_NAME_LENGTH:
            return None, f"Template names can contain at most {MAX_TEMPLATE_NAME_LENGTH} characters."
        now = int(time.time())
        with self._connect() as connection:
            duplicate = connection.execute(
                """
                SELECT id FROM team_templates
                WHERE user_id = ? AND name = ? AND id <> ?
                """,
                (user_id, name, template_id),
            ).fetchone()
            if duplicate is not None:
                return None, f"You already have a saved team named **{name}**."
            cursor = connection.execute(
                """
                UPDATE team_templates
                SET name = ?, updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (name, now, user_id, template_id),
            )
            if cursor.rowcount <= 0:
                return None, "That saved team no longer exists."
            row = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, template_id),
            ).fetchone()
        return (self._row_to_template(row) if row else None), None

    async def update_member(
        self,
        user_id: int,
        template_id: int,
        position: int,
        animal: str,
        weapon_id: str,
    ) -> tuple[TeamTemplate | None, str | None]:
        async with self.lock:
            return await asyncio.to_thread(
                self._update_member_sync,
                user_id,
                template_id,
                position,
                animal,
                weapon_id,
            )

    def _update_member_sync(
        self,
        user_id: int,
        template_id: int,
        position: int,
        animal: str,
        weapon_id: str,
    ) -> tuple[TeamTemplate | None, str | None]:
        if position not in {1, 2, 3}:
            return None, "Team position must be 1, 2, or 3."
        clean_animal = re.sub(r"\s+", " ", animal or "").strip()
        if not clean_animal:
            return None, "Animal/pet identity cannot be empty."
        clean_weapon = re.sub(r"[^A-Za-z0-9]", "", weapon_id or "").upper()
        if clean_weapon and not re.fullmatch(r"[A-Z0-9]{6}", clean_weapon):
            return None, "Weapon ID must be empty or exactly six letters/numbers."
        now = int(time.time())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, template_id),
            ).fetchone()
            if row is None:
                return None, "That saved team no longer exists."
            template = self._row_to_template(row)
            members_by_position = {member.position: member for member in template.members}
            members_by_position[position] = TeamMember(
                position=position,
                animal=clean_animal,
                weapon_id=clean_weapon,
            )
            members = tuple(members_by_position[pos] for pos in sorted(members_by_position))
            connection.execute(
                """
                UPDATE team_templates
                SET members_json = ?, updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (self._members_to_json(members), now, user_id, template_id),
            )
            row = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, template_id),
            ).fetchone()
        return (self._row_to_template(row) if row else None), None

    async def move_template_to_slot(
        self, user_id: int, template_id: int, target_slot: int
    ) -> tuple[TeamTemplate | None, str | None]:
        async with self.lock:
            return await asyncio.to_thread(
                self._move_template_to_slot_sync, user_id, template_id, target_slot
            )

    def _move_template_to_slot_sync(
        self, user_id: int, template_id: int, target_slot: int
    ) -> tuple[TeamTemplate | None, str | None]:
        if not 1 <= target_slot <= MAX_TEMPLATES_PER_USER:
            return None, f"Team slots must be between 1 and {MAX_TEMPLATES_PER_USER}."
        now = int(time.time())
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM team_templates
                WHERE user_id = ?
                ORDER BY slot ASC, created_at ASC, id ASC
                """,
                (user_id,),
            ).fetchall()
            moving = next((row for row in rows if int(row["id"]) == template_id), None)
            if moving is None:
                return None, "That saved team no longer exists."
            remaining = [row for row in rows if int(row["id"]) != template_id]
            insert_at = min(max(target_slot - 1, 0), len(remaining))
            reordered = remaining[:insert_at] + [moving] + remaining[insert_at:]
            for index, row in enumerate(reordered, start=1):
                connection.execute(
                    "UPDATE team_templates SET slot = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                    (-index, now, user_id, int(row["id"])),
                )
            for index, row in enumerate(reordered, start=1):
                connection.execute(
                    "UPDATE team_templates SET slot = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                    (index, now, user_id, int(row["id"])),
                )
            row = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, template_id),
            ).fetchone()
        return (self._row_to_template(row) if row else None), None

    async def swap_templates(
        self, user_id: int, first_id: int, second_id: int
    ) -> tuple[tuple[TeamTemplate, TeamTemplate] | None, str | None]:
        async with self.lock:
            return await asyncio.to_thread(
                self._swap_templates_sync, user_id, first_id, second_id
            )

    def _swap_templates_sync(
        self, user_id: int, first_id: int, second_id: int
    ) -> tuple[tuple[TeamTemplate, TeamTemplate] | None, str | None]:
        if first_id == second_id:
            return None, "Choose two different saved teams to swap."
        now = int(time.time())
        with self._connect() as connection:
            first = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, first_id),
            ).fetchone()
            second = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, second_id),
            ).fetchone()
            if first is None or second is None:
                return None, "One of those saved teams no longer exists."
            first_slot = int(first["slot"])
            second_slot = int(second["slot"])
            temp_slot = -abs(first_id)
            connection.execute(
                "UPDATE team_templates SET slot = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                (temp_slot, now, user_id, first_id),
            )
            connection.execute(
                "UPDATE team_templates SET slot = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                (first_slot, now, user_id, second_id),
            )
            connection.execute(
                "UPDATE team_templates SET slot = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                (second_slot, now, user_id, first_id),
            )
            first_row = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, first_id),
            ).fetchone()
            second_row = connection.execute(
                "SELECT * FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, second_id),
            ).fetchone()
        if first_row is None or second_row is None:
            return None, "The swap could not be confirmed."
        return (self._row_to_template(first_row), self._row_to_template(second_row)), None
    async def delete_by_id(self, user_id: int, template_id: int) -> bool:
        async with self.lock:
            return await asyncio.to_thread(self._delete_by_id_sync, user_id, template_id)

    def _delete_by_id_sync(self, user_id: int, template_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM team_templates WHERE user_id = ? AND id = ?",
                (user_id, template_id),
            )
        return cursor.rowcount > 0

    async def delete_by_selector(
        self, user_id: int, selector: str
    ) -> TeamTemplate | None:
        async with self.lock:
            return await asyncio.to_thread(
                self._delete_by_selector_sync, user_id, selector
            )

    def _delete_by_selector_sync(
        self, user_id: int, selector: str
    ) -> TeamTemplate | None:
        with self._connect() as connection:
            if selector.isdigit():
                row = connection.execute(
                    "SELECT * FROM team_templates WHERE user_id = ? AND slot = ?",
                    (user_id, int(selector)),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM team_templates WHERE user_id = ? AND name = ?",
                    (user_id, selector),
                ).fetchone()
            if row is None:
                return None
            template = self._row_to_template(row)
            connection.execute(
                "DELETE FROM team_templates WHERE id = ? AND user_id = ?",
                (template.template_id, user_id),
            )
            return template

    @staticmethod
    def _row_to_template(row: sqlite3.Row) -> TeamTemplate:
        payload = json.loads(row["members_json"])
        members = tuple(
            TeamMember(
                position=int(item["position"]),
                animal=str(item["animal"]),
                weapon_id=str(item["weapon_id"]),
            )
            for item in payload
        )
        return TeamTemplate(
            template_id=int(row["id"]),
            user_id=int(row["user_id"]),
            slot=int(row["slot"]),
            name=str(row["name"]),
            source_title=str(row["source_title"]),
            members=members,
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


def format_team_members(members: Iterable[TeamMember]) -> str:
    lines: list[str] = []
    for member in members:
        weapon = f"weapon `{member.weapon_id}`" if member.weapon_id else "**no weapon saved**"
        lines.append(f"**{member.position}.** `{member.animal}` — {weapon}")
    return "\n".join(lines) or "No team members found."


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "These team templates belong to another user. Open your own saved-team list.",
            ephemeral=True,
        )
        return False


class GuidedStepView(discord.ui.View):
    """Owner-only controls for one specific guided-session step."""

    def __init__(
        self,
        cog: "TeamTemplates",
        session: GuidedTeamSession,
        step_index: int,
    ) -> None:
        super().__init__(timeout=GUIDED_SESSION_TIMEOUT_SECONDS)
        self.cog = cog
        self.owner_id = session.user_id
        self.key = cog.guided_key(
            session.guild_id, session.channel_id, session.user_id
        )
        self.step_index = step_index

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "This guided setup belongs to another user.", ephemeral=True
        )
        return False

    @discord.ui.button(
        label="Skip step", emoji="⏭️", style=discord.ButtonStyle.secondary
    )
    async def skip_step(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.cog.skip_guided_step_from_interaction(
            interaction, self.key, self.step_index
        )

    @discord.ui.button(
        label="Cancel", emoji="🛑", style=discord.ButtonStyle.danger
    )
    async def cancel(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.cog.cancel_guided_session_from_interaction(
            interaction, self.key, self.step_index
        )


class MissingWeaponConfirmView(OwnedView):
    """Require an explicit choice before saving a team with missing weapons."""

    def __init__(
        self,
        cog: "TeamTemplates",
        owner_id: int,
        *,
        action: str,
        name: str | None,
        source_title: str,
        members: tuple[TeamMember, ...],
        existing: TeamTemplate | None = None,
    ) -> None:
        super().__init__(owner_id, timeout=180)
        self.cog = cog
        self.action = action
        self.name = name
        self.source_title = source_title
        self.members = members
        self.existing = existing

    @discord.ui.button(label="Save without weapons", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def save_anyway(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        helper_prefix = await get_guild_helper_prefix(interaction.guild_id)
        if self.action == "update" and self.existing is not None:
            updated = await self.cog.store.update_existing(
                interaction.user.id,
                self.existing.template_id,
                self.source_title,
                self.members,
            )
            if updated is None:
                await interaction.response.edit_message(
                    content="That saved team no longer exists.", embed=None, view=None
                )
                return
            embed = self.cog.build_updated_embed(self.existing, updated)
        else:
            template, error = await self.cog.store.save(
                interaction.user.id,
                self.name or "Unnamed team",
                self.source_title,
                self.members,
            )
            if error or template is None:
                await interaction.response.edit_message(
                    content=f"⚠️ {error or 'The team could not be saved.'}",
                    embed=None,
                    view=None,
                )
                return
            embed = self.cog.build_saved_embed(template, helper_prefix)

        await interaction.response.edit_message(content=None, embed=embed, view=None)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content="Cancelled. Equip the missing weapon(s), then run the save or update command again.",
            embed=None,
            view=None,
        )


class TemplateActionView(OwnedView):
    def __init__(
        self,
        cog: "TeamTemplates",
        owner_id: int,
        template: TeamTemplate,
    ) -> None:
        super().__init__(owner_id)
        self.cog = cog
        self.template = template

    @discord.ui.button(label="Quick replace", emoji="⚡", style=discord.ButtonStyle.primary)
    async def quick_replace(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        owo_prefix = await self.cog.store.get_guild_owo_prefix(interaction.guild_id or 0)
        helper_prefix = await get_guild_helper_prefix(interaction.guild_id)
        commands = quick_replace_commands(self.template, owo_prefix)
        packet = format_command_packet(
            f"Quick replace — #{self.template.slot} {self.template.name}",
            commands,
            "This replaces positions directly. If an animal already exists in a "
            "different slot, OwO may reject the add step; the helper will catch that "
            "brief error and tell you how to move it. Unmentioned positions remain unchanged.",
            owo_prefix=owo_prefix,
            helper_prefix=helper_prefix,
        )
        await interaction.response.send_message(packet, ephemeral=True)
        await self.cog.start_guided_session(
            interaction, self.template, commands, "Quick replace", owo_prefix=owo_prefix
        )

    @discord.ui.button(label="Exact reset", emoji="🔄", style=discord.ButtonStyle.success)
    async def exact_reset(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        owo_prefix = await self.cog.store.get_guild_owo_prefix(interaction.guild_id or 0)
        helper_prefix = await get_guild_helper_prefix(interaction.guild_id)
        commands = exact_reset_commands(self.template, owo_prefix)
        packet = format_command_packet(
            f"Exact reset — #{self.template.slot} {self.template.name}",
            commands,
            "This clears all three positions first, then restores each animal and its "
            "weapon as an alternating pair.",
            owo_prefix=owo_prefix,
            helper_prefix=helper_prefix,
        )
        await interaction.response.send_message(packet, ephemeral=True)
        await self.cog.start_guided_session(
            interaction, self.template, commands, "Exact reset", owo_prefix=owo_prefix
        )

    @discord.ui.button(label="All commands", emoji="📋", style=discord.ButtonStyle.secondary)
    async def all_commands(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        owo_prefix = await self.cog.store.get_guild_owo_prefix(interaction.guild_id or 0)
        commands = quick_replace_commands(self.template, owo_prefix)
        packet = format_plain_command_list(
            f"All commands — #{self.template.slot} {self.template.name}",
            commands,
        )
        await interaction.response.send_message(
            packet,
            ephemeral=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Edit team", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def edit_team(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.cog.send_template_editor_from_interaction(
            interaction, self.template.template_id
        )


    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete_template(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        deleted = await self.cog.store.delete_by_id(
            interaction.user.id, self.template.template_id
        )
        if deleted:
            await interaction.response.edit_message(
                content=f"🗑️ Deleted **#{self.template.slot} — {self.template.name}**.",
                embed=None,
                view=None,
            )
            logger.info(
                "Deleted team template %s for user %s",
                self.template.template_id,
                interaction.user.id,
            )
        else:
            await interaction.response.send_message(
                "That template no longer exists.", ephemeral=True
            )



class TeamMemberEditModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "TeamTemplates",
        template: TeamTemplate,
        position: int,
        member: TeamMember | None,
    ) -> None:
        super().__init__(title=f"Edit team #{template.slot} position {position}")
        self.cog = cog
        self.template = template
        self.position = position
        self.animal = discord.ui.TextInput(
            label="Animal / pet identity",
            placeholder="Example: lizard or 2025dec_daisy",
            default=(member.animal if member else ""),
            max_length=80,
            required=True,
        )
        self.weapon_id = discord.ui.TextInput(
            label="Weapon ID",
            placeholder="Six-character ID, or leave empty",
            default=(member.weapon_id if member else ""),
            min_length=0,
            max_length=12,
            required=False,
        )
        self.add_item(self.animal)
        self.add_item(self.weapon_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        updated, error = await self.cog.store.update_member(
            interaction.user.id,
            self.template.template_id,
            self.position,
            str(self.animal.value),
            str(self.weapon_id.value),
        )
        if error or updated is None:
            await interaction.response.send_message(
                f"⚠️ {error or 'The saved team could not be updated.'}",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=self.cog.build_template_editor_embed(updated),
            view=TemplateEditView(self.cog, interaction.user.id, updated),
        )


class TeamRenameModal(discord.ui.Modal):
    def __init__(self, cog: "TeamTemplates", template: TeamTemplate) -> None:
        super().__init__(title=f"Rename team #{template.slot}")
        self.cog = cog
        self.template = template
        self.name = discord.ui.TextInput(
            label="New team name",
            default=template.name,
            max_length=MAX_TEMPLATE_NAME_LENGTH,
            required=True,
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        updated, error = await self.cog.store.rename_template(
            interaction.user.id,
            self.template.template_id,
            str(self.name.value),
        )
        if error or updated is None:
            await interaction.response.send_message(
                f"⚠️ {error or 'The saved team could not be renamed.'}",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=self.cog.build_template_editor_embed(updated),
            view=TemplateEditView(self.cog, interaction.user.id, updated),
        )


class TeamMoveModal(discord.ui.Modal):
    def __init__(self, cog: "TeamTemplates", template: TeamTemplate) -> None:
        super().__init__(title=f"Move team #{template.slot}")
        self.cog = cog
        self.template = template
        self.target_slot = discord.ui.TextInput(
            label="Move to list position",
            placeholder=f"1-{MAX_TEMPLATES_PER_USER}",
            default=str(template.slot),
            max_length=3,
            required=True,
        )
        self.add_item(self.target_slot)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_slot = str(self.target_slot.value).strip()
        if not raw_slot.isdigit():
            await interaction.response.send_message(
                "⚠️ Slot must be a number.", ephemeral=True
            )
            return
        updated, error = await self.cog.store.move_template_to_slot(
            interaction.user.id,
            self.template.template_id,
            int(raw_slot),
        )
        if error or updated is None:
            await interaction.response.send_message(
                f"⚠️ {error or 'The saved team could not be moved.'}",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=self.cog.build_template_editor_embed(updated),
            view=TemplateEditView(self.cog, interaction.user.id, updated),
        )


class TemplateEditView(OwnedView):
    def __init__(
        self,
        cog: "TeamTemplates",
        owner_id: int,
        template: TeamTemplate,
    ) -> None:
        super().__init__(owner_id, timeout=600)
        self.cog = cog
        self.template = template

    async def open_member_modal(self, interaction: discord.Interaction, position: int) -> None:
        latest = await self.cog.store.get(interaction.user.id, self.template.template_id)
        if latest is None:
            await interaction.response.send_message("That saved team no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(
            TeamMemberEditModal(
                self.cog,
                latest,
                position,
                next((member for member in latest.members if member.position == position), None),
            )
        )

    @discord.ui.button(label="Edit pos 1", emoji="1️⃣", style=discord.ButtonStyle.primary)
    async def edit_pos_1(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_member_modal(interaction, 1)

    @discord.ui.button(label="Edit pos 2", emoji="2️⃣", style=discord.ButtonStyle.primary)
    async def edit_pos_2(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_member_modal(interaction, 2)

    @discord.ui.button(label="Edit pos 3", emoji="3️⃣", style=discord.ButtonStyle.primary)
    async def edit_pos_3(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.open_member_modal(interaction, 3)

    @discord.ui.button(label="Rename", emoji="🏷️", style=discord.ButtonStyle.secondary)
    async def rename(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        latest = await self.cog.store.get(interaction.user.id, self.template.template_id)
        if latest is None:
            await interaction.response.send_message("That saved team no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(TeamRenameModal(self.cog, latest))

    @discord.ui.button(label="Move order", emoji="↕️", style=discord.ButtonStyle.secondary)
    async def move_order(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        latest = await self.cog.store.get(interaction.user.id, self.template.template_id)
        if latest is None:
            await interaction.response.send_message("That saved team no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(TeamMoveModal(self.cog, latest))


class TemplateOrderSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "TeamTemplates",
        templates: list[TeamTemplate],
        selected_template_id: int | None,
    ) -> None:
        self.cog = cog
        current = templates[:25]
        options: list[discord.SelectOption] = []
        for template in current:
            options.append(
                discord.SelectOption(
                    label=f"#{template.slot} — {template.name}"[:100],
                    value=str(template.template_id),
                    description=("selected" if template.template_id == selected_template_id else "move this saved team")[:100],
                    emoji=("✅" if template.template_id == selected_template_id else "🐾"),
                    default=(template.template_id == selected_template_id),
                )
            )
        super().__init__(
            placeholder="Choose a team to move up/down…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        templates = await self.cog.store.list_for_user(interaction.user.id)
        selected_id = int(self.values[0])
        await interaction.response.edit_message(
            embed=self.cog.build_order_embed(templates, selected_id),
            view=TemplateOrderView(self.cog, interaction.user.id, templates, selected_id),
        )


class TemplateOrderView(OwnedView):
    def __init__(
        self,
        cog: "TeamTemplates",
        owner_id: int,
        templates: list[TeamTemplate],
        selected_template_id: int | None = None,
    ) -> None:
        super().__init__(owner_id, timeout=600)
        self.cog = cog
        self.templates = templates
        self.selected_template_id = selected_template_id
        if templates:
            self.add_item(TemplateOrderSelect(cog, templates, selected_template_id))
        selected = self.selected_template()
        self.move_up.disabled = selected is None or selected.slot <= 1
        self.move_down.disabled = selected is None or selected.slot >= len(templates)

    def selected_template(self) -> TeamTemplate | None:
        if self.selected_template_id is None:
            return None
        return next(
            (template for template in self.templates if template.template_id == self.selected_template_id),
            None,
        )

    async def move_selected(self, interaction: discord.Interaction, delta: int) -> None:
        selected = self.selected_template()
        if selected is None:
            await interaction.response.send_message("Choose a team first.", ephemeral=True)
            return
        target_slot = max(1, min(len(self.templates), selected.slot + delta))
        updated, error = await self.cog.store.move_template_to_slot(
            interaction.user.id,
            selected.template_id,
            target_slot,
        )
        if error or updated is None:
            await interaction.response.send_message(
                f"⚠️ {error or 'The team order could not be updated.'}",
                ephemeral=True,
            )
            return
        templates = await self.cog.store.list_for_user(interaction.user.id)
        await interaction.response.edit_message(
            embed=self.cog.build_order_embed(templates, updated.template_id),
            view=TemplateOrderView(self.cog, interaction.user.id, templates, updated.template_id),
        )

    @discord.ui.button(label="Move up", emoji="⬆️", style=discord.ButtonStyle.secondary, row=1)
    async def move_up(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.move_selected(interaction, -1)

    @discord.ui.button(label="Move down", emoji="⬇️", style=discord.ButtonStyle.secondary, row=1)
    async def move_down(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.move_selected(interaction, 1)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        templates = await self.cog.store.list_for_user(interaction.user.id)
        selected_id = self.selected_template_id
        await interaction.response.edit_message(
            embed=self.cog.build_order_embed(templates, selected_id),
            view=TemplateOrderView(self.cog, interaction.user.id, templates, selected_id),
        )
class TemplateSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "TeamTemplates",
        templates: list[TeamTemplate],
        page: int,
    ) -> None:
        self.cog = cog
        start = page * TEMPLATE_PAGE_SIZE
        current = templates[start:start + TEMPLATE_PAGE_SIZE]
        options: list[discord.SelectOption] = []
        for template in current:
            animals = " / ".join(member.animal for member in template.members)
            options.append(
                discord.SelectOption(
                    label=f"#{template.slot} — {template.name}"[:100],
                    value=str(template.template_id),
                    description=animals[:100] or "Saved OwO team",
                    emoji="🐾",
                )
            )
        super().__init__(
            placeholder="Choose a saved team template…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        template = await self.cog.store.get(
            interaction.user.id, int(self.values[0])
        )
        if template is None:
            await interaction.response.send_message(
                "That template no longer exists. Open your saved-team list again.", ephemeral=True
            )
            return

        member_lines = format_team_members(template.members)
        embed = discord.Embed(
            title=f"🐾 #{template.slot} — {template.name}",
            description=(
                f"Saved from **{template.source_title}**\n\n{member_lines}\n\n"
                "Choose **Quick replace** to overwrite the listed positions, or "
                "**Exact reset** to clear all positions first."
            ),
            color=0x5865F2,
        )
        await interaction.response.send_message(
            embed=embed,
            view=TemplateActionView(
                self.cog, interaction.user.id, template
            ),
            ephemeral=True,
        )


class TemplateListView(OwnedView):
    def __init__(
        self,
        cog: "TeamTemplates",
        owner_id: int,
        templates: list[TeamTemplate],
        *,
        page: int = 0,
    ) -> None:
        super().__init__(owner_id)
        self.cog = cog
        self.templates = templates
        self.page_count = max(
            1,
            (len(templates) + TEMPLATE_PAGE_SIZE - 1) // TEMPLATE_PAGE_SIZE,
        )
        self.page = max(0, min(page, self.page_count - 1))
        self.add_item(TemplateSelect(cog, templates, self.page))

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
        order_button = discord.ui.Button(
            label="Edit order",
            emoji="↕️",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        previous.callback = self.previous_page
        next_button.callback = self.next_page
        order_button.callback = self.open_order_view
        self.add_item(previous)
        self.add_item(next_button)
        self.add_item(order_button)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        target = max(0, self.page - 1)
        await interaction.response.edit_message(
            embed=self.cog.build_template_list_embed(self.templates, target),
            view=TemplateListView(
                self.cog,
                interaction.user.id,
                self.templates,
                page=target,
            ),
        )

    async def next_page(self, interaction: discord.Interaction) -> None:
        target = min(self.page_count - 1, self.page + 1)
        await interaction.response.edit_message(
            embed=self.cog.build_template_list_embed(self.templates, target),
            view=TemplateListView(
                self.cog,
                interaction.user.id,
                self.templates,
                page=target,
            ),
        )

    async def open_order_view(self, interaction: discord.Interaction) -> None:
        templates = await self.cog.store.list_for_user(interaction.user.id)
        await interaction.response.send_message(
            embed=self.cog.build_order_embed(templates, None),
            view=TemplateOrderView(self.cog, interaction.user.id, templates),
            ephemeral=True,
        )


class TeamTemplates(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = TeamTemplateStore(DATABASE_FILE)
        self.reaction_instruction_cooldowns: dict[tuple[int, int], float] = {}
        self.guided_sessions: dict[tuple[int, int, int], GuidedTeamSession] = {}
        self.guided_timeout_tasks: dict[tuple[int, int, int], asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        await self.store.initialize()
        logger.info("Team template storage ready at %s", DATABASE_FILE)

    async def cog_unload(self) -> None:
        for task in self.guided_timeout_tasks.values():
            task.cancel()
        self.guided_timeout_tasks.clear()
        self.guided_sessions.clear()

    @staticmethod
    def guided_key(guild_id: int, channel_id: int, user_id: int) -> tuple[int, int, int]:
        return guild_id, channel_id, user_id

    def clear_guided_session(self, key: tuple[int, int, int]) -> GuidedTeamSession | None:
        session = self.guided_sessions.pop(key, None)
        task = self.guided_timeout_tasks.pop(key, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        return session

    def reset_guided_timeout(
        self, key: tuple[int, int, int], session: GuidedTeamSession
    ) -> None:
        old_task = self.guided_timeout_tasks.pop(key, None)
        if old_task and old_task is not asyncio.current_task():
            old_task.cancel()
        self.guided_timeout_tasks[key] = asyncio.create_task(
            self.expire_guided_session(key, session)
        )

    async def expire_guided_session(
        self, key: tuple[int, int, int], session: GuidedTeamSession
    ) -> None:
        try:
            await asyncio.sleep(GUIDED_SESSION_TIMEOUT_SECONDS)
            if self.guided_sessions.get(key) is not session:
                return
            self.guided_sessions.pop(key, None)
            self.guided_timeout_tasks.pop(key, None)
            logger.info(
                "Expired guided team session for user %s in channel %s",
                session.user_id,
                session.channel_id,
            )
        except asyncio.CancelledError:
            return

    async def start_guided_session(
        self,
        interaction: discord.Interaction,
        template: TeamTemplate,
        commands_to_run: Iterable[str],
        mode: str,
        *,
        owo_prefix: str = OWO_PREFIX_DEFAULT,
    ) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            return
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return

        key = self.guided_key(
            interaction.guild_id, interaction.channel_id, interaction.user.id
        )
        self.clear_guided_session(key)
        identity_sources = [
            getattr(interaction.user, "display_name", ""),
            getattr(interaction.user, "global_name", ""),
            getattr(interaction.user, "name", ""),
        ]
        identity_tokens = tuple(
            sorted(
                {
                    token.lower()
                    for source in identity_sources
                    for token in re.findall(r"[A-Za-z0-9_]{4,}", source or "")
                },
                key=len,
                reverse=True,
            )
        )
        helper_prefix = await get_guild_helper_prefix(interaction.guild_id)
        session = GuidedTeamSession(
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            template_id=template.template_id,
            template_slot=template.slot,
            template_name=template.name,
            identity_tokens=identity_tokens,
            mode=mode,
            commands=tuple(commands_to_run),
            owo_prefix=owo_prefix,
            helper_prefix=helper_prefix,
            last_activity=time.monotonic(),
        )
        self.guided_sessions[key] = session
        self.reset_guided_timeout(key, session)
        await self.send_guided_step(channel, session)
        logger.info(
            "Started %s guided team session for user %s using team #%s",
            mode,
            interaction.user.id,
            template.slot,
        )

    async def send_guided_step(
        self,
        channel: discord.abc.Messageable,
        session: GuidedTeamSession,
        *,
        notice: str | None = None,
    ) -> None:
        expected = session.expected_command
        if expected is None:
            return
        session.ready_for_user = True
        session.waiting_for_owo = False
        session.command_message_id = None
        session.command_sent_at = 0.0
        session.last_activity = time.monotonic()

        lines = [
            f"<@{session.user_id}> **{session.mode} — team "
            f"#{session.template_slot} `{session.template_name}`**",
        ]
        if notice:
            lines.extend((notice, ""))
        lines.extend(
            (
                f"Step **{session.next_index + 1}/{len(session.commands)}**:",
                f"`{expected}`",
                "Send this exact command. The next step appears immediately after "
                "OwO confirms it.",
                f"Use `{helper_alias(session.helper_prefix, 'hs')}` / "
                f"`{helper_command(session.helper_prefix, 'skip')}` or **Skip step** "
                "when this step is already correct. "
                f"Use `{helper_alias(session.helper_prefix, 'ht')} cancel` to stop.",
            )
        )
        sent = await channel.send(
            "\n".join(lines),
            view=GuidedStepView(self, session, session.next_index),
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False, replied_user=False
            ),
        )
        session.prompt_message_id = sent.id

    async def delete_message_safely(
        self,
        channel: discord.abc.Messageable,
        message_id: int | None,
        *,
        member_message: bool = False,
    ) -> None:
        """Delete a guided message without making cleanup a workflow dependency."""
        if not message_id:
            return
        get_partial = getattr(channel, "get_partial_message", None)
        if get_partial is None:
            return
        try:
            await get_partial(message_id).delete()
        except discord.Forbidden:
            if member_message:
                logger.debug(
                    "Manage Messages is unavailable; kept guided user command %s",
                    message_id,
                )
        except (discord.NotFound, discord.HTTPException):
            return

    async def finish_guided_session(
        self,
        channel: discord.abc.Messageable,
        key: tuple[int, int, int],
        session: GuidedTeamSession,
    ) -> None:
        self.clear_guided_session(key)
        await channel.send(
            (
                f"<@{session.user_id}> ✅ **Team #{session.template_slot} "
                f"`{session.template_name}` setup finished.**\n"
                "Run this final check and confirm every animal and weapon before battling:\n"
                f"`{owo_team_command(session.owo_prefix)}`"
            ),
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False, replied_user=False
            ),
        )
        logger.info(
            "Completed guided team session for user %s using team #%s",
            session.user_id,
            session.template_slot,
        )

    async def skip_guided_step_from_interaction(
        self,
        interaction: discord.Interaction,
        key: tuple[int, int, int],
        step_index: int,
    ) -> None:
        session = self.guided_sessions.get(key)
        if session is None or session.next_index != step_index:
            await interaction.response.send_message(
                "That step is no longer active.", ephemeral=True
            )
            return
        if session.waiting_for_owo:
            await interaction.response.send_message(
                "OwO is still responding to the command you sent. Wait for that "
                "response before skipping.",
                ephemeral=True,
            )
            return

        skipped = session.expected_command or "this step"
        session.next_index += 1
        session.ready_for_user = False
        session.prompt_message_id = None
        session.last_activity = time.monotonic()
        self.reset_guided_timeout(key, session)
        await interaction.response.edit_message(
            content=f"⏭️ Skipped `{skipped}`.", view=None
        )

        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return
        if session.next_index >= len(session.commands):
            await self.finish_guided_session(channel, key, session)
        else:
            await self.send_guided_step(
                channel, session, notice=f"⏭️ Skipped `{skipped}`."
            )

    async def cancel_guided_session_from_interaction(
        self,
        interaction: discord.Interaction,
        key: tuple[int, int, int],
        step_index: int,
    ) -> None:
        session = self.guided_sessions.get(key)
        if session is None or session.next_index != step_index:
            await interaction.response.send_message(
                "That guided setup is no longer active.", ephemeral=True
            )
            return
        self.clear_guided_session(key)
        await interaction.response.edit_message(
            content=(
                f"🛑 Stopped the guided setup for team "
                f"**#{session.template_slot} {session.template_name}**."
            ),
            view=None,
        )

    async def handle_guided_user_command(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        key = self.guided_key(message.guild.id, message.channel.id, message.author.id)
        session = self.guided_sessions.get(key)
        if session is None or not session.ready_for_user or session.waiting_for_owo:
            return False
        expected = session.expected_command
        if expected is None:
            return False
        if normalize_owo_command(message.content) != normalize_owo_command(expected):
            return False

        session.ready_for_user = False
        session.waiting_for_owo = True
        session.command_message_id = message.id
        session.command_sent_at = time.monotonic()
        session.last_activity = session.command_sent_at
        self.reset_guided_timeout(key, session)

        # Remove the helper's previous prompt as soon as the user sends the expected
        # command. The member's command stays visible until OwO has responded, so we
        # never race OwO's message listener.
        previous_prompt_id = session.prompt_message_id
        session.prompt_message_id = None
        await self.delete_message_safely(message.channel, previous_prompt_id)

        logger.info(
            "User %s sent guided team step %s/%s in channel %s",
            message.author.id,
            session.next_index + 1,
            len(session.commands),
            message.channel.id,
        )
        return True

    def find_guided_session_for_owo_message(
        self, message: discord.Message, text: str
    ) -> tuple[tuple[int, int, int], GuidedTeamSession, str] | None:
        if message.guild is None:
            return None
        waiting: list[tuple[tuple[int, int, int], GuidedTeamSession]] = []
        for key, session in self.guided_sessions.items():
            if session.guild_id != message.guild.id or session.channel_id != message.channel.id:
                continue
            if not session.waiting_for_owo or session.expected_command is None:
                continue
            if time.monotonic() - session.command_sent_at <= 45:
                waiting.append((key, session))
        if not waiting:
            return None

        reference_id = (
            message.reference.message_id
            if message.reference is not None
            else None
        )
        if reference_id is not None:
            for key, session in waiting:
                if session.command_message_id == reference_id:
                    status = classify_team_confirmation(text, session.expected_command or "")
                    return key, session, status or "retry"

        mentioned_ids = {user.id for user in message.mentions}
        if mentioned_ids:
            mentioned = [item for item in waiting if item[1].user_id in mentioned_ids]
            for key, session in sorted(
                mentioned, key=lambda item: item[1].command_sent_at
            ):
                status = classify_team_confirmation(text, session.expected_command or "")
                if status is not None:
                    return key, session, status

        lowered_text = text.lower()
        identified = [
            item
            for item in waiting
            if any(token in lowered_text for token in item[1].identity_tokens)
        ]
        for key, session in sorted(
            identified, key=lambda item: item[1].command_sent_at
        ):
            status = classify_team_confirmation(text, session.expected_command or "")
            if status is not None:
                return key, session, status

        classified: list[
            tuple[tuple[int, int, int], GuidedTeamSession, str]
        ] = []
        for key, session in waiting:
            status = classify_team_confirmation(text, session.expected_command or "")
            if status is not None:
                classified.append((key, session, status))
        if not classified:
            return None

        # OwO normally processes messages in channel order. FIFO is the safest
        # fallback when its response does not reference or mention the requester.
        return min(classified, key=lambda item: item[1].command_sent_at)

    async def handle_guided_owo_confirmation(self, message: discord.Message) -> bool:
        # OwO error messages can disappear after only a few seconds, but the gateway
        # event reaches this listener immediately, so no polling or OCR is needed.
        text = extract_message_text(message)
        found = self.find_guided_session_for_owo_message(message, text)
        if found is None:
            return False
        key, session, status = found
        user_command_message_id = session.command_message_id
        session.waiting_for_owo = False
        session.command_message_id = None
        session.last_activity = time.monotonic()

        if DELETE_CONFIRMED_USER_COMMANDS:
            # OwO has already responded, so deleting the member's command now cannot
            # race the OwO listener. Manage Messages is optional; failure is harmless.
            await self.delete_message_safely(
                message.channel,
                user_command_message_id,
                member_message=True,
            )

        notice: str | None = None
        if status == "success":
            session.next_index += 1
        elif status == "animal_already_in_team":
            target = parse_team_add_target(session.expected_command or "")
            if target is None:
                notice = (
                    "⚠️ This animal is already in your team. If it is in the wrong "
                    "position, run the matching remove command for that animal and retry this step; if it is already "
                    f"correct, press **Skip step** or type "
                    f"`{helper_alias(session.helper_prefix, 'hs')}`."
                )
            else:
                animal, target_position = target
                retry_command = owo_team_command(session.owo_prefix, "a", animal, target_position)
                notice = (
                    f"⚠️ `{animal}` is already in your team. If it is in the wrong "
                    f"position, run `wtm d {animal}` then resend `{retry_command}`; if "
                    f"it is already correct, press **Skip step** or type "
                    f"`{helper_alias(session.helper_prefix, 'hs')}`."
                )
            logger.info(
                "Guided add conflict for user %s at step %s",
                session.user_id,
                session.next_index + 1,
            )
        elif status == "position_occupied":
            target = parse_team_add_target(session.expected_command or "")
            position = target[1] if target else None
            remove_command = f"`{owo_team_command(session.owo_prefix, 'd', position)}`" if position else f"`{owo_team_command(session.owo_prefix, 'd', '<position>')}`"
            notice = (
                "⚠️ The target team position is occupied. Remove the current animal "
                f"with {remove_command}, then retry this step. Use "
                f"`{helper_alias(session.helper_prefix, 'hs')}` only when the "
                "saved animal is already correct."
            )
        else:
            notice = (
                "⏳ OwO did not confirm that step, usually because of a temporary "
                "cooldown. Retry the same command when OwO is ready, or use "
                f"`{helper_alias(session.helper_prefix, 'hs')}` to skip it."
            )
            logger.info(
                "OwO did not confirm guided team step for user %s; retrying step %s",
                session.user_id,
                session.next_index + 1,
            )

        self.reset_guided_timeout(key, session)
        if self.guided_sessions.get(key) is not session:
            return True

        channel = message.channel
        if status == "success" and session.next_index >= len(session.commands):
            await self.finish_guided_session(channel, key, session)
            return True

        # No artificial delay: alternating add/equip commands avoids the repeated
        # action cooldown, and the next prompt appears as soon as OwO responds.
        await self.send_guided_step(channel, session, notice=notice)
        return True

    async def skip_guided_session(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        key = self.guided_key(message.guild.id, message.channel.id, message.author.id)
        session = self.guided_sessions.get(key)
        if session is None:
            await message.reply(
                "You do not have an active guided team step in this channel.",
                mention_author=False,
            )
            return
        if session.waiting_for_owo:
            await message.reply(
                "OwO is still responding to the command you sent. Wait for that "
                f"response before using `{helper_alias(session.helper_prefix, 'hs')}` "
                f"or `{helper_command(session.helper_prefix, 'skip')}`.",
                mention_author=False,
            )
            return

        skipped = session.expected_command
        if skipped is None:
            return
        previous_prompt_id = session.prompt_message_id
        session.prompt_message_id = None
        session.ready_for_user = False
        session.next_index += 1
        session.last_activity = time.monotonic()
        self.reset_guided_timeout(key, session)

        await self.delete_message_safely(message.channel, previous_prompt_id)
        if DELETE_CONFIRMED_USER_COMMANDS:
            await self.delete_message_safely(
                message.channel, message.id, member_message=True
            )

        logger.info(
            "User %s skipped guided team step %s in channel %s",
            message.author.id,
            session.next_index,
            message.channel.id,
        )
        if session.next_index >= len(session.commands):
            await self.finish_guided_session(message.channel, key, session)
            return
        await self.send_guided_step(
            message.channel,
            session,
            notice=f"⏭️ Skipped `{skipped}`.",
        )

    async def cancel_guided_session(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        key = self.guided_key(message.guild.id, message.channel.id, message.author.id)
        session = self.clear_guided_session(key)
        if session is None:
            await message.reply(
                "You do not have an active guided team setup in this channel.",
                mention_author=False,
            )
            return

        await self.delete_message_safely(message.channel, session.prompt_message_id)
        if DELETE_CONFIRMED_USER_COMMANDS:
            await self.delete_message_safely(
                message.channel, message.id, member_message=True
            )
        await message.channel.send(
            f"<@{message.author.id}> 🛑 Stopped the guided setup for team "
            f"**#{session.template_slot} {session.template_name}**.",
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False, replied_user=False
            ),
        )

    @staticmethod
    def can_manage_team_settings(member: discord.abc.User | discord.Member) -> bool:
        permissions = getattr(member, "guild_permissions", None)
        return bool(
            permissions
            and (
                getattr(permissions, "manage_guild", False)
                or getattr(permissions, "administrator", False)
            )
        )

    async def set_owo_prefix_command(self, message: discord.Message, argument: str | None) -> None:
        if message.guild is None:
            return
        current = await self.store.get_guild_owo_prefix(message.guild.id)
        helper_prefix = await get_guild_helper_prefix(message.guild.id)
        team_short = helper_alias(helper_prefix, "ht")
        value = re.sub(r"\s+", "", argument or "").strip()
        if not value:
            await message.reply(
                f"This server's OwO prefix for helper-generated OwO commands is currently `{current}`. "
                f"Generated commands use `{owo_team_command(current)}` and `{current}w <weapon_id> <animal>`. "
                f"Server managers can change it with `{team_short} prefix <prefix>`, "
                f"for example `{team_short} prefix o`.",
                mention_author=False,
            )
            return
        if not isinstance(message.author, discord.Member) or not self.can_manage_team_settings(message.author):
            await message.reply(
                "Only members with **Manage Server** can change the OwO prefix used for team restore commands.",
                mention_author=False,
            )
            return
        prefix = normalize_owo_prefix(value)
        if prefix is None:
            await message.reply(
                f"Use a short no-space prefix, up to **{MAX_OWO_PREFIX_LENGTH}** characters. "
                f"Examples: `{team_short} prefix w`, `{team_short} prefix o`, "
                f"or `{team_short} prefix g`.",
                mention_author=False,
            )
            return
        saved = await self.store.set_guild_owo_prefix(message.guild.id, prefix, message.author.id)
        await message.reply(
            f"✅ OwO prefix saved for this server: `{saved}`. "
            f"Guided team restore, boss inventory, and boss-ticket tracking now use `{owo_team_command(saved)}` and `{saved}w <weapon_id> <animal>`.",
            mention_author=False,
        )

    async def send_team_help(self, message: discord.Message) -> None:
        helper_prefix = await get_guild_helper_prefix(
            message.guild.id if message.guild else None
        )
        team_short = helper_alias(helper_prefix, "ht")
        team_menu = helper_alias(helper_prefix, "htm")
        embed = discord.Embed(
            title="💾 OwO Team Templates",
            description=(
                f"Save up to **{MAX_TEMPLATES_PER_USER}** personal templates. Each "
                "template keeps a stable number, every animal position, and the exact "
                "six-character weapon ID. Server managers can set the local OwO prefix, "
                "so servers using `otm` / `gtm` do not need to edit generated commands."
            ),
            color=0x5865F2,
        )
        embed.add_field(
            name="Save a team",
            value=(
                "Run `wtm` or `owo team`, open the correct page, and reply to it with "
                f"`{team_short} C <name>`. Full form: "
                f"`{helper_command(helper_prefix, 'team create <name>')}`. The helper reads "
                "the animal emoji alias, so custom pet nicknames do not break restores."
            ),
            inline=False,
        )
        embed.add_field(
            name="Open teams",
            value=(
                f"`{team_short}` or `{team_menu}` opens the dropdown. "
                f"`{team_short}3` or `{team_menu}3` opens team #3 "
                "directly without the extra selection step."
            ),
            inline=False,
        )
        embed.add_field(
            name="Update, delete, skip, and cancel",
            value=(
                f"Reply to a fresh OwO team page with `{team_short} U <number or name>` "
                f"to update it. Use `{team_short} D <number or name>` to delete it. "
                f"During guided setup, use `{helper_alias(helper_prefix, 'hs')}` / "
                f"`{helper_command(helper_prefix, 'skip')}` to skip the current step, "
                f"or `{team_short} cancel` to stop."
            ),
            inline=False,
        )
        embed.add_field(
            name="Server OwO prefix",
            value=(
                "Default generated commands use `wtm` and `ww`. If your server uses "
                f"another OwO prefix, a manager can run `{team_short} prefix o` or "
                f"`{helper_command(helper_prefix, 'team prefix <prefix>')}`. "
                f"Use `{team_short} prefix` to check the current setting."
            ),
            inline=False,
        )
        embed.add_field(
            name="Guided setup",
            value=(
                "Choose **Quick replace** or **Exact reset**. The helper shows the full "
                "packet, then posts one command at a time. Animal adds and weapon equips "
                "alternate, and weapon equips use the animal name instead of the team position. "
                "Use **All commands** to post a public, clean command list in one message. "
                "Discord does not allow true drag-and-drop inside bot messages, so "
                f"ordering uses buttons, `{team_short} move`, and `{team_short} swap`."
            ),
            inline=False,
        )
        embed.set_footer(
            text="The final check uses this server's configured OwO team command."
        )
        await message.reply(embed=embed, mention_author=False)

    async def parse_team_reply(
        self, message: discord.Message
    ) -> ParsedTeamMessage | None:
        if message.reference is None or message.reference.message_id is None:
            await message.reply(
                "Reply directly to the OwO team message you want to use.",
                mention_author=False,
            )
            return None

        channel_id = message.reference.channel_id or message.channel.id
        raw = await fetch_raw_message(
            self.bot, int(channel_id), int(message.reference.message_id)
        )
        if raw is None:
            await message.reply(
                "I could not read that referenced message. Check my **Read Message History** "
                "permission and try again.",
                mention_author=False,
            )
            return None
        author_id = int((raw.get("author") or {}).get("id", 0) or 0)
        if author_id != OWO_BOT_ID:
            await message.reply(
                "That reply is not pointing to an official OwO Bot team message.",
                mention_author=False,
            )
            return None

        parsed = parse_team_message_detailed(extract_all_text(raw))
        if parsed is None:
            await message.reply(
                "I could not read that OwO team page. Make sure you replied to the "
                "visible `wtm` / `owo team` message.",
                mention_author=False,
            )
            return None

        if parsed.missing_positions:
            positions = ", ".join(str(position) for position in parsed.missing_positions)
            await message.reply(
                f"❌ This team is incomplete. Add an animal to position(s) **{positions}** "
                "before saving or updating the template.",
                mention_author=False,
            )
            return None
        return parsed

    @staticmethod
    def build_saved_embed(
        template: TeamTemplate,
        helper_prefix: str = HELPER_PREFIX_DEFAULT,
    ) -> discord.Embed:
        team_short = helper_alias(helper_prefix, "ht")
        return discord.Embed(
            title=f"✅ Team #{template.slot} saved: {template.name}",
            description=(
                f"{format_team_members(template.members)}\n\n"
                f"Use `{team_short}{template.slot}` or `{team_short} {template.name}` "
                f"to open it directly, or `{team_short}` to open the full list."
            ),
            color=0x57F287,
        )

    @staticmethod
    def build_updated_embed(before: TeamTemplate, after: TeamTemplate) -> discord.Embed:
        return discord.Embed(
            title=f"✅ Team #{after.slot} updated: {after.name}",
            description=(
                "**Before**\n"
                f"{format_team_members(before.members)}\n\n"
                "**After**\n"
                f"{format_team_members(after.members)}"
            ),
            color=0x57F287,
        )

    async def confirm_or_save_create(
        self,
        message: discord.Message,
        name: str,
        parsed: ParsedTeamMessage,
    ) -> None:
        if parsed.missing_weapon_positions:
            positions = ", ".join(str(position) for position in parsed.missing_weapon_positions)
            await message.reply(
                f"⚠️ Animal position(s) **{positions}** have no equipped weapon. "
                "You can save the team without those weapon commands, or cancel and equip them first.",
                view=MissingWeaponConfirmView(
                    self,
                    message.author.id,
                    action="create",
                    name=name,
                    source_title=parsed.source_title,
                    members=parsed.members,
                ),
                mention_author=False,
            )
            return

        template, error = await self.store.save(
            message.author.id, name, parsed.source_title, parsed.members
        )
        if error or template is None:
            await message.reply(f"⚠️ {error or 'The team could not be saved.'}", mention_author=False)
            return
        helper_prefix = await get_guild_helper_prefix(
            message.guild.id if message.guild else None
        )
        await message.reply(
            embed=self.build_saved_embed(template, helper_prefix),
            mention_author=False,
        )
        logger.info(
            "Saved team template %s for user %s with %s member(s)",
            template.template_id,
            message.author.id,
            len(template.members),
        )

    async def save_from_reply(self, message: discord.Message, requested_name: str) -> None:
        name = re.sub(r"\s+", " ", requested_name).strip()
        if not name:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            await message.reply(
                f"Use `{helper_alias(helper_prefix, 'ht')} C <name>` or "
                f"`{helper_command(helper_prefix, 'team create <name>')}`.",
                mention_author=False,
            )
            return
        if len(name) > MAX_TEMPLATE_NAME_LENGTH:
            await message.reply(
                f"Template names can contain at most {MAX_TEMPLATE_NAME_LENGTH} characters.",
                mention_author=False,
            )
            return
        parsed = await self.parse_team_reply(message)
        if parsed is None:
            return
        await self.confirm_or_save_create(message, name, parsed)

    async def update_from_reply(
        self, message: discord.Message, selector: str | None
    ) -> None:
        value = re.sub(r"\s+", " ", selector or "").strip()
        if not value:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            team_short = helper_alias(helper_prefix, "ht")
            await message.reply(
                f"Use `{team_short} U <number or name>`, for example "
                f"`{team_short}U 3` or `{team_short} U boss team`.",
                mention_author=False,
            )
            return

        existing = (
            await self.store.get_by_slot(message.author.id, int(value))
            if value.isdigit()
            else await self.store.get_by_name(message.author.id, value)
        )
        if existing is None:
            await message.reply(
                f"I could not find a saved team matching **{value}**.",
                mention_author=False,
            )
            return

        parsed = await self.parse_team_reply(message)
        if parsed is None:
            return

        if parsed.missing_weapon_positions:
            positions = ", ".join(str(position) for position in parsed.missing_weapon_positions)
            await message.reply(
                f"⚠️ Animal position(s) **{positions}** have no equipped weapon. "
                "You can update the saved team without those weapon commands, or cancel and equip them first.",
                view=MissingWeaponConfirmView(
                    self,
                    message.author.id,
                    action="update",
                    name=None,
                    source_title=parsed.source_title,
                    members=parsed.members,
                    existing=existing,
                ),
                mention_author=False,
            )
            return

        updated = await self.store.update_existing(
            message.author.id,
            existing.template_id,
            parsed.source_title,
            parsed.members,
        )
        if updated is None:
            await message.reply("That saved team no longer exists.", mention_author=False)
            return
        await message.reply(
            embed=self.build_updated_embed(existing, updated), mention_author=False
        )
        logger.info(
            "Updated team template %s (slot %s) for user %s",
            updated.template_id,
            updated.slot,
            message.author.id,
        )


    def build_template_editor_embed(self, template: TeamTemplate) -> discord.Embed:
        embed = discord.Embed(
            title=f"✏️ Edit team #{template.slot} — {template.name}",
            description=(
                f"Saved from **{template.source_title}**\n\n"
                f"{format_team_members(template.members)}\n\n"
                "Use the buttons below to edit one position, rename the team, or move it in your saved-team order. "
                "Weapon IDs stay tied to the animal/pet identity, not the visible team position."
            ),
            color=0xFEE75C,
        )
        embed.set_footer(
            text="Discord does not support real drag-and-drop in bot messages; use the buttons or saved-team move/swap commands."
        )
        return embed

    def build_order_embed(
        self,
        templates: list[TeamTemplate],
        selected_template_id: int | None,
    ) -> discord.Embed:
        selected = next(
            (template for template in templates if template.template_id == selected_template_id),
            None,
        )
        lines: list[str] = []
        for template in templates[:25]:
            marker = "✅" if selected and selected.template_id == template.template_id else "•"
            lines.append(f"{marker} `#{template.slot}` **{discord.utils.escape_markdown(template.name)}**")
        if len(templates) > 25:
            lines.append(f"…and {len(templates) - 25} more saved teams.")
        embed = discord.Embed(
            title="↕️ Edit saved-team order",
            description=(
                "Choose a team, then use **Move up** or **Move down**. "
                "The saved-team move and swap commands provide exact control.\n\n"
                + ("\n".join(lines) if lines else "No saved teams yet.")
            ),
            color=0x5865F2,
        )
        if selected:
            embed.add_field(
                name="Selected",
                value=f"`#{selected.slot}` **{discord.utils.escape_markdown(selected.name)}**",
                inline=False,
            )
        return embed

    async def send_template_editor_from_interaction(
        self, interaction: discord.Interaction, template_id: int
    ) -> None:
        template = await self.store.get(interaction.user.id, template_id)
        if template is None:
            await interaction.response.send_message(
                "That saved team no longer exists.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=self.build_template_editor_embed(template),
            view=TemplateEditView(self, interaction.user.id, template),
            ephemeral=True,
        )

    async def send_template_editor(
        self, message: discord.Message, template: TeamTemplate
    ) -> None:
        await message.reply(
            embed=self.build_template_editor_embed(template),
            view=TemplateEditView(self, message.author.id, template),
            mention_author=False,
        )

    async def resolve_template_selector(
        self,
        message: discord.Message,
        selector: str | None,
        *,
        action_name: str,
    ) -> TeamTemplate | None:
        value = re.sub(r"\s+", " ", selector or "").strip()
        if not value:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            await message.reply(
                f"Use `{helper_alias(helper_prefix, 'ht')} {action_name} <number or name>`.",
                mention_author=False,
            )
            return None
        if value.isdigit():
            template = await self.store.get_by_slot(message.author.id, int(value))
            if template is None:
                await message.reply(
                    f"You do not have a saved team in slot **#{value}**.",
                    mention_author=False,
                )
            return template

        templates = await self.store.list_for_user(message.author.id)
        matches = [template for template in templates if template_matches_query(template, value)]
        if not matches:
            await message.reply(
                f"I could not find a saved team matching **{value}**.",
                mention_author=False,
            )
            return None
        normalized_value = normalize_template_query(value)
        exact_matches = [
            template
            for template in matches
            if normalize_template_query(template.name) == normalized_value
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(matches) == 1:
            return matches[0]
        await message.reply(
            content=(
                f"I found **{len(matches)}** saved teams matching **{value}**. "
                "Choose the exact one from the list below."
            ),
            embed=self.build_template_list_embed(matches, 0),
            view=TemplateListView(self, message.author.id, matches, page=0),
            mention_author=False,
            delete_after=300,
        )
        return None

    async def edit_template(self, message: discord.Message, selector: str | None) -> None:
        template = await self.resolve_template_selector(message, selector, action_name="edit")
        if template is None:
            return
        await self.send_template_editor(message, template)

    async def show_order_manager(self, message: discord.Message) -> None:
        templates = await self.store.list_for_user(message.author.id)
        if not templates:
            await message.reply(
                "You do not have any saved teams yet.",
                mention_author=False,
            )
            return
        await message.reply(
            embed=self.build_order_embed(templates, None),
            view=TemplateOrderView(self, message.author.id, templates),
            mention_author=False,
        )

    async def rename_template_command(self, message: discord.Message, argument: str | None) -> None:
        value = re.sub(r"\s+", " ", argument or "").strip()
        if not value:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            team_short = helper_alias(helper_prefix, "ht")
            await message.reply(
                f"Use `{team_short} rename <number> <new name>` or "
                f"`{team_short} rename <old name> to <new name>`.",
                mention_author=False,
            )
            return
        selector = ""
        new_name = ""
        first, _, rest = value.partition(" ")
        if first.isdigit() and rest.strip():
            selector = first
            new_name = rest.strip()
        else:
            split = re.split(r"\s+to\s+", value, maxsplit=1, flags=re.IGNORECASE)
            if len(split) == 2:
                selector, new_name = split[0].strip(), split[1].strip()
        if not selector or not new_name:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            team_short = helper_alias(helper_prefix, "ht")
            await message.reply(
                f"For named teams, use `{team_short} rename <old name> to <new name>`. "
                f"For slots, use `{team_short} rename 3 new name`.",
                mention_author=False,
            )
            return
        template = await self.resolve_template_selector(message, selector, action_name="rename")
        if template is None:
            return
        updated, error = await self.store.rename_template(message.author.id, template.template_id, new_name)
        if error or updated is None:
            await message.reply(f"⚠️ {error or 'The team could not be renamed.'}", mention_author=False)
            return
        await message.reply(
            f"🏷️ Renamed team **#{updated.slot}** to **{updated.name}**.",
            mention_author=False,
        )

    async def move_template_command(self, message: discord.Message, argument: str | None) -> None:
        value = re.sub(r"\s+", " ", argument or "").strip()
        match = re.match(r"^(.+?)\s+(\d{1,3})$", value)
        if not match:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            team_short = helper_alias(helper_prefix, "ht")
            await message.reply(
                f"Use `{team_short} move <number or name> <new position>`, for example "
                f"`{team_short} move 8 1`.",
                mention_author=False,
            )
            return
        selector = match.group(1).strip()
        target_slot = int(match.group(2))
        template = await self.resolve_template_selector(message, selector, action_name="move")
        if template is None:
            return
        updated, error = await self.store.move_template_to_slot(
            message.author.id,
            template.template_id,
            target_slot,
        )
        if error or updated is None:
            await message.reply(f"⚠️ {error or 'The team could not be moved.'}", mention_author=False)
            return
        await message.reply(
            f"↕️ Moved **{updated.name}** to saved-team position **#{updated.slot}**.",
            mention_author=False,
        )

    async def swap_template_command(self, message: discord.Message, argument: str | None) -> None:
        value = re.sub(r"\s+", " ", argument or "").strip()
        parts = re.split(r"\s+(?:with|and)\s+|,\s*", value, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            team_short = helper_alias(helper_prefix, "ht")
            await message.reply(
                f"Use `{team_short} swap <team A> with <team B>`, for example "
                f"`{team_short} swap 1 with 5`.",
                mention_author=False,
            )
            return
        first = await self.resolve_template_selector(message, parts[0].strip(), action_name="swap")
        if first is None:
            return
        second = await self.resolve_template_selector(message, parts[1].strip(), action_name="swap")
        if second is None:
            return
        swapped, error = await self.store.swap_templates(
            message.author.id,
            first.template_id,
            second.template_id,
        )
        if error or swapped is None:
            await message.reply(f"⚠️ {error or 'The teams could not be swapped.'}", mention_author=False)
            return
        left, right = swapped
        await message.reply(
            f"🔁 Swapped **{left.name}** and **{right.name}**. New slots: "
            f"**{left.name}** `#{left.slot}`, **{right.name}** `#{right.slot}`.",
            mention_author=False,
        )
    async def send_template_card(
        self, message: discord.Message, template: TeamTemplate
    ) -> None:
        member_lines = format_team_members(template.members)
        embed = discord.Embed(
            title=f"🐾 #{template.slot} — {template.name}",
            description=(
                f"Saved from **{template.source_title}**\n\n{member_lines}\n\n"
                "Choose **Quick replace** to overwrite the listed positions, "
                "**Exact reset** to clear all three positions first, or **Edit team** "
                "to change one animal/weapon, rename it, or move it in your saved order. "
                "Guided mode posts each command as OwO confirms the previous one."
            ),
            color=0x5865F2,
        )
        await message.reply(
            embed=embed,
            view=TemplateActionView(self, message.author.id, template),
            mention_author=False,
        )

    def build_template_list_embed(
        self,
        templates: list[TeamTemplate],
        page: int,
    ) -> discord.Embed:
        page_count = max(
            1,
            (len(templates) + TEMPLATE_PAGE_SIZE - 1) // TEMPLATE_PAGE_SIZE,
        )
        page = max(0, min(page, page_count - 1))
        start = page * TEMPLATE_PAGE_SIZE
        current = templates[start:start + TEMPLATE_PAGE_SIZE]
        numbered = "\n".join(
            f"`#{template.slot}` **{template.name}**"
            for template in current
        )
        title = "🐾 Your saved OwO teams"
        if page_count > 1:
            title += f" — Page {page + 1}/{page_count}"
        return discord.Embed(
            title=title,
            description=(
                f"You have **{len(templates)}/{MAX_TEMPLATES_PER_USER}** templates.\n\n"
                f"{numbered}\n\nChoose one below, use the arrows to change pages, "
                "or use **Edit order** to reorder saved teams."
            ),
            color=0x5865F2,
        )

    async def show_templates(self, message: discord.Message) -> None:
        templates = await self.store.list_for_user(message.author.id)
        if not templates:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            await message.reply(
                "You do not have any saved teams yet. Reply to an OwO `wtm` / "
                f"`owo team` message with `{helper_alias(helper_prefix, 'ht')} C <name>`.",
                mention_author=False,
            )
            return

        await message.reply(
            embed=self.build_template_list_embed(templates, 0),
            view=TemplateListView(self, message.author.id, templates, page=0),
            mention_author=False,
            delete_after=300,
        )

    async def show_template_by_slot(self, message: discord.Message, slot: int) -> None:
        if not 1 <= slot <= MAX_TEMPLATES_PER_USER:
            await message.reply(
                f"Team numbers are between **1** and **{MAX_TEMPLATES_PER_USER}**.",
                mention_author=False,
            )
            return
        template = await self.store.get_by_slot(message.author.id, slot)
        if template is None:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            await message.reply(
                f"You do not have a saved team in slot **#{slot}**. Use "
                f"`{helper_alias(helper_prefix, 'ht')}` to see "
                "your current list.",
                mention_author=False,
            )
            return
        await self.send_template_card(message, template)

    async def show_template_by_query(self, message: discord.Message, query: str) -> None:
        value = re.sub(r"\s+", " ", query or "").strip()
        if not value:
            await self.show_templates(message)
            return
        templates = await self.store.list_for_user(message.author.id)
        matches = [template for template in templates if template_matches_query(template, value)]
        if not matches:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            await message.reply(
                f"I could not find a saved team matching **{value}**. Use "
                f"`{helper_alias(helper_prefix, 'ht')}` to open your full list.",
                mention_author=False,
            )
            return
        normalized_value = normalize_template_query(value)
        exact_matches = [
            template
            for template in matches
            if normalize_template_query(template.name) == normalized_value
        ]
        if len(exact_matches) == 1:
            await self.send_template_card(message, exact_matches[0])
            return
        if len(matches) == 1:
            await self.send_template_card(message, matches[0])
            return

        await message.reply(
            content=(
                f"I found **{len(matches)}** saved teams matching **{value}**. "
                "Choose one below."
            ),
            embed=self.build_template_list_embed(matches, 0),
            view=TemplateListView(self, message.author.id, matches, page=0),
            mention_author=False,
            delete_after=300,
        )

    async def delete_template(self, message: discord.Message, selector: str | None) -> None:
        value = re.sub(r"\s+", " ", selector or "").strip()
        if not value:
            helper_prefix = await get_guild_helper_prefix(
                message.guild.id if message.guild else None
            )
            team_short = helper_alias(helper_prefix, "ht")
            await message.reply(
                f"Use `{team_short} D <number or name>`, for example "
                f"`{team_short}D 3` or `{team_short} D boss team`.",
                mention_author=False,
            )
            return
        deleted = await self.store.delete_by_selector(message.author.id, value)
        if deleted is None:
            await message.reply(
                f"I could not find a saved team matching **{value}**.",
                mention_author=False,
            )
            return
        await message.reply(
            f"🗑️ Deleted team **#{deleted.slot} — {deleted.name}**.",
            mention_author=False,
        )
        logger.info(
            "Deleted team template %s (slot %s) for user %s",
            deleted.template_id,
            deleted.slot,
            message.author.id,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            if message.author.bot:
                if message.author.id != OWO_BOT_ID or message.guild is None:
                    return

                # Guided setup confirmations are handled before normal team-page
                # detection. Multiple users can have independent sessions in the
                # same channel; reply/mention matching is preferred, with FIFO as a
                # fallback for OwO responses that contain neither.
                await self.handle_guided_owo_confirmation(message)

                text = extract_message_text(message)
                if parse_team_message(text) is not None:
                    try:
                        await message.add_reaction(TEAM_SAVE_EMOJI)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                return

            if message.guild is None:
                return

            if await self.handle_guided_user_command(message):
                return

            helper_prefix = await get_guild_helper_prefix(message.guild.id)
            parsed = parse_team_helper_command(message.content or "", helper_prefix)
            if parsed is None:
                return
            action, argument = parsed

            if action == "create":
                await self.save_from_reply(message, argument or "")
                return
            if action == "update":
                await self.update_from_reply(message, argument)
                return
            if action == "prefix":
                await self.set_owo_prefix_command(message, argument)
                return
            if action == "delete":
                await self.delete_template(message, argument)
                return

            if action == "edit":
                await self.edit_template(message, argument)
                return
            if action == "rename":
                await self.rename_template_command(message, argument)
                return
            if action == "move":
                await self.move_template_command(message, argument)
                return
            if action == "swap":
                await self.swap_template_command(message, argument)
                return
            if action == "order":
                await self.show_order_manager(message)
                return
            if action == "help":
                await self.send_team_help(message)
                return
            if action == "skip":
                await self.skip_guided_session(message)
                return
            if action == "cancel":
                await self.cancel_guided_session(message)
                return
            if action == "open":
                await self.show_template_by_slot(message, int(argument or 0))
                return
            if action == "open_query":
                await self.show_template_by_query(message, argument or "")
                return
            await self.show_templates(message)
        except Exception as exc:
            logger.exception("Unhandled team-template message error: %s", exc)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.emoji) != TEAM_SAVE_EMOJI:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        if payload.guild_id is None:
            return

        cooldown_key = (payload.user_id, payload.message_id)
        now = time.monotonic()
        if now - self.reaction_instruction_cooldowns.get(cooldown_key, 0) < 30:
            return
        self.reaction_instruction_cooldowns[cooldown_key] = now

        raw = await fetch_raw_message(self.bot, payload.channel_id, payload.message_id)
        if raw is None:
            return
        if int((raw.get("author") or {}).get("id", 0) or 0) != OWO_BOT_ID:
            return
        if parse_team_message(extract_all_text(raw)) is None:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return
        if not isinstance(channel, discord.abc.Messageable):
            return

        user = self.bot.get_user(payload.user_id)
        mention = user.mention if user else f"<@{payload.user_id}>"
        helper_prefix = await get_guild_helper_prefix(payload.guild_id)
        team_short = helper_alias(helper_prefix, "ht")
        try:
            await channel.send(
                f"{mention} Reply to this OwO team message with "
                f"`{team_short} C <name>` (or "
                f"`{helper_command(helper_prefix, 'team create <name>')}`) to save its "
                f"animals and exact weapon IDs. Use `{team_short} help` for the full guide.",
                reference=discord.MessageReference(
                    message_id=payload.message_id,
                    channel_id=payload.channel_id,
                    guild_id=payload.guild_id,
                ),
                mention_author=False,
                delete_after=45,
                allowed_mentions=discord.AllowedMentions(users=True, replied_user=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeamTemplates(bot))
