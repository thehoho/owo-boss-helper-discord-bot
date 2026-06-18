# OwO Boss Helper

A focused Discord bot that automatically:

- Reads `owo boss i` and `w boss i` boss-inventory pages.
- Keeps bosses in OwO's authoritative `1/3`, `2/3`, `3/3` order even when users navigate out of order.
- Extracts current HP from each individual boss image using bundled pixel templates.
- Generates a mobile-friendly Neon battle command using inline code.
- Tracks the latest active guild-boss status message in each configured server.
- Checks that message every 15 seconds and announces the five-minute cooldown and ready state after a defeat or escape.
- Persists the selected cooldown channel and active watcher state across restarts.

Made by Hassaan.

> This is an independent community project. It is not affiliated with Discord, OwO Bot, or NeonUtil.

## Public bot vs. self-hosting

People who only want to use your running bot should receive its Discord installation link. They do not need this source code or your token.

People who clone this repository for learning or self-hosting must create their own Discord application and use their own bot token.

## Requirements

- Python 3.11 or newer
- A Discord bot application
- Message Content Intent enabled for that bot

## Setup on Windows

```bat
cd /d D:\owo-boss-helper-discord-bot
py -m pip install -r requirements.txt
copy .env.example .env
notepad .env
py bot.py
```

Set your private token in `.env`:

```env
DISCORD_TOKEN=your_real_bot_token_here
```

Never upload `.env`, share your token, or paste it into an issue. If a token is exposed, reset it immediately in the Discord Developer Portal.

## Discord permissions

The bot needs access to the channels where OwO is used:

- View Channels
- Send Messages
- Embed Links
- Read Message History
- Add Reactions

The Discord installation scopes should include:

- `bot`
- `applications.commands`

Enable **Message Content Intent** in the Discord Developer Portal.

## Slash commands

### `/boss-cooldown-channel`

Selects the channel that receives automatic guild-boss cooldown and ready alerts. The command requires Manage Server permission by default.

### `/boss-cooldown`

Privately displays the current cooldown state.

## Boss command generation

The automatic trigger accepts only these commands after whitespace and capitalization are normalized:

```text
owo boss i
owobossi
w boss i
wbossi
```

Open all three OwO pages. The bot reads the visible page counter and always generates the final command in `1/3 → 2/3 → 3/3` order.

The final command is shown with one backtick on each side for easier mobile copying. There is no Change HP button; HP is read automatically from the individual boss images, with a safe default when a value cannot be confirmed.

## Files that stay private

These files are intentionally ignored by Git:

```text
.env
boss_cooldown_config.json
prefix_config.json
__pycache__/
*.pyc
```

`boss_cooldown_config.json` is generated locally and stores each server's selected cooldown channel and watcher state. Do not commit it.

## Project structure

```text
owo-boss-helper-discord-bot/
├── assets/
│   └── hp_digits/
├── cogs/
│   ├── __init__.py
│   └── boss_generator.py
├── .env.example
├── .gitignore
├── bot.py
├── LICENSE
├── README.md
└── requirements.txt
```

## Updating

After testing a change locally:

```bat
git status
git add .
git commit -m "Describe the update"
git push
```

Keep `.env` and `boss_cooldown_config.json` only on the host machine.

## License

MIT License. See [LICENSE](LICENSE).
