"""Public information card for the separate TapDeck Lite Android project."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .helper_prefix import get_guild_helper_prefix, parse_helper_command_argument

logger = logging.getLogger(__name__)

TAPDECK_GOOGLE_PLAY_URL = (
    "https://play.google.com/store/apps/details?id=app.tapdeck.keyboard.lite"
)
TAPDECK_PREFIX_ALIASES = {
    "h grind",
    "hgrind",
    "h tapdeck",
    "htapdeck",
}


class TapDeckLinks(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Get it on Google Play",
                emoji="📲",
                url=TAPDECK_GOOGLE_PLAY_URL,
            )
        )


def build_tapdeck_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📱 TapDeck Lite, one tap, one command",
        url=TAPDECK_GOOGLE_PLAY_URL,
        description=(
            "An offline Android shortcut keyboard for command-based chats and bots. "
            "Build a customizable deck of up to 40 keys, then insert or send a "
            "saved command with one tap."
        ),
        color=0x70E1B5,
    )
    embed.add_field(
        name="How it helps",
        value=(
            "• Save up to 40 commands, with up to 4,000 characters per key.\n"
            "• Choose **Insert only** or **Insert + send** per key.\n"
            "• Set global or per-key send delays and cooldowns.\n"
            "• Customize the keyboard, buttons, borders, and colors.\n"
            "• Use guarded slash-command sending with an optional retry.\n"
            "• Keep an optional 24-hour history of 20 copied texts."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy and transparency",
        value=(
            "Google Play declares **no data collected or shared**. The app works "
            "offline, has no ads, analytics, accounts, or tracking, and requests no "
            "Android permissions. Commands and optional clipboard history stay in "
            "private local storage, with cloud backup disabled."
        ),
        inline=False,
    )
    embed.add_field(
        name="Rules and installation",
        value=(
            "The demonstrated **one tap = one command** workflow was shared with "
            "OwO's staff team for review and confirmed as allowed under the rules at "
            "the time. Rules can change, so users remain responsible for following "
            "current OwO and Discord rules.\n\n"
            "Install TapDeck Lite from **Google Play** using the button below."
        ),
        inline=False,
    )
    embed.set_footer(
        text="Independent project by Hassaan • Not affiliated with Discord or OwO Bot"
    )
    return embed


class TapDeckInfo(commands.Cog):
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
            TAPDECK_PREFIX_ALIASES,
        )
        if argument is None:
            return
        if argument:
            await message.reply(
                "This command does not need any options. Use it by itself to open the TapDeck Lite card.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await message.reply(
            embed=build_tapdeck_embed(),
            view=TapDeckLinks(),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        logger.info(
            "TapDeck information requested by user %s in guild %s",
            message.author.id,
            message.guild.id,
        )

    @app_commands.command(name="tapdeck", description="Learn about and download TapDeck Lite for Android.")
    async def tapdeck(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_tapdeck_embed(),
            view=TapDeckLinks(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TapDeckInfo(bot))
