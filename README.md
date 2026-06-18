# OwO Boss Helper

A focused Discord bot that helps with OwO guild-boss fights by generating Neon battle commands and tracking guild-boss timing.

## Features

- Automatically reads `owo boss i` and `w boss i`.
- Keeps bosses in OwO's authoritative `1/3`, `2/3`, `3/3` order.
- Extracts current HP from each individual boss image using bundled digit templates.
- Generates a mobile-friendly Neon command using inline code.
- Tracks the latest active guild-boss status message in each configured server.
- Checks the latest tracked boss message every **15 seconds**.
- Announces when a new guild boss appears.
- Announces the five-minute cooldown after a defeat or escape.
- Announces when the cooldown ends.
- Supports both slash commands and the lightweight `H` helper prefix.
- Persists the selected notification channel and active watcher state across restarts.
- Writes rotating runtime logs to `logs/bot.log`.

Made by Hassaan.

## Credits

Special thanks to **Pencilvester** for sharing the original exact-command parsing logic and the weapon/passive rarity ranges that helped form the foundation of the command generator. The project has since been substantially expanded, adapted, and integrated into this Discord bot.

> This is an independent community project. It is not affiliated with Discord, OwO Bot, or NeonUtil.

## Requirements

- Python 3.11 or newer
- A Discord bot application
- Message Content Intent enabled

## Setup on Windows

```bat
cd /d D:\owo-boss-helper-discord-bot
py -m pip install -r requirements.txt
copy .env.example .env
notepad .env
py bot.py
```

Put your private bot token in `.env`:

```env
DISCORD_TOKEN=your_real_bot_token_here
```

Never upload `.env` or share your token.

## Discord permissions

The bot needs:

- View Channels
- Send Messages
- Embed Links
- Read Message History
- Add Reactions

Installation scopes:

- `bot`
- `applications.commands`

## Commands

### Boss command generator

Run either:

```text
owo boss i
w boss i
```

Open all three boss pages. The bot reads the visible page counter and emits the final command in `1/3 → 2/3 → 3/3` order.

### Public cooldown status

```text
H boss cd
H boss cooldown
```

Whitespace and capitalization are ignored. These commands show one of:

- Active boss and its escape time
- Running five-minute cooldown
- Ready state

### Slash commands

```text
/boss-cooldown-channel
/boss-cooldown
```

`/boss-cooldown-channel` selects where automatic new-boss, cooldown, and ready alerts are sent. It requires Manage Server permission by default.

## Logging

Runtime activity is written to:

```text
logs/bot.log
```

Logs rotate automatically at 5 MB, with up to five backup files:

```text
bot.log
bot.log.1
bot.log.2
...
```

The logs include startup, boss-page captures, HP detection, generated commands, active-boss tracking, cooldown events, alerts, and errors. Tokens and `.env` contents are never logged.

## Private runtime files

These remain local and are ignored by Git:

```text
.env
boss_cooldown_config.json
logs/
__pycache__/
*.pyc
```

## Project structure

```text
owo-boss-helper-discord-bot/
├── assets/
│   └── hp_digits/
├── cogs/
│   ├── __init__.py
│   └── boss_generator.py
├── logs/                  # created automatically
├── .env.example
├── .gitignore
├── bot.py
├── CHANGELOG.md
├── LICENSE
├── README.md
└── requirements.txt
```

## License

MIT License. See [LICENSE](LICENSE).
