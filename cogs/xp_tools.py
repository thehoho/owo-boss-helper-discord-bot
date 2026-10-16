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
from dataclasses import dataclass
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

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "xp_reminders.db"
XP_SOURCE_URL = "https://owo.bwep.net/exp/"
STREAK_DATA_URL = "https://docs.google.com/spreadsheets/d/1qXl5N8VYT1C0yUyiYQ1QzdlNcPMo9iEFu8HMJ4zmuKc/edit"
XP_DATA_URL = "https://docs.google.com/spreadsheets/d/1E_YSDmmX-WVfoX29dkpPcgCwY_9K7g_uM3pVZN2E_74/edit"
REMINDER_INTERVALS = (10, 50, 100, 500, 1000)
BATTLE_COMMAND_SUFFIXES = {"b", "battle"}
PENDING_BATTLE_SECONDS = 45
TEAM_TOOLS_SECONDS = 5 * 60
MAX_LEVEL = 250
MAX_VALIDATED_STREAK = 122_500

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
        profile = await self.cog.store.get(self.guild_id, self.owner_id)
        if not profile.ready:
            await interaction.response.send_message(
                "Save both a normal and a different Knowledge weapon preset from the team-tools reaction first.",
                ephemeral=True,
            )
            return
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


class TeamXPCalculatorModal(discord.ui.Modal, title="Team XP calculator"):
    pet_position = discord.ui.TextInput(
        label="Pet position (1, 2, or 3)",
        placeholder="1",
        min_length=1,
        max_length=1,
    )
    target_level = discord.ui.TextInput(
        label="Target level",
        placeholder="50",
        min_length=1,
        max_length=3,
    )
    average_xp = discord.ui.TextInput(
        label="Average raw XP per battle",
        placeholder="350",
        min_length=1,
        max_length=8,
    )
    battles_per_day = discord.ui.TextInput(
        label="Battles per day",
        placeholder="1000",
        min_length=1,
        max_length=5,
    )
    knowledge_quality = discord.ui.TextInput(
        label="Knowledge bonus (0 to 0.15)",
        placeholder="0.15",
        min_length=1,
        max_length=4,
    )

    def __init__(self, cog: "XPTools", snapshot: TeamXPSnapshot) -> None:
        super().__init__()
        self.cog = cog
        self.snapshot = snapshot
        if snapshot.pets:
            self.pet_position.default = str(snapshot.pets[0].position)
            self.target_level.default = str(min(MAX_LEVEL, snapshot.pets[0].level + 1))
        if 0 < snapshot.streak <= MAX_VALIDATED_STREAK:
            self.average_xp.default = f"{average_winning_streak_xp(snapshot.streak):.1f}"
        else:
            self.average_xp.default = "350"
        self.battles_per_day.default = "1000"
        self.knowledge_quality.default = "0.15"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            position = int(str(self.pet_position))
            pet = next(item for item in self.snapshot.pets if item.position == position)
            target = int(str(self.target_level))
            average = float(str(self.average_xp))
            per_day = int(str(self.battles_per_day))
            knowledge = float(str(self.knowledge_quality))
            if target <= pet.level or target > MAX_LEVEL:
                raise ValueError(f"Target level must be between {pet.level + 1} and {MAX_LEVEL}")
            if not 50 <= average <= 10_000:
                raise ValueError("Average XP must be between 50 and 10,000")
            if not 1 <= per_day <= 5_760:
                raise ValueError("Battles per day must be between 1 and 5,760")
            if not 0 <= knowledge <= 0.15:
                raise ValueError("Knowledge must be between 0 and 0.15")
            highest = max(item.level for item in self.snapshot.pets)
            embed = self.cog.build_calculator_embed(
                start_level=pet.level,
                target_level=target,
                highest_level=highest,
                current_xp=pet.current_xp,
                average_xp=average,
                battles_per_day=per_day,
                knowledge_quality=knowledge,
                current_streak=min(self.snapshot.streak, MAX_VALIDATED_STREAK),
            )
        except (ValueError, StopIteration) as exc:
            detail = str(exc) or "Choose a pet position shown on this team."
            await interaction.response.send_message(f"❌ {detail}", ephemeral=True)
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TeamXPToolsView(discord.ui.View):
    def __init__(
        self,
        cog: "XPTools",
        owner_id: int,
        guild_id: int,
        snapshot: TeamXPSnapshot,
        helper_prefix: str,
    ) -> None:
        super().__init__(timeout=TEAM_TOOLS_SECONDS)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.snapshot = snapshot
        self.helper_prefix = helper_prefix

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Open the team tools from your own reaction.", ephemeral=True)
        return False

    @discord.ui.button(label="XP forecast", emoji="📈", style=discord.ButtonStyle.primary)
    async def forecast(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            embed=self.cog.build_team_forecast_embed(self.snapshot),
            ephemeral=True,
        )

    @discord.ui.button(label="XP calculator", emoji="🧮", style=discord.ButtonStyle.primary, row=1)
    async def calculator(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self.snapshot.pets:
            await interaction.response.send_message(
                "This team layout did not expose readable XP values. Use `/xp-calc` instead.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TeamXPCalculatorModal(self.cog, self.snapshot))

    @discord.ui.button(label="Save normal", emoji="⚔️", style=discord.ButtonStyle.secondary)
    async def save_normal(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        profile = await self.cog.store.upsert(
            self.guild_id,
            self.owner_id,
            normal=self.snapshot.members,
            enabled=False,
            awaiting_restore=False,
        )
        await interaction.response.send_message(
            f"✅ Saved the normal weapon preset with {len(profile.normal)} equipped weapon(s). Reminder tracking stays off until you enable it.",
            ephemeral=True,
        )

    @discord.ui.button(label="Save Knowledge", emoji="🧠", style=discord.ButtonStyle.secondary)
    async def save_knowledge(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        current = await self.cog.store.get(self.guild_id, self.owner_id)
        if current.normal and not presets_compatible(current.normal, self.snapshot.members):
            await interaction.response.send_message(
                "This team has different animals or positions from your normal preset. Save the matching normal team first.",
                ephemeral=True,
            )
            return
        profile = await self.cog.store.upsert(
            self.guild_id,
            self.owner_id,
            knowledge=self.snapshot.members,
            enabled=False,
            awaiting_restore=False,
        )
        changed = changed_positions(profile.normal, profile.knowledge)
        note = (
            f"Positions with a Knowledge switch: **{', '.join(map(str, changed))}**."
            if changed
            else "No weapon differs from the normal preset yet."
        )
        await interaction.response.send_message(
            f"✅ Saved the Knowledge preset. {note} Reminder tracking stays off until you enable it.",
            ephemeral=True,
        )

    @discord.ui.button(label="Reminders", emoji="🔔", style=discord.ButtonStyle.success)
    async def reminders(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        profile = await self.cog.store.get(self.guild_id, self.owner_id)
        await interaction.response.send_message(
            embed=self.cog.build_reminder_embed(profile),
            view=XPReminderView(self.cog, self.guild_id, self.owner_id, profile),
            ephemeral=True,
        )

    @discord.ui.button(label="Save-team help", emoji="💾", style=discord.ButtonStyle.secondary)
    async def save_team_help(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            f"Reply directly to the OwO team message with `{helper_alias(self.helper_prefix, 'ht')} C <name>` to save the team template.",
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
        if not profile.enabled or not profile.ready:
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
                "Level XP is `1,000 + level⁴`. Wins/ties/losses give 200/100/50 base battle XP, "
                "then catch-up and Knowledge affect eligible primary-team battle XP."
            ),
            color=0xF1C40F,
            url=XP_SOURCE_URL,
        )
        embed.add_field(
            name="Catch-up and Knowledge",
            value=(
                "A lower-level pet's catch-up multiplier is `2 + 0.1 × level difference`. "
                "Knowledge adds `0.05–0.15` to that multiplier; it does not multiply it and "
                "does not apply to hunting or fixed daily XP."
            ),
            inline=False,
        )
        embed.add_field(
            name="Why switch at milestones?",
            value=(
                "Streaks divisible by 10/50/100/500/1,000 grant increasingly valuable bonus XP. "
                "Equipping Knowledge only for that battle captures extra XP without weakening every "
                "ordinary battle. The chance of losing one battle may be low for a strong team, but it is never zero."
            ),
            inline=False,
        )
        rows = []
        for interval in REMINDER_INTERVALS:
            qualifying, extra = switch_benefit(interval)
            rows.append(
                f"**Every {interval:,}:** {qualifying:,} switch battle(s) in 1,000 wins • "
                f"about **{extra:,.0f}** extra XP per pet at 15% Knowledge"
            )
        embed.add_field(name="1,000-win comparison", value="\n".join(rows), inline=False)
        embed.add_field(
            name="Use the tools",
            value=(
                "Use `/xp-calc` for custom levels and activity. Click the helper reaction under an OwO "
                "team page for a forecast and to save normal/Knowledge weapon presets. Open "
                f"`{helper_command(helper_prefix, 'reminders')}` or `/xp-reminders` to configure prompts."
            ),
            inline=False,
        )
        embed.set_footer(
            text="Formulas: owo.bwep.net • Community streak/XP sheets • Thanks to Heather / Hasse for the reference"
        )
        return embed

    def build_reminder_embed(self, profile: XPReminderProfile) -> discord.Embed:
        changed = changed_positions(profile.normal, profile.knowledge)
        status = "Enabled" if profile.enabled else "Disabled"
        embed = discord.Embed(
            title="🔔 Knowledge-switch reminders",
            description=(
                "The helper watches only your configured OwO battle command and its matching result. "
                "It prompts before the selected milestone, keeps the Knowledge step open through ties, "
                "and gives the normal preset after a win or loss."
            ),
            color=0x57F287 if profile.enabled else 0x747F8D,
        )
        embed.add_field(
            name="Current settings",
            value=(
                f"**Status:** {status}\n"
                f"**Interval:** every {profile.interval:,} wins\n"
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
            name="Setup",
            value=(
                "1. Show the correct OwO team with normal weapons and click the helper reaction → **Save normal**.\n"
                "2. Equip the Knowledge weapon(s), show the same team again, and click → **Save Knowledge**.\n"
                "3. Choose an interval and enable reminders below."
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
        embed.set_footer(text="No reminder is enabled until you press Enable. Saving either preset turns tracking off for safety.")
        return embed

    def build_team_tools_embed(self, snapshot: TeamXPSnapshot) -> discord.Embed:
        pet_lines = []
        for pet in snapshot.pets:
            percent = 100 * pet.current_xp / pet.next_xp if pet.next_xp else 0
            pet_lines.append(
                f"**[{pet.position}] {pet.animal}** — L.{pet.level} • "
                f"{pet.current_xp:,}/{pet.next_xp:,} XP ({percent:.1f}%)"
            )
        embed = discord.Embed(
            title="🧰 Team tools",
            description="\n".join(pet_lines) if pet_lines else "The team is readable, but this OwO layout did not expose pet XP values.",
            color=0x5865F2,
        )
        embed.add_field(name="Current streak", value=f"{snapshot.streak:,}", inline=True)
        embed.add_field(name="Equipped weapons", value=str(sum(bool(member.weapon_id) for member in snapshot.members)), inline=True)
        embed.set_footer(text="The panel belongs to the member who clicked the reaction and expires after five minutes.")
        return embed

    def build_team_forecast_embed(self, snapshot: TeamXPSnapshot) -> discord.Embed:
        embed = discord.Embed(title="📈 Next-level XP forecast", color=0x3498DB)
        if not snapshot.pets:
            embed.description = "This team layout did not expose readable level/XP values. Use `/xp-calc` instead."
            return embed
        if snapshot.streak > MAX_VALIDATED_STREAK:
            embed.description = (
                f"This team's streak is {snapshot.streak:,}. The supplied public model changes above "
                f"{MAX_VALIDATED_STREAK:,}, so I will not extrapolate it. Use `/xp-calc` with an "
                "average XP value measured from your recent battles."
            )
            return embed
        highest = max(pet.level for pet in snapshot.pets)
        average_xp = average_winning_streak_xp(snapshot.streak)
        lines = []
        for pet in snapshot.pets:
            needed = max(0, pet.next_xp - pet.current_xp)
            multiplier = catchup_multiplier(pet.level, highest)
            base_battles = math.ceil((needed / multiplier) / average_xp) if needed else 0
            knowledge_battles = math.ceil((needed / (multiplier + 0.15)) / average_xp) if needed else 0
            lines.append(
                f"**[{pet.position}] {pet.animal} → L.{pet.level + 1}**\n"
                f"{needed:,} pet XP • {multiplier:.2f}× catch-up • up to **{base_battles:,}** wins "
                f"(**{knowledge_battles:,}** with 15% Knowledge always equipped)"
            )
        embed.description = "\n\n".join(lines)
        embed.add_field(
            name="Model",
            value=(
                f"Current streak {snapshot.streak:,} gives an exact all-win run average of "
                f"**{average_xp:,.1f} raw XP/battle**. This is an optimistic battle-only estimate; "
                "ties, losses, enemy-level bonuses, hunts, and fixed daily XP change the result."
            ),
            inline=False,
        )
        return embed

    def build_calculator_embed(
        self,
        *,
        start_level: int,
        target_level: int,
        highest_level: int,
        current_xp: int,
        average_xp: float,
        battles_per_day: int,
        knowledge_quality: float,
        current_streak: int,
    ) -> discord.Embed:
        actual_xp = total_level_xp(start_level, target_level, current_xp)
        base_raw = raw_battle_xp_needed(start_level, target_level, current_xp, highest_level, 0)
        knowledge_raw = raw_battle_xp_needed(
            start_level, target_level, current_xp, highest_level, knowledge_quality
        )
        base_battles = math.ceil(base_raw / average_xp)
        knowledge_battles = math.ceil(knowledge_raw / average_xp)
        embed = discord.Embed(
            title=f"📊 L.{start_level} → L.{target_level} XP calculator",
            description=(
                f"**Pet XP remaining:** {actual_xp:,.0f}\n"
                f"**Raw battle XP needed after dynamic catch-up:** {base_raw:,.0f}\n"
                f"**Estimated battles:** {base_battles:,} without Knowledge • "
                f"{knowledge_battles:,} with {knowledge_quality * 100:.0f}% Knowledge always equipped\n"
                f"**Estimated days:** {base_battles / battles_per_day:,.1f} • "
                f"{knowledge_battles / battles_per_day:,.1f} with Knowledge"
            ),
            color=0xF1C40F,
            url=XP_SOURCE_URL,
        )
        rows = []
        for interval in REMINDER_INTERVALS:
            count, extra = switch_benefit(
                interval,
                start_streak=current_streak,
                knowledge_bonus=knowledge_quality,
            )
            rows.append(
                f"**{interval:,}:** {count} switch(es), +{extra:,.0f} XP/pet over the next 1,000 wins"
            )
        embed.add_field(name="Milestone-switch comparison", value="\n".join(rows), inline=False)
        embed.add_field(
            name="Assumptions",
            value=(
                f"Highest companion stays L.{highest_level}; catch-up is recalculated each level; "
                f"average raw battle XP is {average_xp:,.1f}; {battles_per_day:,} battles/day; "
                "no hunting, fixed daily XP, or higher-enemy-level bonus."
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
        user = self.bot.get_user(payload.user_id)
        mention = user.mention if user else f"<@{payload.user_id}>"
        await channel.send(
            mention,
            embed=self.build_team_tools_embed(snapshot),
            view=TeamXPToolsView(self, payload.user_id, payload.guild_id, snapshot, helper_prefix),
            reference=discord.MessageReference(
                message_id=payload.message_id,
                channel_id=payload.channel_id,
                guild_id=payload.guild_id,
            ),
            mention_author=False,
            delete_after=TEAM_TOOLS_SECONDS,
            allowed_mentions=discord.AllowedMentions(users=True, replied_user=False),
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
            if not profile.ready:
                await safe_reply(
                    message,
                    "Save matching normal and Knowledge presets from the team-tools reaction before enabling reminders.",
                    mention_author=False,
                )
                return
            updated = await self.store.upsert(
                message.guild.id,
                message.author.id,
                enabled=True,
                interval=requested_interval or profile.interval,
            )
            await safe_reply(
                message,
                f"🔔 XP switch reminders enabled every **{updated.interval:,}** wins.",
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
        if not profile.enabled or not profile.ready or profile.last_battle_message_id == message.id:
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
            if commands:
                try:
                    await message.channel.send(
                        mention
                        + f"↩️ **{result.outcome.title()} recorded at streak {result.streak:,}. Restore normal weapons:**\n"
                        + "\n".join(f"`{command}`" for command in commands),
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
        if not commands:
            return
        try:
            await message.channel.send(
                mention
                + f"🧠 **Streak {result.streak:,}: the next win reaches {target:,}. Equip Knowledge for the next battle:**\n"
                + "\n".join(f"`{command}`" for command in commands)
                + "\nA tie keeps this step open. After a win or loss, I will send the normal preset.",
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
        average_xp="Expected raw XP per battle before catch-up/Knowledge",
        battles_per_day="Expected battles per day",
        knowledge_quality="Knowledge bonus from 0.05 to 0.15; use 0 for none",
        current_streak="Current streak used for the 1,000-win switching comparison",
    )
    async def xp_calc(
        self,
        interaction: discord.Interaction,
        start_level: app_commands.Range[int, 1, MAX_LEVEL - 1],
        target_level: app_commands.Range[int, 2, MAX_LEVEL],
        highest_level: app_commands.Range[int, 1, MAX_LEVEL],
        current_xp: app_commands.Range[int, 0, 2_000_000_000] = 0,
        average_xp: app_commands.Range[float, 50.0, 10_000.0] = 350.0,
        battles_per_day: app_commands.Range[int, 1, 5760] = 1000,
        knowledge_quality: app_commands.Range[float, 0.0, 0.15] = 0.15,
        current_streak: app_commands.Range[int, 0, MAX_VALIDATED_STREAK] = 0,
    ) -> None:
        try:
            embed = self.build_calculator_embed(
                start_level=start_level,
                target_level=target_level,
                highest_level=highest_level,
                current_xp=current_xp,
                average_xp=float(average_xp),
                battles_per_day=battles_per_day,
                knowledge_quality=float(knowledge_quality),
                current_streak=current_streak,
            )
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(XPTools(bot))
