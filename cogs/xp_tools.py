"""Pet-XP explanations, forecasts, and opt-in Knowledge-switch reminders.

The calculator intentionally keeps battle XP separate from hunting and fixed
daily XP.  Reminder tracking is scoped to an enabled member's own battle
command and the matching OwO response; unrelated grinding is ignored.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .helper_prefix import (
    get_guild_helper_prefix,
    helper_alias,
    helper_command,
    matches_helper_command,
    parse_helper_command_argument,
)
from .message_utils import safe_reply
from .owo_prefix import get_guild_owo_prefix, is_owo_prefixed_command
from .team_templates import (
    OWO_BOT_ID,
    TeamMember,
    extract_all_text,
    extract_message_text,
    owo_weapon_command,
    parse_team_message_detailed,
)
from .team_guides import animal_emoji_key
from .ui_emojis import ui_emoji_button, ui_emoji_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "xp_reminders.db"
XP_SOURCE_URL = "https://owo.bwep.net/exp/"
XP_CREDIT_MARKDOWN = f"Inspired by [Heather's Pet EXP guide]({XP_SOURCE_URL})."
XP_CREDIT_FOOTER = "Inspired by Heather's Pet EXP guide • owo.bwep.net/exp/"
STREAK_DATA_URL = "https://docs.google.com/spreadsheets/d/1qXl5N8VYT1C0yUyiYQ1QzdlNcPMo9iEFu8HMJ4zmuKc/edit"
XP_DATA_URL = "https://docs.google.com/spreadsheets/d/1E_YSDmmX-WVfoX29dkpPcgCwY_9K7g_uM3pVZN2E_74/edit"
REMINDER_INTERVALS = (10, 50, 100, 500, 1000)
BATTLE_COMMAND_SUFFIXES = {"b", "battle"}
PENDING_BATTLE_SECONDS = 45
TEAM_TOOLS_SECONDS = 15 * 60
FORECAST_PAGE_LINES = 12
MAX_FORECAST_LINES = 99
MAX_FORECAST_BATTLES = 3_000_000
MAX_LEVEL = 250
MAX_VALIDATED_STREAK = 122_500
KNOWLEDGE_SCENARIOS: tuple[tuple[str, int | None], ...] = (
    ("Always equipped", None),
    ("Every 10th battle", 10),
    ("Every 50th battle", 50),
    ("Every 100th battle", 100),
    ("Every 500th battle", 500),
    ("Every 1,000th battle", 1000),
)

XP_HELP_ALIASES = {"hxp", "h xp", "hexp", "h exp"}
REMINDER_ALIASES = {
    "hreminder",
    "h reminder",
    "hreminders",
    "h reminders",
    "hxp reminder",
    "hxp reminders",
}


@dataclass(frozen=True)
class PetXPState:
    position: int
    animal: str
    level: int
    current_xp: int
    next_xp: int


@dataclass(frozen=True)
class TeamXPSnapshot:
    source_title: str
    members: tuple[TeamMember, ...]
    pets: tuple[PetXPState, ...]
    streak: int


@dataclass(frozen=True)
class ForecastSettings:
    battles_per_day: int = 0
    fixed_xp_per_day: int = 0
    average_xp_per_hunt: float = 0.0
    tie_rate_percent: float = 0.0
    loss_rate_percent: float = 0.0
    maximum_streak: int = 0
    knowledge_quality: float = 0.15
    max_lines: int = 20


@dataclass(frozen=True)
class ForecastPetProgress:
    position: int
    level: int
    current_xp: float
    next_xp: int
    leveled: bool = False


@dataclass(frozen=True)
class ForecastLine:
    pets: tuple[ForecastPetProgress, ...]
    battles: int
    streak: int
    days: int


@dataclass(frozen=True)
class BattleResult:
    outcome: str
    streak: int
    xp: int | None = None


@dataclass(frozen=True)
class PendingBattle:
    guild_id: int
    channel_id: int
    user_id: int
    command_message_id: int
    identity_tokens: tuple[str, ...]
    created_at: float


@dataclass(frozen=True)
class XPReminderProfile:
    guild_id: int
    user_id: int
    enabled: bool
    interval: int
    mention: bool
    normal: tuple[TeamMember, ...]
    knowledge: tuple[TeamMember, ...]
    awaiting_restore: bool
    trigger_streak: int
    last_streak: int
    last_battle_message_id: int

    @property
    def ready(self) -> bool:
        return bool(self.normal and self.knowledge and changed_positions(self.normal, self.knowledge))


def xp_for_next_level(level: int) -> int:
    if level < 1:
        raise ValueError("Level must be at least 1")
    return 1000 + int(level) ** 4


def total_level_xp(start_level: int, target_level: int, current_xp: int = 0) -> int:
    if start_level < 1 or target_level <= start_level:
        raise ValueError("Target level must be greater than the starting level")
    current_requirement = xp_for_next_level(start_level)
    if current_xp < 0 or current_xp >= current_requirement:
        raise ValueError(
            f"Current XP must be between 0 and {current_requirement - 1:,} for level {start_level}"
        )
    return sum(xp_for_next_level(level) for level in range(start_level, target_level)) - current_xp


def catchup_multiplier(level: int, highest_level: int) -> float:
    return 2.0 + 0.1 * (highest_level - level) if level < highest_level else 1.0


def knowledge_effect_text(base_multiplier: float, knowledge_quality: float) -> str:
    """Explain Knowledge as an additive multiplier and its effective increase."""
    selected = base_multiplier + knowledge_quality
    relative = 100 * knowledge_quality / base_multiplier if base_multiplier else 0.0
    return f"{base_multiplier:.2f}× → {selected:.2f}× (+{relative:.1f}%)"


def raw_battle_xp_needed(
    start_level: int,
    target_level: int,
    current_xp: int,
    highest_level: int,
    knowledge_bonus: float = 0.0,
) -> float:
    """Return pre-multiplier battle XP needed with dynamic catch-up per level."""
    if not 0 <= knowledge_bonus <= 0.15:
        raise ValueError("Knowledge must be between 0 and 0.15")
    total_level_xp(start_level, target_level, current_xp)
    raw_needed = 0.0
    for level in range(start_level, target_level):
        needed = xp_for_next_level(level)
        if level == start_level:
            needed -= current_xp
        multiplier = catchup_multiplier(level, highest_level) + knowledge_bonus
        raw_needed += needed / multiplier
    return raw_needed


def milestone_bonus_value(streak: int) -> float:
    if streak <= 0:
        return 0.0
    if streak % 1000 == 0:
        value = 250 * math.sqrt(streak) + 12_500
    elif streak % 500 == 0:
        value = 100 * math.sqrt(streak) + 5_000
    elif streak % 100 == 0:
        value = 50 * math.sqrt(streak) + 2_500
    elif streak % 50 == 0:
        value = 30 * math.sqrt(streak) + 1_500
    elif streak % 10 == 0:
        value = 10 * math.sqrt(streak) + 500
    else:
        return 0.0
    return min(100_000.0, value)


def milestone_bonus(streak: int) -> int:
    """Return the nearest displayed whole XP for one milestone."""
    return int(round(milestone_bonus_value(streak)))


def winning_streak_total(streak: int) -> int:
    if streak <= 0:
        return 0
    # OwO's reference spreadsheet keeps milestone precision while summing and
    # rounds the completed run once. Rounding every milestone loses several XP.
    total = 200 * streak + sum(
        milestone_bonus_value(value) for value in range(10, streak + 1, 10)
    )
    return int(round(total))


def average_winning_streak_xp(streak: int) -> float:
    return winning_streak_total(streak) / streak if streak > 0 else 200.0


def current_streak_average_xp(streak: int) -> float:
    """Return the public sustained-streak XP estimate shown by team forecasters."""
    if streak <= 0:
        return 200.0
    if streak > MAX_VALIDATED_STREAK:
        raise ValueError(
            f"The validated streak model ends at {MAX_VALIDATED_STREAK:,}"
        )
    return 292.5 + 1.85 * math.sqrt(streak)


def estimated_battles_needed(
    start_level: int,
    target_level: int,
    current_xp: int,
    highest_level: int,
    average_battle_xp: float,
    *,
    knowledge_bonus: float = 0.0,
    fixed_xp_per_battle: float = 0.0,
) -> int:
    """Forecast battles while recalculating catch-up at each level.

    Battle XP receives catch-up and Knowledge. Fixed XP is spread across the
    configured battles/day and remains outside those multipliers, matching the
    public forecaster's treatment of fixed daily XP.
    """
    if average_battle_xp <= 0 or fixed_xp_per_battle < 0:
        raise ValueError("XP rates cannot be negative or zero")
    if not 0 <= knowledge_bonus <= 0.15:
        raise ValueError("Knowledge must be between 0 and 0.15")
    total_level_xp(start_level, target_level, current_xp)
    battles = 0.0
    for level in range(start_level, target_level):
        needed = xp_for_next_level(level)
        if level == start_level:
            needed -= current_xp
        per_battle = (
            average_battle_xp
            * (catchup_multiplier(level, highest_level) + knowledge_bonus)
            + fixed_xp_per_battle
        )
        battles += needed / per_battle
    return math.ceil(battles)


def validate_forecast_settings(settings: ForecastSettings) -> None:
    if not 0 <= settings.battles_per_day <= 5_760:
        raise ValueError("Battles per day must be between 0 and 5,760")
    if not 0 <= settings.fixed_xp_per_day <= 2_000_000_000:
        raise ValueError("Fixed XP per day must be between 0 and 2,000,000,000")
    if not 0 <= settings.average_xp_per_hunt <= 1_000_000:
        raise ValueError("Average XP per hunt must be between 0 and 1,000,000")
    if not 0 <= settings.tie_rate_percent < 100:
        raise ValueError("Tie rate must be at least 0 and below 100 percent")
    if not 0 <= settings.loss_rate_percent < 100:
        raise ValueError("Loss rate must be at least 0 and below 100 percent")
    if settings.tie_rate_percent + settings.loss_rate_percent >= 100:
        raise ValueError("Tie rate and loss rate together must stay below 100 percent")
    if settings.fixed_xp_per_day and settings.battles_per_day == 0:
        raise ValueError("Set battles per day before adding fixed XP per day")
    if not 0 <= settings.maximum_streak <= MAX_VALIDATED_STREAK:
        raise ValueError(
            f"Maximum streak must be between 0 and {MAX_VALIDATED_STREAK:,}"
        )
    if not 0 <= settings.knowledge_quality <= 0.15:
        raise ValueError("Knowledge quality must be between 0 and 0.15")
    if not 1 <= settings.max_lines <= MAX_FORECAST_LINES:
        raise ValueError(f"Maximum lines must be between 1 and {MAX_FORECAST_LINES}")


def simulate_team_forecast(
    snapshot: TeamXPSnapshot,
    settings: ForecastSettings,
    *,
    use_knowledge: bool,
    knowledge_interval: int | None = None,
    line_limit: int | None = None,
    stop_after_next_levels: bool = False,
) -> tuple[tuple[ForecastLine, ...], dict[int, int]]:
    """Run a deterministic team forecast using the documented approximation.

    Tie rate reduces the streak-derived battle estimate, maximum streak wraps
    the projected streak, and one hunt is paired with each configured battle.
    Hunting and fixed daily XP remain outside catch-up and Knowledge.
    """
    validate_forecast_settings(settings)
    if knowledge_interval is not None and knowledge_interval not in REMINDER_INTERVALS:
        raise ValueError("Unsupported Knowledge interval")
    if not snapshot.pets:
        return (), {}

    limit = min(MAX_FORECAST_LINES, line_limit or settings.max_lines)
    tie_rate = settings.tie_rate_percent / 100.0
    loss_rate = settings.loss_rate_percent / 100.0
    fixed_per_battle = (
        settings.fixed_xp_per_day / settings.battles_per_day
        if settings.battles_per_day
        else 0.0
    )
    streak = max(0, snapshot.streak)
    if settings.maximum_streak and streak > settings.maximum_streak:
        streak %= settings.maximum_streak
    tie_accumulator = 0.0
    loss_accumulator = 0.0
    initial_levels = {pet.position: pet.level for pet in snapshot.pets}
    next_level_battles: dict[int, int] = {}
    levels = [pet.level for pet in snapshot.pets]
    progress = [float(pet.current_xp) for pet in snapshot.pets]

    def make_line(battles: int, leveled_positions: set[int]) -> ForecastLine:
        pets = tuple(
            ForecastPetProgress(
                position=pet.position,
                level=levels[index],
                current_xp=progress[index],
                next_xp=xp_for_next_level(levels[index]),
                leveled=pet.position in leveled_positions,
            )
            for index, pet in enumerate(snapshot.pets)
        )
        return ForecastLine(
            pets=pets,
            battles=battles,
            streak=streak,
            days=battles // settings.battles_per_day if settings.battles_per_day else 0,
        )

    lines: list[ForecastLine] = [make_line(0, set())]
    for battles in range(1, MAX_FORECAST_BATTLES + 1):
        tie_accumulator += tie_rate
        loss_accumulator += loss_rate
        lost = loss_accumulator >= 1.0
        tied = not lost and tie_accumulator >= 1.0
        if lost:
            loss_accumulator -= 1.0
            streak = 0
            raw_battle_xp = 50.0
        elif tied:
            tie_accumulator -= 1.0
            raw_battle_xp = 100.0
        else:
            streak += 1
            if settings.maximum_streak and streak > settings.maximum_streak:
                streak = 1
            raw_battle_xp = current_streak_average_xp(
                min(streak, MAX_VALIDATED_STREAK)
            )
        knowledge_active = use_knowledge and (
            knowledge_interval is None
            or (not tied and not lost and streak > 0 and streak % knowledge_interval == 0)
        )
        highest_level = max(levels)
        leveled_positions: set[int] = set()
        for index, pet in enumerate(snapshot.pets):
            if levels[index] >= MAX_LEVEL:
                continue
            bonus = settings.knowledge_quality if knowledge_active else 0.0
            progress[index] += (
                raw_battle_xp * (catchup_multiplier(levels[index], highest_level) + bonus)
                + fixed_per_battle
                + settings.average_xp_per_hunt
            )
            while levels[index] < MAX_LEVEL and progress[index] >= xp_for_next_level(levels[index]):
                progress[index] -= xp_for_next_level(levels[index])
                levels[index] += 1
                leveled_positions.add(pet.position)
                if (
                    pet.position not in next_level_battles
                    and levels[index] > initial_levels[pet.position]
                ):
                    next_level_battles[pet.position] = battles

        if leveled_positions and len(lines) < limit:
            lines.append(make_line(battles, leveled_positions))
        if stop_after_next_levels and len(next_level_battles) == len(snapshot.pets):
            break
        if len(lines) >= limit and not stop_after_next_levels:
            break

    return tuple(lines), next_level_battles


def next_level_forecasts(
    snapshot: TeamXPSnapshot,
    settings: ForecastSettings,
) -> tuple[dict[int, int], dict[int, int]]:
    _, normal = simulate_team_forecast(
        snapshot,
        settings,
        use_knowledge=False,
        line_limit=1,
        stop_after_next_levels=True,
    )
    _, always = simulate_team_forecast(
        snapshot,
        settings,
        use_knowledge=True,
        knowledge_interval=None,
        line_limit=1,
        stop_after_next_levels=True,
    )
    return normal, always


def switch_benefit(
    interval: int,
    *,
    start_streak: int = 0,
    battles: int = 1000,
    knowledge_bonus: float = 0.15,
) -> tuple[int, float]:
    """Return qualifying battles and extra XP per pet across an all-win window."""
    if interval not in REMINDER_INTERVALS:
        raise ValueError("Unsupported reminder interval")
    qualifying = 0
    extra = 0.0
    for streak in range(start_streak + 1, start_streak + battles + 1):
        if streak % interval:
            continue
        qualifying += 1
        extra += (200 + milestone_bonus_value(streak)) * knowledge_bonus
    return qualifying, extra


def next_switch_streak(streak: int, interval: int) -> int:
    return ((max(0, streak) // interval) + 1) * interval


def should_prompt_switch(streak: int, interval: int) -> bool:
    return (max(0, streak) + 1) % interval == 0


def parse_number(value: str) -> int:
    return int(re.sub(r"[^0-9]", "", value or "") or 0)


def parse_battle_result(text: str) -> BattleResult | None:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    streak_match = re.search(r"\bStreak\s*:\s*([\d,]+)", normalized, re.IGNORECASE)
    if streak_match is None:
        return None
    lowered = normalized.casefold()
    if re.search(r"\byou\s+(?:win|won)\b", lowered):
        outcome = "win"
    elif re.search(r"\byou\s+(?:lose|lost)\b", lowered):
        outcome = "loss"
    elif re.search(r"\b(?:tie|tied|draw)\b", lowered):
        outcome = "tie"
    else:
        return None
    xp_match = re.search(r"\+\s*([\d,]+)\s*xp\b", normalized, re.IGNORECASE)
    return BattleResult(
        outcome=outcome,
        streak=parse_number(streak_match.group(1)),
        xp=parse_number(xp_match.group(1)) if xp_match else None,
    )


def parse_team_xp_snapshot(text: str) -> TeamXPSnapshot | None:
    parsed = parse_team_message_detailed(text)
    if parsed is None:
        return None
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").replace("`", "")
    section_re = re.compile(r"(?m)^[ \t]*\[([1-3])\](?:[ \t]+([^\n]*?))?[ \t]*$")
    matches = list(section_re.finditer(cleaned))
    member_by_position = {member.position: member for member in parsed.members}
    pets: list[PetXPState] = []
    for index, match in enumerate(matches):
        position = int(match.group(1))
        member = member_by_position.get(position)
        if member is None:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        section = f"{match.group(2) or ''}\n{cleaned[match.end():end]}"
        level_match = re.search(r"\bLvl\.?\s*(\d{1,3})\b", section, re.IGNORECASE)
        xp_match = re.search(r"\[\s*([\d,]+)\s*/\s*([\d,]+)\s*\]", section)
        if level_match is None:
            continue
        level = int(level_match.group(1))
        current_xp = parse_number(xp_match.group(1)) if xp_match else 0
        next_xp = parse_number(xp_match.group(2)) if xp_match else xp_for_next_level(level)
        pets.append(PetXPState(position, member.animal, level, current_xp, next_xp))
    streak_match = re.search(r"Current\s+Streak\s*:\s*([\d,]+)", cleaned, re.IGNORECASE)
    return TeamXPSnapshot(
        source_title=parsed.source_title,
        members=parsed.members,
        pets=tuple(sorted(pets, key=lambda pet: pet.position)),
        streak=parse_number(streak_match.group(1)) if streak_match else 0,
    )


def serialize_members(members: tuple[TeamMember, ...]) -> str:
    return json.dumps(
        [
            {"position": member.position, "animal": member.animal, "weapon_id": member.weapon_id}
            for member in members
            if member.weapon_id
        ],
        separators=(",", ":"),
    )


def deserialize_members(value: str | None) -> tuple[TeamMember, ...]:
    try:
        raw = json.loads(value or "[]")
        members = tuple(
            TeamMember(int(item["position"]), str(item["animal"]), str(item["weapon_id"]).upper())
            for item in raw
            if str(item.get("weapon_id") or "").strip()
        )
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return ()
    return tuple(sorted(members, key=lambda member: member.position))


def changed_positions(
    normal: tuple[TeamMember, ...], knowledge: tuple[TeamMember, ...]
) -> tuple[int, ...]:
    normal_by_position = {member.position: member for member in normal}
    knowledge_by_position = {member.position: member for member in knowledge}
    changed: list[int] = []
    for position in sorted(set(normal_by_position) & set(knowledge_by_position)):
        before = normal_by_position[position]
        after = knowledge_by_position[position]
        if before.animal.casefold() != after.animal.casefold():
            continue
        if before.weapon_id.upper() != after.weapon_id.upper():
            changed.append(position)
    return tuple(changed)


def knowledge_preset_from_ids(
    normal: tuple[TeamMember, ...], entered: dict[int, str]
) -> tuple[TeamMember, ...]:
    normalized = {position: value.strip().upper() for position, value in entered.items()}
    for weapon_id in normalized.values():
        if weapon_id and re.fullmatch(r"[A-Z0-9]{6}", weapon_id) is None:
            raise ValueError(
                "Every entered weapon ID must contain exactly six letters or numbers"
            )
    return tuple(
        TeamMember(
            member.position,
            member.animal,
            normalized.get(member.position) or member.weapon_id,
        )
        for member in normal
    )


def presets_compatible(
    normal: tuple[TeamMember, ...], knowledge: tuple[TeamMember, ...]
) -> bool:
    normal_animals = {member.position: member.animal.casefold() for member in normal}
    knowledge_animals = {member.position: member.animal.casefold() for member in knowledge}
    return bool(normal_animals) and normal_animals == knowledge_animals


def weapon_switch_commands(
    normal: tuple[TeamMember, ...],
    target: tuple[TeamMember, ...],
    owo_prefix: str,
) -> tuple[str, ...]:
    normal_by_position = {member.position: member for member in normal}
    commands: list[str] = []
    for position in changed_positions(normal, target):
        target_member = next(member for member in target if member.position == position)
        before = normal_by_position[position]
        if before.animal.casefold() != target_member.animal.casefold():
            continue
        commands.append(
            owo_weapon_command(
                owo_prefix,
                target_member.weapon_id,
                target_member.animal,
                use_alias=bool(len(commands) % 2),
            )
        )
    return tuple(commands)


class XPReminderStore:
    def __init__(self, path: Path = DATABASE_FILE) -> None:
        self.path = path
        self.lock = asyncio.Lock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize_sync(self) -> None:
        connection = self.connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS xp_reminder_profiles (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    interval INTEGER NOT NULL DEFAULT 100,
                    mention INTEGER NOT NULL DEFAULT 1,
                    normal_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_json TEXT NOT NULL DEFAULT '[]',
                    awaiting_restore INTEGER NOT NULL DEFAULT 0,
                    trigger_streak INTEGER NOT NULL DEFAULT 0,
                    last_streak INTEGER NOT NULL DEFAULT 0,
                    last_battle_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS xp_forecast_settings (
                    user_id INTEGER NOT NULL PRIMARY KEY,
                    battles_per_day INTEGER NOT NULL DEFAULT 0,
                    fixed_xp_per_day INTEGER NOT NULL DEFAULT 0,
                    average_xp_per_hunt REAL NOT NULL DEFAULT 0,
                    tie_rate_percent REAL NOT NULL DEFAULT 0,
                    loss_rate_percent REAL NOT NULL DEFAULT 0,
                    maximum_streak INTEGER NOT NULL DEFAULT 0,
                    knowledge_quality REAL NOT NULL DEFAULT 0.15,
                    max_lines INTEGER NOT NULL DEFAULT 20,
                    updated_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.initialize_sync)

    @staticmethod
    def from_row(row: sqlite3.Row | None, guild_id: int, user_id: int) -> XPReminderProfile:
        if row is None:
            return XPReminderProfile(guild_id, user_id, False, 100, True, (), (), False, 0, 0, 0)
        return XPReminderProfile(
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            enabled=bool(row["enabled"]),
            interval=int(row["interval"]),
            mention=bool(row["mention"]),
            normal=deserialize_members(row["normal_json"]),
            knowledge=deserialize_members(row["knowledge_json"]),
            awaiting_restore=bool(row["awaiting_restore"]),
            trigger_streak=int(row["trigger_streak"]),
            last_streak=int(row["last_streak"]),
            last_battle_message_id=int(row["last_battle_message_id"]),
        )

    def get_sync(self, guild_id: int, user_id: int) -> XPReminderProfile:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM xp_reminder_profiles WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        finally:
            connection.close()
        return self.from_row(row, guild_id, user_id)

    async def get(self, guild_id: int, user_id: int) -> XPReminderProfile:
        return await asyncio.to_thread(self.get_sync, guild_id, user_id)

    def upsert_sync(
        self,
        guild_id: int,
        user_id: int,
        *,
        enabled: bool | None = None,
        interval: int | None = None,
        mention: bool | None = None,
        normal: tuple[TeamMember, ...] | None = None,
        knowledge: tuple[TeamMember, ...] | None = None,
        awaiting_restore: bool | None = None,
        trigger_streak: int | None = None,
        last_streak: int | None = None,
        last_battle_message_id: int | None = None,
    ) -> XPReminderProfile:
        current = self.get_sync(guild_id, user_id)
        values = {
            "enabled": current.enabled if enabled is None else enabled,
            "interval": current.interval if interval is None else interval,
            "mention": current.mention if mention is None else mention,
            "normal": current.normal if normal is None else normal,
            "knowledge": current.knowledge if knowledge is None else knowledge,
            "awaiting_restore": current.awaiting_restore if awaiting_restore is None else awaiting_restore,
            "trigger_streak": current.trigger_streak if trigger_streak is None else trigger_streak,
            "last_streak": current.last_streak if last_streak is None else last_streak,
            "last_battle_message_id": (
                current.last_battle_message_id
                if last_battle_message_id is None
                else last_battle_message_id
            ),
        }
        connection = self.connect()
        try:
            connection.execute(
                """
                INSERT INTO xp_reminder_profiles (
                    guild_id, user_id, enabled, interval, mention, normal_json,
                    knowledge_json, awaiting_restore, trigger_streak, last_streak,
                    last_battle_message_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    interval = excluded.interval,
                    mention = excluded.mention,
                    normal_json = excluded.normal_json,
                    knowledge_json = excluded.knowledge_json,
                    awaiting_restore = excluded.awaiting_restore,
                    trigger_streak = excluded.trigger_streak,
                    last_streak = excluded.last_streak,
                    last_battle_message_id = excluded.last_battle_message_id,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    user_id,
                    int(values["enabled"]),
                    int(values["interval"]),
                    int(values["mention"]),
                    serialize_members(values["normal"]),
                    serialize_members(values["knowledge"]),
                    int(values["awaiting_restore"]),
                    int(values["trigger_streak"]),
                    int(values["last_streak"]),
                    int(values["last_battle_message_id"]),
                    int(time.time()),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_sync(guild_id, user_id)

    async def upsert(self, guild_id: int, user_id: int, **kwargs: Any) -> XPReminderProfile:
        async with self.lock:
            return await asyncio.to_thread(self.upsert_sync, guild_id, user_id, **kwargs)

    @staticmethod
    def forecast_settings_from_row(row: sqlite3.Row | None) -> ForecastSettings:
        if row is None:
            return ForecastSettings()
        return ForecastSettings(
            battles_per_day=int(row["battles_per_day"]),
            fixed_xp_per_day=int(row["fixed_xp_per_day"]),
            average_xp_per_hunt=float(row["average_xp_per_hunt"]),
            tie_rate_percent=float(row["tie_rate_percent"]),
            loss_rate_percent=float(row["loss_rate_percent"]),
            maximum_streak=int(row["maximum_streak"]),
            knowledge_quality=float(row["knowledge_quality"]),
            max_lines=int(row["max_lines"]),
        )

    def get_forecast_settings_sync(self, user_id: int) -> ForecastSettings:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM xp_forecast_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            connection.close()
        return self.forecast_settings_from_row(row)

    async def get_forecast_settings(self, user_id: int) -> ForecastSettings:
        return await asyncio.to_thread(self.get_forecast_settings_sync, user_id)

    def upsert_forecast_settings_sync(
        self, user_id: int, settings: ForecastSettings
    ) -> ForecastSettings:
        validate_forecast_settings(settings)
        connection = self.connect()
        try:
            connection.execute(
                """
                INSERT INTO xp_forecast_settings (
                    user_id, battles_per_day, fixed_xp_per_day,
                    average_xp_per_hunt, tie_rate_percent, loss_rate_percent,
                    maximum_streak, knowledge_quality, max_lines, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    battles_per_day = excluded.battles_per_day,
                    fixed_xp_per_day = excluded.fixed_xp_per_day,
                    average_xp_per_hunt = excluded.average_xp_per_hunt,
                    tie_rate_percent = excluded.tie_rate_percent,
                    loss_rate_percent = excluded.loss_rate_percent,
                    maximum_streak = excluded.maximum_streak,
                    knowledge_quality = excluded.knowledge_quality,
                    max_lines = excluded.max_lines,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    settings.battles_per_day,
                    settings.fixed_xp_per_day,
                    settings.average_xp_per_hunt,
                    settings.tie_rate_percent,
                    settings.loss_rate_percent,
                    settings.maximum_streak,
                    settings.knowledge_quality,
                    settings.max_lines,
                    int(time.time()),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_forecast_settings_sync(user_id)

    async def upsert_forecast_settings(
        self, user_id: int, settings: ForecastSettings
    ) -> ForecastSettings:
        async with self.lock:
            return await asyncio.to_thread(
                self.upsert_forecast_settings_sync, user_id, settings
            )


def identity_tokens(user: discord.abc.User) -> tuple[str, ...]:
    values = {
        str(getattr(user, "id", "")),
        str(getattr(user, "name", "")),
        str(getattr(user, "display_name", "")),
        str(getattr(user, "global_name", "")),
    }
    return tuple(
        sorted(
            {
                re.sub(r"[^a-z0-9_]", "", value.casefold())
                for value in values
                if re.sub(r"[^a-z0-9_]", "", value.casefold())
            },
            key=len,
            reverse=True,
        )
    )


class ReminderIntervalSelect(discord.ui.Select):
    def __init__(self, panel: "XPReminderView", current: int) -> None:
        self.panel = panel
        options = [
            discord.SelectOption(
                label=f"Every {value:,} wins",
                value=str(value),
                description=f"Prompt before streaks divisible by {value:,}",
                default=value == current,
            )
            for value in REMINDER_INTERVALS
        ]
        super().__init__(placeholder="Choose a milestone interval", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.panel.cog.store.upsert(
            self.panel.guild_id,
            self.panel.owner_id,
            interval=int(self.values[0]),
        )
        await self.panel.refresh(interaction)


class XPReminderView(discord.ui.View):
    def __init__(self, cog: "XPTools", guild_id: int, owner_id: int, profile: XPReminderProfile):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.add_item(ReminderIntervalSelect(self, profile.interval))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("These XP settings belong to another member.", ephemeral=True)
        return False

    async def refresh(self, interaction: discord.Interaction) -> None:
        profile = await self.cog.store.get(self.guild_id, self.owner_id)
        await interaction.response.edit_message(
            embed=self.cog.build_reminder_embed(profile),
            view=XPReminderView(self.cog, self.guild_id, self.owner_id, profile),
        )

    @discord.ui.button(label="Enable", emoji="🔔", style=discord.ButtonStyle.success, row=1)
    async def enable(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.cog.store.upsert(self.guild_id, self.owner_id, enabled=True)
        await self.refresh(interaction)

    @discord.ui.button(label="Disable", emoji="🔕", style=discord.ButtonStyle.danger, row=1)
    async def disable(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.cog.store.upsert(
            self.guild_id,
            self.owner_id,
            enabled=False,
            awaiting_restore=False,
            trigger_streak=0,
        )
        await self.refresh(interaction)

    @discord.ui.button(label="Toggle mention", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_mention(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        profile = await self.cog.store.get(self.guild_id, self.owner_id)
        await self.cog.store.upsert(self.guild_id, self.owner_id, mention=not profile.mention)
        await self.refresh(interaction)


class ForecastActivityModal(discord.ui.Modal, title="Forecast settings"):
    activity = discord.ui.TextInput(
        label="Battles/day, fixed XP/day, XP/hunt",
        placeholder="Example: 2000, 200000, 21",
        required=False,
        max_length=40,
    )
    rates = discord.ui.TextInput(
        label="Tie %, loss %",
        placeholder="Example: 0.82, 0.10 • Neon Global can estimate",
        required=False,
        max_length=40,
    )
    maximum_streak = discord.ui.TextInput(
        label="Maximum streak",
        placeholder="0 means no forced reset",
        required=False,
        max_length=7,
    )
    knowledge_quality = discord.ui.TextInput(
        label="Knowledge quality",
        placeholder="0 to 0.15",
        required=False,
        max_length=4,
    )
    max_lines = discord.ui.TextInput(
        label="Forecast lines",
        placeholder="1 to 99 • default 20",
        required=False,
        max_length=2,
    )

    def __init__(self, panel: "TeamLevelForecastView") -> None:
        super().__init__()
        self.panel = panel
        settings = panel.settings
        self.activity.default = (
            f"{settings.battles_per_day}, {settings.fixed_xp_per_day}, "
            f"{settings.average_xp_per_hunt:g}"
        )
        self.rates.default = (
            f"{settings.tie_rate_percent:g}, {settings.loss_rate_percent:g}"
        )
        self.maximum_streak.default = str(settings.maximum_streak)
        self.knowledge_quality.default = f"{settings.knowledge_quality:g}"
        self.max_lines.default = str(settings.max_lines)

    @staticmethod
    def _values(value: str, count: int, label: str) -> tuple[str, ...] | None:
        if not value.strip():
            return None
        parts = tuple(part.strip() for part in re.split(r"[,;]", value))
        if len(parts) != count or not all(parts):
            raise ValueError(
                f"Enter {label} as {count} values separated by commas"
            )
        return parts

    @staticmethod
    def _optional_int(value: str, current: int) -> int:
        return int(value.replace(",", "")) if value.strip() else current

    @staticmethod
    def _optional_float(value: str, current: float) -> float:
        return float(value) if value.strip() else current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            current = self.panel.settings
            activity = self._values(
                str(self.activity), 3, "activity settings"
            )
            rates = self._values(str(self.rates), 2, "tie and loss rates")
            settings = replace(
                current,
                battles_per_day=(
                    int(activity[0]) if activity else current.battles_per_day
                ),
                fixed_xp_per_day=(
                    int(activity[1]) if activity else current.fixed_xp_per_day
                ),
                average_xp_per_hunt=(
                    float(activity[2]) if activity else current.average_xp_per_hunt
                ),
                tie_rate_percent=(
                    float(rates[0]) if rates else current.tie_rate_percent
                ),
                loss_rate_percent=(
                    float(rates[1]) if rates else current.loss_rate_percent
                ),
                maximum_streak=self._optional_int(
                    str(self.maximum_streak), current.maximum_streak
                ),
                knowledge_quality=self._optional_float(
                    str(self.knowledge_quality), current.knowledge_quality
                ),
                max_lines=self._optional_int(str(self.max_lines), current.max_lines),
            )
            validate_forecast_settings(settings)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        self.panel.settings = await self.panel.cog.store.upsert_forecast_settings(
            self.panel.owner_id, settings
        )
        self.panel.page = 0
        await interaction.response.defer()
        await interaction.edit_original_response(embed=self.panel.build_embed(), view=self.panel)
        if self.panel.team_tools_message is not None:
            try:
                await self.panel.team_tools_message.edit(
                    embed=self.panel.cog.build_team_tools_embed(
                        self.panel.snapshot, self.panel.settings
                    ),
                    view=TeamXPToolsView(
                        self.panel.cog,
                        self.panel.owner_id,
                        self.panel.guild_id,
                        self.panel.snapshot,
                        self.panel.helper_prefix,
                        self.panel.settings,
                    ),
                )
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                logger.debug("Could not refresh the source Team XP panel", exc_info=True)


class KnowledgePresetModal(discord.ui.Modal, title="Knowledge weapon IDs"):
    position_1 = discord.ui.TextInput(
        label="Position 1 weapon ID", required=False, max_length=6
    )
    position_2 = discord.ui.TextInput(
        label="Position 2 weapon ID", required=False, max_length=6
    )
    position_3 = discord.ui.TextInput(
        label="Position 3 weapon ID", required=False, max_length=6
    )

    def __init__(self, cog: "XPTools", guild_id: int, owner_id: int, profile: XPReminderProfile) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.profile = profile
        existing = {member.position: member.weapon_id for member in profile.knowledge}
        normal_members = {member.position: member for member in profile.normal}
        for position, item in (
            (1, self.position_1),
            (2, self.position_2),
            (3, self.position_3),
        ):
            current = normal_members.get(position)
            current_id = current.weapon_id if current else "none"
            item.placeholder = (
                f"Current {current_id} • enter Knowledge ID for {current.animal}"
                if current
                else "No current weapon saved"
            )
            if existing.get(position) and existing[position] != current_id:
                item.default = existing[position]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        entered = {
            1: str(self.position_1).strip().upper(),
            2: str(self.position_2).strip().upper(),
            3: str(self.position_3).strip().upper(),
        }
        try:
            knowledge = knowledge_preset_from_ids(self.profile.normal, entered)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}.", ephemeral=True)
            return
        if not changed_positions(self.profile.normal, knowledge):
            await interaction.response.send_message(
                "❌ Enter at least one Knowledge weapon ID that differs from the normal preset.",
                ephemeral=True,
            )
            return
        saved = await self.cog.store.upsert(
            self.guild_id,
            self.owner_id,
            knowledge=knowledge,
            enabled=False,
            awaiting_restore=False,
        )
        changed = changed_positions(saved.normal, saved.knowledge)
        await interaction.response.send_message(
            f"✅ Saved Knowledge weapons for position(s) **{', '.join(map(str, changed))}**. "
            "Reminder tracking stays off until you enable it.",
            ephemeral=True,
        )


def build_knowledge_setup_embed(
    bot: commands.Bot, profile: XPReminderProfile
) -> discord.Embed:
    knowledge_icon = ui_emoji_text(bot, "passive_kno", "🧠")
    embed = discord.Embed(
        title=f"{knowledge_icon} Save your Knowledge weapons",
        description=(
            "Knowledge weapons are optional. Reminders work without them, but saving "
            "them lets the helper include ready-to-copy switch commands."
        ),
        color=0x5865F2,
    )
    if not profile.normal:
        embed.add_field(
            name="First, save the normal weapons",
            value=(
                "Show your OwO team with its usual weapons, reopen Team XP tools, "
                f"and click **⚔️**. Then reopen {knowledge_icon} and choose either method below."
            ),
            inline=False,
        )
    embed.add_field(
        name="Method 1 — Enter weapon IDs",
        value=(
            "Press **Enter IDs** and type the six-character Knowledge weapon ID for "
            "each position you want to change. Leave unchanged positions empty."
        ),
        inline=False,
    )
    embed.add_field(
        name="Method 2 — Read the equipped team",
        value=(
            "Equip the Knowledge weapons in OwO, open a fresh OwO team page, reopen "
            f"Team XP tools from that message, click {knowledge_icon}, then press **Save equipped**."
        ),
        inline=False,
    )
    embed.set_footer(
        text="Saving a weapon preset turns reminder tracking off until you enable it again."
    )
    return embed


class KnowledgeSetupView(discord.ui.View):
    def __init__(
        self,
        cog: "XPTools",
        guild_id: int,
        owner_id: int,
        snapshot: TeamXPSnapshot,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.snapshot = snapshot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "These Knowledge settings belong to another member.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Enter IDs", emoji="📝", style=discord.ButtonStyle.primary)
    async def enter_ids(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        profile = await self.cog.store.get(self.guild_id, self.owner_id)
        if not profile.normal:
            await interaction.response.send_message(
                "Save your normal weapons with **⚔️** first.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            KnowledgePresetModal(self.cog, self.guild_id, self.owner_id, profile)
        )

    @discord.ui.button(
        label="Save equipped", emoji="⚔️", style=discord.ButtonStyle.secondary
    )
    async def save_equipped(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        profile = await self.cog.store.get(self.guild_id, self.owner_id)
        if not profile.normal:
            await interaction.response.send_message(
                "Save your normal weapons with **⚔️** first.", ephemeral=True
            )
            return
        equipped = self.snapshot.members
        if not presets_compatible(profile.normal, equipped):
            await interaction.response.send_message(
                "This team does not match the saved normal team. Open a fresh page for "
                "the same team after equipping its Knowledge weapons, then try again.",
                ephemeral=True,
            )
            return
        changed = changed_positions(profile.normal, equipped)
        if not changed:
            await interaction.response.send_message(
                "The current page still shows the saved normal weapons. Equip at least "
                "one Knowledge weapon, open a fresh OwO team page, and try again.",
                ephemeral=True,
            )
            return
        await self.cog.store.upsert(
            self.guild_id,
            self.owner_id,
            knowledge=equipped,
            enabled=False,
            awaiting_restore=False,
        )
        await interaction.response.edit_message(
            content=(
                f"✅ Saved the currently equipped weapons as Knowledge for position(s) "
                f"**{', '.join(map(str, changed))}**. Reminder tracking stays off until "
                "you enable it."
            ),
            embed=None,
            view=None,
        )


class TeamLevelForecastView(discord.ui.View):
    def __init__(
        self,
        cog: "XPTools",
        owner_id: int,
        snapshot: TeamXPSnapshot,
        settings: ForecastSettings | None = None,
        *,
        guild_id: int = 0,
        helper_prefix: str = "h",
        team_tools_message: discord.Message | None = None,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.cog = cog
        self.owner_id = owner_id
        self.snapshot = snapshot
        self.settings = settings or ForecastSettings()
        self.guild_id = guild_id
        self.helper_prefix = helper_prefix
        self.team_tools_message = team_tools_message
        self.scenario_index = 0
        self.page = 0
        self.show_stats = False
        self.show_streak = True
        self._cache_key: tuple[ForecastSettings, int] | None = None
        self._cache_lines: tuple[ForecastLine, ...] = ()
        self._cache_selected_counts: dict[int, int] = {}
        self._cache_normal_counts: dict[int, int] = {}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Open the calculator from your own team panel.", ephemeral=True
        )
        return False

    def _scenario(self) -> tuple[str, int | None]:
        return KNOWLEDGE_SCENARIOS[self.scenario_index]

    def _forecast_data(
        self,
    ) -> tuple[tuple[ForecastLine, ...], dict[int, int], dict[int, int]]:
        label, interval = self._scenario()
        key = (self.settings, self.scenario_index)
        if key == self._cache_key:
            return self._cache_lines, self._cache_selected_counts, self._cache_normal_counts
        self._cache_lines, _ = simulate_team_forecast(
            self.snapshot,
            self.settings,
            use_knowledge=True,
            knowledge_interval=interval,
        )
        _, self._cache_selected_counts = simulate_team_forecast(
            self.snapshot,
            self.settings,
            use_knowledge=True,
            knowledge_interval=interval,
            line_limit=1,
            stop_after_next_levels=True,
        )
        _, self._cache_normal_counts = simulate_team_forecast(
            self.snapshot,
            self.settings,
            use_knowledge=False,
            line_limit=1,
            stop_after_next_levels=True,
        )
        self._cache_key = key
        return self._cache_lines, self._cache_selected_counts, self._cache_normal_counts

    def build_embed(self) -> discord.Embed:
        label, _interval = self._scenario()
        lines, selected_counts, normal_counts = self._forecast_data()
        page_count = max(1, math.ceil(len(lines) / FORECAST_PAGE_LINES))
        self.page = min(self.page, page_count - 1)
        start = self.page * FORECAST_PAGE_LINES
        visible = lines[start : start + FORECAST_PAGE_LINES]
        headings = ["Levels", "Battles"]
        if self.show_streak:
            headings.append("Streak")
        headings.append("Days")
        table_rows = [" | ".join(headings)]
        for line in visible:
            pet_cells = []
            for pet in line.pets:
                marker = "🆙" if pet.leveled else "⬛"
                pet_cells.append(f"{marker} {pet.level:>3}")
            row = [*pet_cells, f"{line.battles:,}"]
            if self.show_streak:
                row.append(f"{line.streak:,}")
            row.append(f"{line.days:,}" if self.settings.battles_per_day else "—")
            table_rows.append(" | ".join(row))

        embed = discord.Embed(
            title=f"🔮 Level Forecaster — {self.snapshot.source_title}",
            description=(
                f"**Knowledge plan:** {label}\n"
                "*Need tie/loss estimates? Run Neon Global in the Neon server.*"
            ),
            color=0x5865F2,
            url=XP_SOURCE_URL,
        )
        if self.show_stats:
            highest = max(pet.level for pet in self.snapshot.pets)
            for pet in self.snapshot.pets:
                selected = selected_counts.get(pet.position, 0)
                normal = normal_counts.get(pet.position, 0)
                saved = max(0, normal - selected)
                multiplier = catchup_multiplier(pet.level, highest)
                pet_icon = ui_emoji_text(self.cog.bot, animal_emoji_key(pet.animal), "🐾")
                embed.add_field(
                    name=f"{pet_icon} [{pet.position}] {pet.animal}",
                    value=(
                        f"Normal: **{normal:,}**\n"
                        f"Selected: **{selected:,}**\n"
                        f"Benefit: **{saved:,} battles**\n"
                        f"Knowledge: `{knowledge_effect_text(multiplier, self.settings.knowledge_quality)}`"
                    ),
                    inline=True,
                )
        embed.add_field(
            name="Progress",
            value="```text\n" + "\n".join(table_rows) + "\n```",
            inline=False,
        )
        maximum = f"{self.settings.maximum_streak:,}" if self.settings.maximum_streak else "none"
        embed.set_footer(
            text=(
                f"Page {self.page + 1}/{page_count} • {len(lines)} lines • "
                f"{self.settings.battles_per_day:,} battles/day • "
                f"+{self.settings.fixed_xp_per_day:,} XP/day • "
                f"{self.settings.average_xp_per_hunt:g} XP/hunt • "
                f"{self.settings.tie_rate_percent:g}% tie • "
                f"{self.settings.loss_rate_percent:g}% loss • max streak: {maximum} • "
                f"Knowledge: {self.settings.knowledge_quality * 100:g}%"
            )
        )
        self.scenario.label = f"Scenario: {label}"
        self.stats.label = "Hide stats" if self.show_stats else "Show stats"
        self.streak.label = "Hide streak" if self.show_streak else "Show streak"
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= page_count - 1
        self.page_number.label = f"Page {self.page + 1}/{page_count}"
        return embed

    @discord.ui.button(label="Settings", style=discord.ButtonStyle.secondary, row=0)
    async def settings_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(ForecastActivityModal(self))

    @discord.ui.button(label="Show stats", style=discord.ButtonStyle.secondary, row=0)
    async def stats(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        self.show_stats = not self.show_stats
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Hide streak", style=discord.ButtonStyle.secondary, row=0)
    async def streak(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        self.show_streak = not self.show_streak
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Scenario", style=discord.ButtonStyle.primary, row=0)
    async def scenario(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        self.scenario_index = (self.scenario_index + 1) % len(KNOWLEDGE_SCENARIOS)
        self.page = 0
        await interaction.response.defer()
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Page", style=discord.ButtonStyle.secondary, disabled=True, row=1)
    async def page_number(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        return

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class TeamXPToolsView(discord.ui.View):
    def __init__(
        self,
        cog: "XPTools",
        owner_id: int,
        guild_id: int,
        snapshot: TeamXPSnapshot,
        helper_prefix: str,
        settings: ForecastSettings | None = None,
    ) -> None:
        super().__init__(timeout=TEAM_TOOLS_SECONDS)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.snapshot = snapshot
        self.helper_prefix = helper_prefix
        self.settings = settings or ForecastSettings()
        icon_keys = {
            "xp_tools:save_knowledge": ("passive_kno", "🧠"),
        }
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id in icon_keys:
                key, fallback = icon_keys[item.custom_id]
                item.emoji = ui_emoji_button(self.cog.bot, key, fallback)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Open the team tools from your own reaction.", ephemeral=True)
        return False

    @discord.ui.button(label="XP calculator", emoji="🧮", style=discord.ButtonStyle.primary, row=0)
    async def calculator(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self.snapshot.pets:
            await interaction.response.send_message(
                "This team layout did not expose readable XP values. Use `/xp-calc` instead.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        panel = TeamLevelForecastView(
            self.cog,
            self.owner_id,
            self.snapshot,
            self.settings,
            guild_id=self.guild_id,
            helper_prefix=self.helper_prefix,
            team_tools_message=getattr(interaction, "message", None),
        )
        await interaction.edit_original_response(embed=panel.build_embed(), view=panel)

    @discord.ui.button(
        emoji="⚔️",
        style=discord.ButtonStyle.secondary,
        custom_id="xp_tools:save_normal",
        row=0,
    )
    async def save_normal(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        profile = await self.cog.store.upsert(
            self.guild_id,
            self.owner_id,
            normal=self.snapshot.members,
            enabled=False,
            awaiting_restore=False,
        )
        await interaction.response.send_message(
            f"✅ Saved the current {len(profile.normal)} equipped weapon(s) as your normal preset. "
            "Reminder tracking stays off until you enable it.",
            ephemeral=True,
        )

    @discord.ui.button(
        emoji="🧠",
        style=discord.ButtonStyle.secondary,
        custom_id="xp_tools:save_knowledge",
        row=0,
    )
    async def save_knowledge(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        profile = await self.cog.store.get(self.guild_id, self.owner_id)
        await interaction.response.send_message(
            embed=build_knowledge_setup_embed(self.cog.bot, profile),
            view=(
                KnowledgeSetupView(
                    self.cog,
                    self.guild_id,
                    self.owner_id,
                    self.snapshot,
                )
                if profile.normal
                else None
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        emoji="🔔",
        style=discord.ButtonStyle.success,
        custom_id="xp_tools:reminders",
        row=0,
    )
    async def reminders(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        profile = await self.cog.store.get(self.guild_id, self.owner_id)
        await interaction.response.send_message(
            embed=self.cog.build_reminder_embed(profile),
            view=XPReminderView(self.cog, self.guild_id, self.owner_id, profile),
            ephemeral=True,
        )

    @discord.ui.button(
        emoji="💾",
        style=discord.ButtonStyle.secondary,
        custom_id="xp_tools:save_team_help",
        row=0,
    )
    async def save_team_help(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            f"Reply directly to the OwO team message with "
            f"`{helper_alias(self.helper_prefix, 'ht')} C <name>` to import and save it as a team.",
            ephemeral=True,
        )


class XPTools(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = XPReminderStore(DATABASE_FILE)
        self.pending_by_channel: dict[int, list[PendingBattle]] = {}

    async def cog_load(self) -> None:
        await self.store.initialize()
        logger.info("XP reminder storage ready at %s", DATABASE_FILE)

    def purge_pending(self, channel_id: int) -> list[PendingBattle]:
        now = time.monotonic()
        pending = [
            item
            for item in self.pending_by_channel.get(channel_id, [])
            if now - item.created_at <= PENDING_BATTLE_SECONDS
        ]
        if pending:
            self.pending_by_channel[channel_id] = pending
        else:
            self.pending_by_channel.pop(channel_id, None)
        return pending

    async def arm_battle(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        profile = await self.store.get(message.guild.id, message.author.id)
        if not profile.enabled:
            return
        pending = [item for item in self.purge_pending(message.channel.id) if item.user_id != message.author.id]
        pending.append(
            PendingBattle(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                user_id=message.author.id,
                command_message_id=message.id,
                identity_tokens=identity_tokens(message.author),
                created_at=time.monotonic(),
            )
        )
        self.pending_by_channel[message.channel.id] = pending

    def match_pending(self, message: discord.Message, text: str) -> PendingBattle | None:
        if message.guild is None:
            return None
        pending = [item for item in self.purge_pending(message.channel.id) if item.guild_id == message.guild.id]
        if not pending:
            return None
        reference_id = message.reference.message_id if message.reference is not None else None
        if reference_id:
            for item in pending:
                if item.command_message_id == reference_id:
                    return item
        mentioned = {user.id for user in message.mentions}
        for item in pending:
            if item.user_id in mentioned:
                return item
        normalized = re.sub(r"[^a-z0-9_]", "", text.casefold())
        identified = [
            item for item in pending if any(token and token in normalized for token in item.identity_tokens)
        ]
        if len(identified) == 1:
            return identified[0]
        return pending[0] if len(pending) == 1 else None

    def consume_pending(self, request: PendingBattle) -> None:
        remaining = [item for item in self.pending_by_channel.get(request.channel_id, []) if item != request]
        if remaining:
            self.pending_by_channel[request.channel_id] = remaining
        else:
            self.pending_by_channel.pop(request.channel_id, None)

    def build_xp_help_embed(self, helper_prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title="🧠 Pet XP and Knowledge switching",
            description=(
                "A pet needs `1,000 + level⁴` XP for its next level. A primary-team pet gets "
                "200/100/50 base XP from a win/tie/loss. Catch-up and Knowledge can increase "
                "this battle XP."
            ),
            color=0xF1C40F,
            url=XP_SOURCE_URL,
        )
        embed.add_field(
            name="Catch-up and Knowledge",
            value=(
                "A lower-level pet gets a catch-up multiplier of `2 + 0.1 × level difference`. "
                "Knowledge adds `0.05–0.15` to the same multiplier; it does not multiply the "
                "catch-up value. This multiplier applies only to battle XP on the primary team."
            ),
            inline=False,
        )
        embed.add_field(
            name="Why switch at milestones?",
            value=(
                "Streaks at 10/50/100/500/1,000 give bonus XP. Equip Knowledge for a milestone "
                "battle to gain extra XP, then switch back to your normal weapon."
            ),
            inline=False,
        )
        rows = [f"{'Plan':<12}{'At 1K':>17}  {'At 10K':>21}"]
        for interval in REMINDER_INTERVALS:
            first_count, first_extra = switch_benefit(interval, battles=1_000)
            full_count, full_extra = switch_benefit(interval, battles=10_000)
            label = f"Every {interval:,}"
            first_value = f"+{first_extra:,.0f} ({first_count:,})"
            full_value = f"+{full_extra:,.0f} ({full_count:,})"
            rows.append(
                f"{label:<12}{first_value:>17}  {full_value:>21}"
            )
        embed.add_field(
            name="15% Knowledge — extra XP for one equipped pet",
            value=(
                "We did the calculations for you. The table shows the extra XP gained when you "
                "switch at each milestone. Numbers in parentheses are the number of switches.\n"
                "```text\n" + "\n".join(rows) + "\n```"
            ),
            inline=False,
        )
        embed.add_field(
            name="Use the tools",
            value=(
                "Use `/xp-calc` for custom levels and activity. Click the helper reaction under an OwO "
                "team page for a forecast and to save normal/Knowledge weapon presets. Open "
                f"`{helper_command(helper_prefix, 'reminders')}` or `/xp-reminders` to configure prompts."
            ),
            inline=False,
        )
        embed.add_field(
            name="Credits",
            value=(
                f"Based on [Heather's Pet EXP guide]({XP_SOURCE_URL}). Community streak and XP "
                "sheets were also used to check the model."
            ),
            inline=False,
        )
        embed.set_footer(
            text="Original Pet EXP guide: Heather • Community streak/XP sheets"
        )
        return embed

    def build_reminder_embed(self, profile: XPReminderProfile) -> discord.Embed:
        changed = changed_positions(profile.normal, profile.knowledge)
        status = "Enabled" if profile.enabled else "Disabled"
        mode = "Copyable weapon commands" if profile.ready else "Simple switch prompts"
        knowledge_icon = ui_emoji_text(self.bot, "passive_kno", "🧠")
        embed = discord.Embed(
            title="🔔 Knowledge-switch reminders",
            description=(
                "The helper watches only your configured OwO battle command and its matching result. "
                "It prompts before the selected milestone, keeps the Knowledge step open through ties, "
                "and reminds you to switch back after a win or loss. Weapon presets are optional."
            ),
            color=0x57F287 if profile.enabled else 0x747F8D,
        )
        embed.add_field(
            name="Current settings",
            value=(
                f"**Status:** {status}\n"
                f"**Interval:** every {profile.interval:,} wins\n"
                f"**Prompt mode:** {mode}\n"
                f"**Mention:** {'On' if profile.mention else 'Off'}\n"
                f"**Waiting to restore:** {'Yes' if profile.awaiting_restore else 'No'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Weapon presets",
            value=(
                f"**Normal:** {len(profile.normal)} weapon(s)\n"
                f"**Knowledge:** {len(profile.knowledge)} weapon(s)\n"
                f"**Switch positions:** {', '.join(map(str, changed)) if changed else 'None'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Setup options",
            value=(
                "**Simple prompts:** choose an interval and press **Enable**. No weapon IDs are required.\n"
                f"**Copyable commands:** save normal weapons with **⚔️**, then click {knowledge_icon} "
                "and either enter IDs or save a freshly equipped Knowledge team."
            ),
            inline=False,
        )
        benefit_lines = []
        for interval in REMINDER_INTERVALS:
            count, extra = switch_benefit(interval)
            benefit_lines.append(
                f"**{interval:,}:** {count} switch battle(s) • about +{extra:,.0f} XP/pet"
            )
        embed.add_field(
            name="Benefit across 1,000 straight wins",
            value="\n".join(benefit_lines) + "\n*Uses maximum 15% Knowledge; real results follow each weapon's quality.*",
            inline=False,
        )
        embed.set_footer(text="No reminder is enabled until you press Enable. Saving a preset turns tracking off for safety.")
        return embed

    def build_team_tools_embed(
        self,
        snapshot: TeamXPSnapshot,
        settings: ForecastSettings | None = None,
    ) -> discord.Embed:
        settings = settings or ForecastSettings()
        average_text = "not available"
        baseline: dict[int, int] = {}
        modeled: dict[int, int] = {}
        modeled_knowledge: dict[int, int] = {}
        if snapshot.streak <= MAX_VALIDATED_STREAK:
            average_text = f"{current_streak_average_xp(snapshot.streak):,.1f} XP/battle"
            baseline, _ = next_level_forecasts(snapshot, ForecastSettings())
            modeled, modeled_knowledge = next_level_forecasts(snapshot, settings)
        embed = discord.Embed(
            title="Team XP tools",
            description=(
                f"**Current streak:** {snapshot.streak:,} • **Calculated average:** {average_text}\n"
                f"**Saved model:** {settings.battles_per_day:,} battles/day • "
                f"+{settings.fixed_xp_per_day:,} fixed XP/day • "
                f"{settings.average_xp_per_hunt:g} XP/hunt • "
                f"{settings.tie_rate_percent:g}% ties • {settings.loss_rate_percent:g}% losses • "
                f"max streak {'none' if not settings.maximum_streak else f'{settings.maximum_streak:,}'}"
            ),
            color=0x5865F2,
        )
        for pet in snapshot.pets:
            multiplier = catchup_multiplier(
                pet.level, max(item.level for item in snapshot.pets)
            )
            pet_icon = ui_emoji_text(self.bot, animal_emoji_key(pet.animal), "🐾")
            model_battles = modeled.get(pet.position, 0)
            model_days = (
                f" (~{model_battles / settings.battles_per_day:,.1f} days)"
                if settings.battles_per_day and model_battles
                else ""
            )
            embed.add_field(
                name=f"{pet_icon} [{pet.position}] {pet.animal}",
                value=(
                    f"**L.{pet.level} → L.{pet.level + 1}**\n"
                    f"Need: **{max(0, pet.next_xp - pet.current_xp):,} XP**\n"
                    f"Knowledge: `{knowledge_effect_text(multiplier, settings.knowledge_quality)}`\n"
                    f"Normal: up to **{baseline.get(pet.position, 0):,} battles**\n"
                    f"Your model: up to **{model_battles:,} battles**{model_days}\n"
                    f"Model + Max Knowledge: **{modeled_knowledge.get(pet.position, 0):,}**"
                ),
                inline=True,
            )
        if not snapshot.pets:
            embed.description = "The team is readable, but this OwO layout did not expose pet XP values."
        embed.set_footer(
            text=(
                f"{XP_CREDIT_FOOTER} • XP calculator opens the adjustable full-team "
                "forecast and all six Knowledge plans"
            )
        )
        return embed

    def build_team_forecast_embed(self, snapshot: TeamXPSnapshot) -> discord.Embed:
        calculate_icon = ui_emoji_text(self.bot, "neon_calculate", "📈")
        knowledge_icon = ui_emoji_text(self.bot, "passive_kno", "🧠")
        embed = discord.Embed(title=f"{calculate_icon} Next-level forecast", color=0x3498DB)
        if not snapshot.pets:
            embed.description = "This team layout did not expose readable level/XP values. Use `/xp-calc` instead."
            return embed
        if snapshot.streak > MAX_VALIDATED_STREAK:
            embed.description = (
                f"This team's streak is {snapshot.streak:,}. The supplied public model changes above "
                f"{MAX_VALIDATED_STREAK:,}, so I will not extrapolate it without a verified formula."
            )
            return embed
        highest = max(pet.level for pet in snapshot.pets)
        average_xp = current_streak_average_xp(snapshot.streak)
        embed.description = (
            f"**Current streak:** {snapshot.streak:,} • "
            f"**Calculated average:** {average_xp:,.1f} XP/battle"
        )
        for pet in snapshot.pets:
            needed = max(0, pet.next_xp - pet.current_xp)
            multiplier = catchup_multiplier(pet.level, highest)
            base_battles = estimated_battles_needed(
                pet.level, pet.level + 1, pet.current_xp, highest, average_xp
            ) if needed else 0
            knowledge_battles = estimated_battles_needed(
                pet.level,
                pet.level + 1,
                pet.current_xp,
                highest,
                average_xp,
                knowledge_bonus=0.15,
            ) if needed else 0
            pet_icon = ui_emoji_text(self.bot, animal_emoji_key(pet.animal), "🐾")
            embed.add_field(
                name=f"{pet_icon} [{pet.position}] {pet.animal}",
                value=(
                    f"**L.{pet.level} → L.{pet.level + 1}**\n"
                    f"Need: **{needed:,} XP**\n"
                    f"Multiplier: **{multiplier:.2f}×**\n"
                    f"Up to **{base_battles:,} battles**\n"
                    f"{knowledge_icon} Max Knowledge: **{knowledge_battles:,}**"
                ),
                inline=True,
            )
        embed.set_footer(
            text=(
                "Battle-only upper estimate • Ties, losses, enemy bonuses, hunts, "
                "and fixed daily XP change the result"
            )
        )
        return embed

    def build_calculator_embed(
        self,
        *,
        start_level: int,
        target_level: int,
        highest_level: int,
        current_xp: int,
        battles_per_day: int,
        fixed_xp_per_day: int,
        knowledge_quality: float,
        current_streak: int,
        maximum_streak: int,
    ) -> discord.Embed:
        if maximum_streak < current_streak:
            raise ValueError("Maximum streak must be at least the current streak")
        actual_xp = total_level_xp(start_level, target_level, current_xp)
        base_raw = raw_battle_xp_needed(start_level, target_level, current_xp, highest_level, 0)
        average_xp = current_streak_average_xp(current_streak)
        fixed_per_battle = fixed_xp_per_day / battles_per_day
        base_battles = estimated_battles_needed(
            start_level,
            target_level,
            current_xp,
            highest_level,
            average_xp,
            fixed_xp_per_battle=fixed_per_battle,
        )
        knowledge_battles = estimated_battles_needed(
            start_level,
            target_level,
            current_xp,
            highest_level,
            average_xp,
            knowledge_bonus=knowledge_quality,
            fixed_xp_per_battle=fixed_per_battle,
        )
        calculate_icon = ui_emoji_text(self.bot, "neon_calculate", "📊")
        knowledge_icon = ui_emoji_text(self.bot, "passive_kno", "🧠")
        embed = discord.Embed(
            title=f"{calculate_icon} L.{start_level} → L.{target_level} XP forecast",
            description="Streak-derived battle XP is calculated automatically—no average-XP input needed.",
            color=0xF1C40F,
            url=XP_SOURCE_URL,
        )
        embed.add_field(
            name="📍 Level plan",
            value=(
                f"**Pet:** L.{start_level} → L.{target_level}\n"
                f"**Highest teammate:** L.{highest_level} (held fixed)\n"
                f"**Pet XP remaining:** {actual_xp:,.0f}\n"
                f"**Raw battle XP required:** {base_raw:,.0f}"
            ),
            inline=True,
        )
        embed.add_field(
            name="⭐ XP model",
            value=(
                f"**Current streak:** {current_streak:,}\n"
                f"**Maximum streak:** {maximum_streak:,}\n"
                f"**Calculated average:** {average_xp:,.1f}/battle\n"
                f"**Battles/day:** {battles_per_day:,}\n"
                f"**Fixed XP/day:** {fixed_xp_per_day:,}"
            ),
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(
            name="⚔️ Normal setup",
            value=(
                f"Up to **{base_battles:,} battles**\n"
                f"About **{base_battles / battles_per_day:,.1f} days**"
            ),
            inline=True,
        )
        embed.add_field(
            name=f"{knowledge_icon} {knowledge_quality * 100:.0f}% Knowledge",
            value=(
                f"Up to **{knowledge_battles:,} battles**\n"
                f"About **{knowledge_battles / battles_per_day:,.1f} days**"
            ),
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        rows = []
        for interval in REMINDER_INTERVALS:
            count, extra = switch_benefit(
                interval,
                start_streak=0,
                battles=maximum_streak,
                knowledge_bonus=knowledge_quality,
            )
            rows.append(
                f"**Every {interval:,}:** {count:,} switch(es) • "
                f"+{extra:,.0f} extra XP per pet"
            )
        embed.add_field(
            name=f"Milestone switching — streak 0 → {maximum_streak:,}",
            value="\n".join(rows),
            inline=False,
        )
        embed.add_field(
            name="Assumptions",
            value=(
                f"Highest companion stays L.{highest_level}; catch-up is recalculated each level; "
                f"average battle XP is derived from streak {current_streak:,}; fixed daily XP is spread "
                "across the configured battles; hunting and higher-enemy-level bonuses are excluded. "
                "For three pets leveling together, use the Team XP full forecaster."
            ),
            inline=False,
        )
        return embed

    async def send_team_tools_from_reaction(
        self,
        payload: discord.RawReactionActionEvent,
        raw: dict[str, Any],
        channel: discord.abc.Messageable,
    ) -> bool:
        snapshot = parse_team_xp_snapshot(extract_all_text(raw))
        if snapshot is None:
            return False
        helper_prefix = await get_guild_helper_prefix(payload.guild_id)
        settings = await self.store.get_forecast_settings(payload.user_id)
        await channel.send(
            embed=self.build_team_tools_embed(snapshot, settings),
            view=TeamXPToolsView(
                self,
                payload.user_id,
                payload.guild_id,
                snapshot,
                helper_prefix,
                settings,
            ),
            reference=discord.MessageReference(
                message_id=payload.message_id,
                channel_id=payload.channel_id,
                guild_id=payload.guild_id,
            ),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    async def send_reminder_panel(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        profile = await self.store.get(message.guild.id, message.author.id)
        await safe_reply(
            message,
            embed=self.build_reminder_embed(profile),
            view=XPReminderView(self, message.guild.id, message.author.id, profile),
            mention_author=False,
        )

    async def handle_reminder_argument(self, message: discord.Message, argument: str) -> None:
        if message.guild is None:
            return
        tokens = re.sub(r"\s+", " ", argument.strip()).casefold().split()
        if not tokens:
            await self.send_reminder_panel(message)
            return
        profile = await self.store.get(message.guild.id, message.author.id)
        if tokens[0] in {"off", "disable", "stop"}:
            await self.store.upsert(
                message.guild.id,
                message.author.id,
                enabled=False,
                awaiting_restore=False,
                trigger_streak=0,
            )
            await safe_reply(message, "🔕 XP switch reminders disabled.", mention_author=False)
            return
        if tokens[0] in {"mention", "ping"} and len(tokens) > 1:
            enabled = tokens[1] in {"on", "yes", "true", "1"}
            await self.store.upsert(message.guild.id, message.author.id, mention=enabled)
            await safe_reply(message, f"👤 Reminder mentions {'enabled' if enabled else 'disabled'}.", mention_author=False)
            return
        requested_interval = next((int(token) for token in tokens if token.isdigit()), None)
        if requested_interval is not None and requested_interval not in REMINDER_INTERVALS:
            await safe_reply(message, "Choose `10`, `50`, `100`, `500`, or `1000`.", mention_author=False)
            return
        enable_requested = tokens[0] in {"on", "enable", "start"} or requested_interval is not None
        if enable_requested:
            updated = await self.store.upsert(
                message.guild.id,
                message.author.id,
                enabled=True,
                interval=requested_interval or profile.interval,
            )
            await safe_reply(
                message,
                f"🔔 XP switch reminders enabled every **{updated.interval:,}** wins "
                f"with **{'copyable commands' if updated.ready else 'simple switch prompts'}**.",
                mention_author=False,
            )
            return
        await self.send_reminder_panel(message)

    async def process_battle_result(self, message: discord.Message, result: BattleResult, text: str) -> None:
        request = self.match_pending(message, text)
        if request is None:
            return
        self.consume_pending(request)
        profile = await self.store.get(request.guild_id, request.user_id)
        if not profile.enabled or profile.last_battle_message_id == message.id:
            return
        owo_prefix = await get_guild_owo_prefix(request.guild_id)
        mention = f"<@{request.user_id}> " if profile.mention else ""
        allowed = discord.AllowedMentions(users=profile.mention, replied_user=False)

        if profile.awaiting_restore:
            if result.outcome == "tie":
                await self.store.upsert(
                    request.guild_id,
                    request.user_id,
                    last_streak=result.streak,
                    last_battle_message_id=message.id,
                )
                logger.info(
                    "Kept Knowledge preset active through tie for user %s in guild %s",
                    request.user_id,
                    request.guild_id,
                )
                return
            commands = weapon_switch_commands(profile.knowledge, profile.normal, owo_prefix)
            restore_text = (
                f"↩️ **{result.outcome.title()} recorded at streak {result.streak:,}. "
                "Switch back to your normal weapons now.**"
            )
            if commands:
                restore_text += "\n" + "\n".join(
                    f"`{command}`" for command in commands
                )
            try:
                await message.channel.send(
                    mention + restore_text,
                    reference=message.to_reference(fail_if_not_exists=False),
                    allowed_mentions=allowed,
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                logger.warning(
                    "Could not send normal-weapon reminder for user %s in guild %s: %s",
                    request.user_id,
                    request.guild_id,
                    exc,
                )
                await self.store.upsert(
                    request.guild_id,
                    request.user_id,
                    last_streak=result.streak,
                    last_battle_message_id=message.id,
                )
                return
            await self.store.upsert(
                request.guild_id,
                request.user_id,
                awaiting_restore=False,
                trigger_streak=0,
                last_streak=result.streak,
                last_battle_message_id=message.id,
            )
            return

        await self.store.upsert(
            request.guild_id,
            request.user_id,
            last_streak=result.streak,
            last_battle_message_id=message.id,
        )
        if result.outcome != "win" or not should_prompt_switch(result.streak, profile.interval):
            return
        target = next_switch_streak(result.streak, profile.interval)
        commands = weapon_switch_commands(profile.normal, profile.knowledge, owo_prefix)
        switch_text = (
            f"🧠 **Streak {result.streak:,}: the next win reaches {target:,}. "
            "Switch to your Knowledge weapons for the next battle now.**"
        )
        if commands:
            switch_text += "\n" + "\n".join(
                f"`{command}`" for command in commands
            )
        switch_text += (
            "\nA tie keeps this step open. After a win or loss, I will remind you "
            "to switch back."
        )
        try:
            await message.channel.send(
                mention + switch_text,
                reference=message.to_reference(fail_if_not_exists=False),
                allowed_mentions=allowed,
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            logger.warning(
                "Could not send Knowledge reminder for user %s in guild %s: %s",
                request.user_id,
                request.guild_id,
                exc,
            )
            return
        await self.store.upsert(
            request.guild_id,
            request.user_id,
            awaiting_restore=True,
            trigger_streak=target,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if not message.author.bot:
            helper_prefix = await get_guild_helper_prefix(message.guild.id)
            if matches_helper_command(message.content or "", helper_prefix, XP_HELP_ALIASES):
                await safe_reply(message, embed=self.build_xp_help_embed(helper_prefix), mention_author=False)
                return
            reminder_argument = parse_helper_command_argument(
                message.content or "", helper_prefix, REMINDER_ALIASES
            )
            if reminder_argument is not None:
                await self.handle_reminder_argument(message, reminder_argument)
                return
            owo_prefix = await get_guild_owo_prefix(message.guild.id)
            if is_owo_prefixed_command(message.content or "", owo_prefix, BATTLE_COMMAND_SUFFIXES):
                await self.arm_battle(message)
            return
        if message.author.id != OWO_BOT_ID:
            return
        text = extract_message_text(message)
        result = parse_battle_result(text)
        if result is not None:
            await self.process_battle_result(message, result, text)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.author.id != OWO_BOT_ID:
            return
        text = extract_message_text(after)
        result = parse_battle_result(text)
        if result is not None:
            await self.process_battle_result(after, result, text)

    @app_commands.command(name="xp-reminders", description="Configure opt-in Knowledge weapon milestone reminders.")
    @app_commands.guild_only()
    async def xp_reminders(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        profile = await self.store.get(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            embed=self.build_reminder_embed(profile),
            view=XPReminderView(self, interaction.guild_id, interaction.user.id, profile),
            ephemeral=True,
        )

    @app_commands.command(name="xp-calc", description="Forecast battle XP and levels with dynamic catch-up and Knowledge.")
    @app_commands.describe(
        start_level="Pet's current level",
        target_level="Level to reach",
        highest_level="Level of the highest companion that stays on the team",
        current_xp="Pet's current XP progress inside its level",
        current_streak="Current streak used to calculate XP per battle",
        maximum_streak="Streak horizon for the milestone-switch simulation",
        battles_per_day="Expected battles per day",
        fixed_xp_per_day="XP earned daily outside battles, such as hunts",
        knowledge_quality="Knowledge bonus from 0.05 to 0.15; use 0 for none",
    )
    async def xp_calc(
        self,
        interaction: discord.Interaction,
        start_level: app_commands.Range[int, 1, MAX_LEVEL - 1],
        target_level: app_commands.Range[int, 2, MAX_LEVEL],
        highest_level: app_commands.Range[int, 1, MAX_LEVEL],
        current_streak: app_commands.Range[int, 0, MAX_VALIDATED_STREAK],
        maximum_streak: app_commands.Range[int, 10, MAX_VALIDATED_STREAK] = 10_000,
        current_xp: app_commands.Range[int, 0, 2_000_000_000] = 0,
        battles_per_day: app_commands.Range[int, 1, 5760] = 1000,
        fixed_xp_per_day: app_commands.Range[int, 0, 2_000_000_000] = 0,
        knowledge_quality: app_commands.Range[float, 0.0, 0.15] = 0.15,
    ) -> None:
        try:
            embed = self.build_calculator_embed(
                start_level=start_level,
                target_level=target_level,
                highest_level=highest_level,
                current_xp=current_xp,
                battles_per_day=battles_per_day,
                fixed_xp_per_day=fixed_xp_per_day,
                knowledge_quality=float(knowledge_quality),
                current_streak=current_streak,
                maximum_streak=maximum_streak,
            )
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(XPTools(bot))
