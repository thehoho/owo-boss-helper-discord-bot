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

logger = logging.getLogger(__name__)

OWO_BOT_ID = 408785106942164992
TEAM_SAVE_EMOJI = "💾"
MAX_TEMPLATES_PER_USER = 10
MAX_TEMPLATE_NAME_LENGTH = 40
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_FILE = PROJECT_ROOT / "team_templates.db"

CREATE_RE = re.compile(
    r"^\s*h\s*team\s*(?:create|save)\s+(.+?)\s*$", re.IGNORECASE
)
DELETE_RE = re.compile(
    r"^\s*h\s*team\s*(?:delete|remove)\s+(.+?)\s*$", re.IGNORECASE
)
LIST_RE = re.compile(r"^\s*h\s*teams?\s*$", re.IGNORECASE)
HELP_RE = re.compile(r"^\s*h\s*team\s*help\s*$", re.IGNORECASE)

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
class TeamTemplate:
    template_id: int
    user_id: int
    name: str
    source_title: str
    members: tuple[TeamMember, ...]
    created_at: int
    updated_at: int


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
    # Custom Discord emoji forms such as <:gfish:123> and plain pasted :gfish:.
    text = re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", " ", text)
    text = re.sub(r":[A-Za-z0-9_]+:", " ", text)
    text = text.replace("`", "")
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_team_message(text: str) -> tuple[str, tuple[TeamMember, ...]] | None:
    """Extract the displayed OwO team page, including each six-character weapon ID."""
    cleaned = _clean_display_text(text)
    lowered = cleaned.lower()
    if not any(marker in lowered for marker in TEAM_MARKERS):
        return None

    section_re = re.compile(r"(?m)^\s*\[([1-3])\]\s+([^\n]+?)\s*$")
    matches = list(section_re.finditer(cleaned))
    if not matches:
        return None

    members: list[TeamMember] = []
    used_positions: set[int] = set()
    for index, match in enumerate(matches):
        position = int(match.group(1))
        if position in used_positions:
            continue
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        section = cleaned[match.end():section_end]

        animal_line = re.sub(r"[*~]", "", match.group(2)).strip()
        animal_tokens = re.findall(r"[A-Za-z0-9_'-]+", animal_line)
        if not animal_tokens:
            continue
        animal = animal_tokens[-1]

        # Weapon IDs are six uppercase alphanumeric characters on the equipment line.
        # Restricting the search to this animal's section avoids matching HP values.
        weapon_match = re.search(
            r"(?m)^\s*([A-Z0-9]{6})\b(?=.*(?:%|$))",
            section,
        )
        if not weapon_match:
            # Components can occasionally flatten lines. This fallback still requires
            # an uppercase six-character token and ignores purely numeric values.
            candidates = re.findall(r"(?<![A-Z0-9])([A-Z0-9]{6})(?![A-Z0-9])", section)
            candidates = [candidate for candidate in candidates if not candidate.isdigit()]
            if not candidates:
                continue
            weapon_id = candidates[-1]
        else:
            weapon_id = weapon_match.group(1)

        members.append(
            TeamMember(position=position, animal=animal, weapon_id=weapon_id.upper())
        )
        used_positions.add(position)

    if not members:
        return None

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
    return source_title[:100], tuple(members)


def exact_reset_commands(template: TeamTemplate) -> list[str]:
    commands = [f"wtm d {position}" for position in (1, 2, 3)]
    commands.extend(
        f"wtm a {member.animal} {member.position}" for member in template.members
    )
    commands.extend(
        f"ww {member.weapon_id} {member.position}" for member in template.members
    )
    return commands


def quick_replace_commands(template: TeamTemplate) -> list[str]:
    commands = [
        f"wtm a {member.animal} {member.position}" for member in template.members
    ]
    commands.extend(
        f"ww {member.weapon_id} {member.position}" for member in template.members
    )
    return commands


def format_command_packet(title: str, commands: Iterable[str], note: str) -> str:
    lines = [f"**{title}**", ""]
    lines.extend(f"`{command}`" for command in commands)
    lines.extend(
        (
            "",
            "⚠️ **Send the commands one at a time.** Wait for OwO to respond before "
            "sending the next command; using roughly five seconds between commands is "
            "a safe fallback when the bot is busy.",
            note,
            "Afterward, run `wtm` or `owo team` and verify all three animals and "
            "weapon IDs before battling.",
        )
    )
    return "\n".join(lines)


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
                "CREATE INDEX IF NOT EXISTS idx_team_templates_user "
                "ON team_templates(user_id, updated_at DESC)"
            )

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
                "SELECT id, created_at FROM team_templates WHERE user_id = ? AND name = ?",
                (user_id, name),
            ).fetchone()
            if existing is None:
                count = connection.execute(
                    "SELECT COUNT(*) AS total FROM team_templates WHERE user_id = ?",
                    (user_id,),
                ).fetchone()["total"]
                if int(count) >= MAX_TEMPLATES_PER_USER:
                    return None, (
                        f"You already have {MAX_TEMPLATES_PER_USER} templates. "
                        "Delete one with `H team delete <name>` before saving another."
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO team_templates
                        (user_id, name, source_title, members_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, name, source_title, members_json, now, now),
                )
                template_id = int(cursor.lastrowid)
                created_at = now
            else:
                template_id = int(existing["id"])
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
                ORDER BY updated_at DESC, name COLLATE NOCASE ASC
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

    async def delete_by_name(self, user_id: int, name: str) -> bool:
        async with self.lock:
            return await asyncio.to_thread(self._delete_by_name_sync, user_id, name)

    def _delete_by_name_sync(self, user_id: int, name: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM team_templates WHERE user_id = ? AND name = ?",
                (user_id, name),
            )
        return cursor.rowcount > 0

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
            name=str(row["name"]),
            source_title=str(row["source_title"]),
            members=members,
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "These team templates belong to another user. Send `H team` to open yours.",
            ephemeral=True,
        )
        return False


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
        packet = format_command_packet(
            f"Quick replace — {self.template.name}",
            quick_replace_commands(self.template),
            "This replaces positions directly. If your saved template contains fewer "
            "than three animals, any unmentioned position may remain unchanged.",
        )
        await interaction.response.send_message(packet, ephemeral=True)

    @discord.ui.button(label="Exact reset", emoji="🔄", style=discord.ButtonStyle.success)
    async def exact_reset(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        packet = format_command_packet(
            f"Exact reset — {self.template.name}",
            exact_reset_commands(self.template),
            "This clears all three positions first, then restores the saved animals and "
            "their exact weapon IDs.",
        )
        await interaction.response.send_message(packet, ephemeral=True)

    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete_template(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        deleted = await self.cog.store.delete_by_id(
            interaction.user.id, self.template.template_id
        )
        if deleted:
            await interaction.response.edit_message(
                content=f"🗑️ Deleted **{self.template.name}**.",
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


class TemplateSelect(discord.ui.Select):
    def __init__(self, cog: "TeamTemplates", templates: list[TeamTemplate]) -> None:
        self.cog = cog
        options: list[discord.SelectOption] = []
        for template in templates[:MAX_TEMPLATES_PER_USER]:
            animals = " / ".join(member.animal for member in template.members)
            options.append(
                discord.SelectOption(
                    label=template.name[:100],
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
                "That template no longer exists. Run `H team` again.", ephemeral=True
            )
            return

        member_lines = "\n".join(
            f"**{member.position}.** `{member.animal}` — weapon `{member.weapon_id}`"
            for member in template.members
        )
        embed = discord.Embed(
            title=f"🐾 {template.name}",
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
    ) -> None:
        super().__init__(owner_id)
        self.add_item(TemplateSelect(cog, templates))


class TeamTemplates(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = TeamTemplateStore(DATABASE_FILE)
        self.reaction_instruction_cooldowns: dict[tuple[int, int], float] = {}
        self._original_boss_help: Any = None

    async def cog_load(self) -> None:
        await self.store.initialize()
        self._patch_main_help()
        logger.info("Team template storage ready at %s", DATABASE_FILE)

    async def cog_unload(self) -> None:
        boss_cog = self.bot.get_cog("BossGenerator")
        if boss_cog is not None and self._original_boss_help is not None:
            boss_cog.send_prefix_help = self._original_boss_help

    def _patch_main_help(self) -> None:
        """Extend the existing H help command without coupling the two cog files."""
        boss_cog = self.bot.get_cog("BossGenerator")
        if boss_cog is None or not hasattr(boss_cog, "send_prefix_help"):
            logger.warning("BossGenerator was not loaded before TeamTemplates")
            return
        self._original_boss_help = boss_cog.send_prefix_help
        boss_cog.send_prefix_help = self.send_combined_help

    async def send_combined_help(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        embed = discord.Embed(
            title="🐾 OwO Boss Helper",
            description=(
                "`H` stands for **Helper** — and it also happens to be the first "
                "letter of Hassaan's name.\n\n"
                "Generate boss commands, track guild-boss timing, and save reusable "
                "OwO teams with their exact weapon IDs."
            ),
            color=0x5865F2,
        )
        embed.add_field(
            name="⚔️ Generate a Neon boss command",
            value=(
                "Send `owo boss i` or `w boss i`, then open all three pages. The "
                "helper reads pages `1/3`–`3/3`, detects HP, and sends the command."
            ),
            inline=False,
        )
        embed.add_field(
            name="⏱️ Check the guild boss",
            value=(
                "Use `H boss cd` or `H boss cooldown`. A defeated boss starts a "
                "five-minute cooldown; an escaped boss can be replaced immediately."
            ),
            inline=False,
        )
        embed.add_field(
            name="💾 Save and restore teams",
            value=(
                "Reply to an OwO team page with `H team create <name>` to save the "
                "animals, positions, and exact weapon IDs. Use `H team` to open your "
                "saved templates, or `H team help` for the complete guide."
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ Server setup",
            value=(
                "A server manager can use `/boss-cooldown-channel` to choose the "
                "guild-boss alert channel. `/boss-cooldown` checks status privately."
            ),
            inline=False,
        )
        embed.set_footer(text="Use H help anytime to show this guide.")
        await message.reply(embed=embed, mention_author=False)
        logger.info(
            "Combined helper help requested by %s in guild %s",
            message.author,
            message.guild.id,
        )

    async def send_team_help(self, message: discord.Message) -> None:
        embed = discord.Embed(
            title="💾 OwO Team Templates",
            description=(
                f"Save up to **{MAX_TEMPLATES_PER_USER}** personal team templates. "
                "Each template stores the animal, its position, and the exact "
                "six-character weapon ID."
            ),
            color=0x5865F2,
        )
        embed.add_field(
            name="1. Show the team you want",
            value="Send `wtm` or `owo team`, then navigate to the correct team page.",
            inline=False,
        )
        embed.add_field(
            name="2. Save it",
            value=(
                "Reply directly to that OwO message with `H team create <name>`. "
                "Example: `H team create boss team`. Saving the same name again updates it."
            ),
            inline=False,
        )
        embed.add_field(
            name="3. Restore it later",
            value=(
                "Send `H team`, choose a template from the dropdown, then choose "
                "**Quick replace** or **Exact reset**."
            ),
            inline=False,
        )
        embed.add_field(
            name="Other command",
            value="`H team delete <name>` removes a saved template.",
            inline=False,
        )
        embed.set_footer(
            text="Always wait for OwO's response between setup commands and verify the final team."
        )
        await message.reply(embed=embed, mention_author=False)

    async def save_from_reply(self, message: discord.Message, requested_name: str) -> None:
        name = re.sub(r"\s+", " ", requested_name).strip()
        if not name:
            await message.reply("Use `H team create <name>`.", mention_author=False)
            return
        if len(name) > MAX_TEMPLATE_NAME_LENGTH:
            await message.reply(
                f"Template names can contain at most {MAX_TEMPLATE_NAME_LENGTH} characters.",
                mention_author=False,
            )
            return
        if message.reference is None or message.reference.message_id is None:
            await message.reply(
                "Reply directly to the OwO team message you want to save, then send "
                "`H team create <name>`.",
                mention_author=False,
            )
            return

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
            return
        author_id = int((raw.get("author") or {}).get("id", 0) or 0)
        if author_id != OWO_BOT_ID:
            await message.reply(
                "That reply is not pointing to an official OwO Bot team message.",
                mention_author=False,
            )
            return

        parsed = parse_team_message(extract_all_text(raw))
        if parsed is None:
            await message.reply(
                "I could not find a team with animal names and weapon IDs in that "
                "message. Make sure you replied to the visible `wtm` / `owo team` page.",
                mention_author=False,
            )
            return

        source_title, members = parsed
        template, error = await self.store.save(
            message.author.id, name, source_title, members
        )
        if error:
            await message.reply(f"⚠️ {error}", mention_author=False)
            return
        assert template is not None

        member_lines = "\n".join(
            f"**{member.position}.** `{member.animal}` — `{member.weapon_id}`"
            for member in template.members
        )
        embed = discord.Embed(
            title=f"✅ Team saved: {template.name}",
            description=(
                f"{member_lines}\n\nUse `H team` whenever you want to restore it."
            ),
            color=0x57F287,
        )
        await message.reply(embed=embed, mention_author=False)
        logger.info(
            "Saved team template %s for user %s with %s member(s)",
            template.template_id,
            message.author.id,
            len(template.members),
        )

    async def show_templates(self, message: discord.Message) -> None:
        templates = await self.store.list_for_user(message.author.id)
        if not templates:
            await message.reply(
                "You do not have any saved teams yet. Reply to an OwO `wtm` / "
                "`owo team` message with `H team create <name>`.",
                mention_author=False,
            )
            return

        embed = discord.Embed(
            title="🐾 Your saved OwO teams",
            description=(
                f"You have **{len(templates)}/{MAX_TEMPLATES_PER_USER}** templates. "
                "Choose one below to view its animals and exact weapon IDs."
            ),
            color=0x5865F2,
        )
        await message.reply(
            embed=embed,
            view=TemplateListView(self, message.author.id, templates),
            mention_author=False,
            delete_after=300,
        )

    async def delete_template(self, message: discord.Message, requested_name: str) -> None:
        name = re.sub(r"\s+", " ", requested_name).strip()
        deleted = await self.store.delete_by_name(message.author.id, name)
        if deleted:
            await message.reply(f"🗑️ Deleted **{name}**.", mention_author=False)
            logger.info("Deleted team template named %s for user %s", name, message.author.id)
        else:
            await message.reply(
                f"I could not find a saved template named **{name}**.",
                mention_author=False,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            if message.author.bot:
                if message.author.id != OWO_BOT_ID or message.guild is None:
                    return
                text = extract_message_text(message)
                if parse_team_message(text) is not None:
                    try:
                        await message.add_reaction(TEAM_SAVE_EMOJI)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                return

            if message.guild is None:
                return
            content = message.content or ""

            match = CREATE_RE.match(content)
            if match:
                await self.save_from_reply(message, match.group(1))
                return

            match = DELETE_RE.match(content)
            if match:
                await self.delete_template(message, match.group(1))
                return

            if HELP_RE.match(content):
                await self.send_team_help(message)
                return

            if LIST_RE.match(content):
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
        try:
            await channel.send(
                f"{mention} Reply to this OwO team message with "
                "`H team create <name>` to save its animals and exact weapon IDs. "
                "Use `H team help` for the full guide.",
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
