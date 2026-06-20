# OwO Boss Helper

A focused Discord bot that helps with OwO guild-boss fights by generating Neon battle commands and tracking guild-boss timing.

## Features

- Automatically reads `owo boss i` and `w boss i`.
- Keeps bosses in OwO's authoritative `1/3`, `2/3`, `3/3` order.
- Extracts current HP from each individual boss image using bundled digit templates.
- Sends the generated Neon command as a normal inline-code message for easier mobile copying.
- Tracks the latest active guild-boss status message in each configured server.
- Uses Discord gateway payloads for discovery instead of fetching every OwO response.
- Checks only the single latest tracked boss message every **15 seconds**.
- Announces when a new guild boss appears.
- Announces the five-minute cooldown after a defeat.
- Marks the guild ready immediately after an escape.
- Announces when a defeat cooldown ends.
- Supports both slash commands and the lightweight `H` helper prefix.
- Persists the selected notification channel and active watcher state across restarts.
- Writes rotating runtime logs to `logs/bot.log`.

Made by Hassaan.

## Credits

Special thanks to **Pencilvester** for:

- Sharing the original exact-command parsing logic and weapon/passive rarity ranges that helped form the foundation of the boss command generator.
- Suggesting the original saved team-template concept that inspired the team-management system.

The project has since been substantially expanded with automatic boss reading, HP detection, guild-boss tracking, exact weapon-ID preservation, numbered team slots, and guided team restoration.

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

## Team-template shortcuts and guided setup

Users can save up to **25** personal OwO team templates. Each template receives a stable number from 1–25 and stores every animal position together with its exact six-character weapon ID.

### Save a team

Run `wtm` or `owo team`, open the correct team page, then reply directly to that OwO message with one of these:

- `HT C <name>`
- `HTC <name>`
- `HTM C <name>`
- `H team create <name>`

Saving the same name updates the existing team without changing its number.

### Open saved teams

- `HT`, `HTM`, or `H team` — open the numbered dropdown.
- `HT3`, `HTM3`, or `H team 3` — open team #3 directly.

Team numbers remain stable after edits. Deleting a team frees that number for a future template.

### Delete teams

- `HT D 3` or `HTD 3` — delete team #3.
- `HT D <name>` — delete by name.
- `H team delete <number or name>` — full command.

### Guided team restoration

Selecting **Quick replace** or **Exact reset** now starts guided mode:

1. The complete command packet is shown privately as a backup.
2. The helper posts the first command in the channel.
3. The user sends that exact command.
4. The helper waits for OwO to confirm it.
5. After a five-second safety delay, the helper posts the next command.
6. After the final confirmed step, the helper posts `wtm` for verification.

Guided sessions are isolated by server, channel, and user, so multiple people can restore teams at the same time. Use `HT cancel` to stop your current session.

The helper does not send commands to OwO on a user's behalf. It only presents and advances the commands after confirmation.

### Storage

Templates remain in the local `team_templates.db` SQLite database. Runtime diagnostics remain in rotating text files under `logs/`; logs are not moved into the database because they are append-only operational records rather than structured user data.

## Faster guided team restoration

Guided team setup now alternates each animal add with that animal's weapon equip:

```text
wtm a hsnake 1
ww AZWWZV 1
wtm a 2025dec_daisy 2
ww DYLYU5 2
wtm a 2026feb_huba 3
ww EEK29J 3
```

The next command appears immediately after OwO confirms the current command. There is no artificial five-second helper delay.

### Skip a step

During an active guided setup, use either:

```text
HS
H skip
H escape
HT skip
```

You can also press the **Skip step** button under the current command. This is useful when the animal is already in the correct position or the correct weapon is already equipped.

### Animal conflicts

OwO may briefly respond with `This animal is already in your team!`. The helper catches this short-lived response immediately and keeps the same guided step active.

If the animal is in the wrong position, remove it directly by name:

```text
wtm d <animal>
```

Then resend the displayed add command. If the animal is already in the correct position, press **Skip step** or use `HS`, `H skip`, or `H escape`.

## Renamed and custom pet support

Team templates now identify each animal from the first OwO animal emoji alias on the team card instead of trusting the visible pet nickname.

This means renamed pets such as:

```text
:gspider: just
:gfish: f i s h
:hlizard: l i z a r d
```

are saved as:

```text
spider
fish
lizard
```

Known standard animals automatically drop their rank prefix when OwO accepts the normal animal name. Examples include `gfish → fish`, `gspider → spider`, `llion → lion`, `deagle → eagle`, and `hlizard → lizard`.

Unknown custom or event pet aliases are preserved exactly instead of being guessed. For example, `:custompet231:` is stored as `custompet231` and used in the guided `wtm a` command.

Templates saved before this update with renamed pets must be saved again from their OwO team page because the old database record contains only the incorrect visible nickname and cannot recover the original emoji alias.

### Cleaner guided channels

After OwO responds, the helper attempts to delete the user's completed command message. This cleanup is optional and requires **Manage Messages**. Without that permission, guided setup still works normally and the command remains visible.

The helper also removes its previous step prompt after the user sends the expected command.

### Exact reset note

**Quick replace** alternates animal and weapon commands immediately. **Exact reset** must still clear positions 1–3 before rebuilding the team so animals can safely move between slots. If OwO applies a temporary delete cooldown, the helper keeps the same step active for retrying.

## Rate-limit-friendly tracking

The bot does not REST-fetch every OwO message in busy grinding channels.

- While a boss is active, incoming gateway payloads are inspected locally so newer status cards can replace the tracked message.
- During a five-minute defeat cooldown, unrelated OwO traffic is ignored because a new boss cannot appear.
- When the guild is ready to spawn, gateway payloads are inspected locally for the next boss card.
- Only the one currently tracked boss message is fetched every 15 seconds.
- The three-page generator may make a small, bounded number of fetches after a user explicitly runs `owo boss i` or `w boss i`.

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
cogs/
├── __init__.py
├── boss_generator.py
└── team_templates.py
│── team_templates.db        # created automatically, ignored by Git
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
