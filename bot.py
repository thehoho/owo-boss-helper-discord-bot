import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token."
    )

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

# This project uses automatic listeners and slash commands only.
# Mention-only is used internally because commands.Bot requires a prefix handler.
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


@bot.event
async def on_ready() -> None:
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as exc:
        print(f"❌ Failed to sync slash commands: {exc}")


async def load_cogs() -> None:
    await bot.load_extension("cogs.boss_generator")
    print("✅ Boss generator loaded")


async def main() -> None:
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
