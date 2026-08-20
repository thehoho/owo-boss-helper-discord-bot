"""Small, isolated random-number commands for community rolls."""

from __future__ import annotations

import logging
import re
import secrets
from decimal import Decimal, InvalidOperation

import discord
from discord import app_commands
from discord.ext import commands

from .helper_prefix import (
    get_guild_helper_prefix,
    helper_alias,
    parse_helper_command_argument,
)

logger = logging.getLogger(__name__)

RNG_PREFIX_ALIASES = {
    "hrng",
    "h rng",
    "hrandom",
    "h random",
}
MAX_ABSOLUTE_BOUND = 10**18
SUFFIX_MULTIPLIERS = {
    "": Decimal(1),
    "k": Decimal(1_000),
    "m": Decimal(1_000_000),
    "b": Decimal(1_000_000_000),
    "t": Decimal(1_000_000_000_000),
}
NUMBER_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*([kmbt]?)$", re.I)
GROUPED_INTEGER_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+$")
BOUND_TOKEN_RE = re.compile(
    r"[+-]?(?:(?:\d{1,3}(?:,\d{3})+)|(?:\d+(?:\.\d+)?|\.\d+))\s*[kmbt]?",
    re.I,
)


class RngInputError(ValueError):
    """Raised when a requested random range cannot be interpreted safely."""


def parse_compact_integer(value: str) -> int:
    """Parse whole numbers and K/M/B/T abbreviations without float rounding."""
    cleaned = (value or "").strip().replace("_", "")
    if GROUPED_INTEGER_RE.fullmatch(cleaned):
        cleaned = cleaned.replace(",", "")
    match = NUMBER_RE.fullmatch(cleaned)
    if match is None:
        raise RngInputError(f"`{value}` is not a valid number.")
    try:
        result = Decimal(match.group(1)) * SUFFIX_MULTIPLIERS[match.group(2).casefold()]
    except (InvalidOperation, KeyError):
        raise RngInputError(f"`{value}` is not a valid number.") from None
    if result != result.to_integral_value():
        raise RngInputError("RNG bounds must resolve to whole numbers.")
    integer = int(result)
    if abs(integer) > MAX_ABSOLUTE_BOUND:
        raise RngInputError(
            f"RNG bounds must stay between {-MAX_ABSOLUTE_BOUND} and {MAX_ABSOLUTE_BOUND}."
        )
    return integer


def split_rng_bounds(argument: str) -> list[str]:
    text = re.sub(
        r"\b(?:minimum|min|maximum|max)\b\s*[:=]?",
        " ",
        argument or "",
        flags=re.I,
    ).strip()
    if not text:
        return []
    matches = list(BOUND_TOKEN_RE.finditer(text))
    remainder = BOUND_TOKEN_RE.sub("", text)
    if remainder.strip(" ,"):
        return []
    return [match.group(0).strip() for match in matches]


def parse_rng_range(argument: str) -> tuple[int, int]:
    """Return inclusive bounds; one supplied number means 1 through that maximum."""
    parts = split_rng_bounds(argument)
    if len(parts) not in {1, 2}:
        raise RngInputError("Provide one maximum, or a minimum and maximum.")
    values = [parse_compact_integer(part) for part in parts]
    if len(values) == 1:
        minimum, maximum = 1, values[0]
    else:
        minimum, maximum = values
    if minimum > maximum:
        raise RngInputError("The minimum cannot be greater than the maximum.")
    return minimum, maximum


def roll_inclusive(minimum: int, maximum: int) -> int:
    return minimum + secrets.randbelow(maximum - minimum + 1)


def rng_usage(helper_prefix: str = "h") -> str:
    short = helper_alias(helper_prefix, "hrng")
    return (
        f"Use `{short} <maximum>` or `{short} <minimum>, <maximum>`. "
        f"Examples: `{short} 1M`, `{short} 100K, 2.5M`. "
        "K, M, B, and T abbreviations are accepted."
    )


class RandomNumber(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        helper_prefix = await get_guild_helper_prefix(message.guild.id)
        argument = parse_helper_command_argument(
            message.content or "",
            helper_prefix,
            RNG_PREFIX_ALIASES,
        )
        if argument is None:
            return
        try:
            minimum, maximum = parse_rng_range(argument)
        except RngInputError as exc:
            await message.reply(
                f"⚠️ {exc}\n{rng_usage(helper_prefix)}",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        result = roll_inclusive(minimum, maximum)
        await message.reply(
            str(result),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        logger.info(
            "RNG roll requested by user %s in guild %s (%s..%s)",
            message.author.id,
            message.guild.id,
            minimum,
            maximum,
        )

    @app_commands.command(name="rng", description="Pick a random whole number in an inclusive range.")
    @app_commands.describe(
        maximum="Maximum value, such as 100K, 1M, or 2.5M",
        minimum="Optional minimum; defaults to 1",
    )
    async def rng(
        self,
        interaction: discord.Interaction,
        maximum: str,
        minimum: str | None = None,
    ) -> None:
        try:
            upper = parse_compact_integer(maximum)
            lower = parse_compact_integer(minimum) if minimum is not None else 1
            if lower > upper:
                raise RngInputError("The minimum cannot be greater than the maximum.")
        except RngInputError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(str(roll_inclusive(lower, upper)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RandomNumber(bot))
