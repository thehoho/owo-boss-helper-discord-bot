# OwO Boss Helper

A community Discord bot for OwO guild-boss fights. It generates Neon battle commands, tracks guild-boss timing, saves reusable teams with exact weapon IDs, and maintains a per-server boss-ticket board.

Developed and maintained by **Hassaan**.

Use `H about` or `/about` inside Discord for the public project profile.

> Independent community project. Not affiliated with Discord, OwO Bot, or NeonUtil.

## Features

### Boss command generator

- Automatically reacts to `owo boss i` and `w boss i`.
- Reads all three boss pages in OwO's visible `1/3 → 2/3 → 3/3` order.
- Extracts current HP from each individual boss image using bundled digit templates.
- Generates the finished Neon battle command as normal inline-code text for easier mobile copying.
- Handles touching HP digits and validates suspicious readings.
- Uses current Bleeding Gaze blueprint ordering with Weapon Cost last.

### Guild-boss tracking

- Detects newly appeared guild bosses.
- Tracks only the latest active guild-boss message.
- Polls that one message every 15 seconds instead of fetching every OwO response.
- Shows the active boss's escape time using Discord timestamps.
- Reconciles stale active-boss state before responding to status checks, ignoring late OwO cards and marking unavailable tracked messages as unconfirmed.
- Starts a five-minute cooldown only after a defeat.
- Marks the guild ready immediately after an escape.
- Sends cooldown-complete alerts.
- Persists configured channels and active watcher state across restarts.
- Includes duplicate outcome protection and per-guild request locking.

### Team templates

- Saves up to 100 personal templates per Discord user.
- Paginates the `HT` template selector in groups of 25 while direct shortcuts such as `HT73` continue to open a known slot immediately.
- Stores stable slot numbers from 1–100.
- Preserves animal positions and exact six-character weapon IDs.
- Reads animal identity from the OwO emoji alias rather than a renameable nickname.
- Normalizes standard aliases such as `gfish → fish`, `gspider → spider`, and `hlizard → lizard`.
- Preserves unknown custom-pet aliases exactly.
- Supports Quick replace and Exact reset.
- Guides users one command at a time and waits for OwO's response before advancing.
- Alternates animal-add and weapon-equip commands for faster restoration.
- Supports concurrent guided sessions by server, channel, and user.
- Handles already-present animals, occupied positions, missing weapons, retries, skips, and cancellations.
- Can optionally clean completed command messages when the bot has **Manage Messages**.

### Boss-ticket board

- Watches explicit ticket checks:
  - `owo boss t`
  - `owo boss ticket`
  - `w boss t`
  - `w boss ticket`
- Associates each OwO response with the requesting Discord user.
- Stores reported `0/3`–`3/3` counts per server.
- Maintains one persistent board in a configured channel.
- Shows username, Discord user ID, ticket count, update time, and next replenishment.
- Keeps the persistent board and manual ticket-list responses in a single Discord message, with Previous and Next buttons for large lists.
- Provides a visual ticket management panel for server managers, including remove, block tracking, and unblock actions.
- Supports paginated managed user selection for servers with more than 25 tracked or blocked users.
- Uses `America/Los_Angeles` so Pacific daylight-saving changes are handled automatically.
- Supports manual list display and board refresh commands.
- Reads Components V2 ticket responses from both message-create and message-edit events, with a bounded raw-message fallback only after an explicit ticket request.

### Developer information and operational statistics

Public commands:

```text
H about
/about
```

The public profile identifies Hassaan as the developer, shows the current version, summarizes the bot, and links to the source repository.

Owner-only commands configured through `BOT_OWNER_ID`:

```text
/bot-stats
/bot-servers
```

They show current and historical server counts, approximate member reach, per-server usage, saved-template counts, ticket-board counts, uptime, latency, and local storage size. The bot also sends the configured owner a private message when it joins or leaves a server. Persistent operational metadata is stored in `bot_stats.db`.

### Logging

- Writes runtime activity to `logs/bot.log`.
- Rotates at 5 MB with up to five backup files.
- Does not log the bot token or `.env` contents.

## Credits

Special thanks to **Pencilvester** for:

- Sharing the original exact-command parsing logic and weapon/passive rarity ranges that helped form the foundation of the boss command generator.
- Suggesting the original saved team-template concept that inspired the team-management system.


The project has since been substantially expanded with automatic boss reading, HP detection, rate-limit-friendly guild-boss tracking, exact weapon-ID preservation, numbered team slots, guided restoration, and boss-ticket tracking.

## Requirements

- Python 3.11 or newer
- A Discord bot application
- Message Content Intent enabled

Install dependencies:

```bat
py -m pip install --upgrade -r requirements.txt
```

## Setup on Windows

```bat
cd /d D:\owo-boss-helper-discord-bot
copy .env.example .env
notepad .env
py bot.py
```

Add your private token to `.env`:

```env
DISCORD_TOKEN=your_real_bot_token_here
```

Never upload `.env` or share the token.

## Discord installation

Recommended scopes:

- `bot`
- `applications.commands`

Required permissions:

- View Channels
- Send Messages
- Embed Links
- Read Message History
- Add Reactions

Optional permission:

- Manage Messages — allows guided team setup to remove completed user command messages.

The bot does not require Administrator permission.

## Commands

### Boss generator

```text
owo boss i
w boss i
```

Open all three pages. The helper captures each visible page number and emits the final command in the correct order.

### Guild-boss status

```text
H boss cd
H boss cooldown
/boss-cooldown
```

Possible states:

- Guild boss active, with escape time
- Five-minute defeat cooldown
- Ready for a new boss

Server setup:

```text
/boss-cooldown-channel
```

This selects the channel for new-boss, defeat, escape, cooldown, and ready alerts.

### Help

```text
H help
```

`H help` now lists only commands currently supported by the bot, including the ticket-management panel and the 100-template workflow.

## Team templates

### Save a team

Run `wtm` or `owo team`, open the correct team page, and reply directly to the OwO message:

```text
HT C <name>
HTC <name>
HTM C <name>
H team create <name>
```

Saving the same name updates that template without changing its stable slot.

### Update a team by slot or name

Reply to a fresh OwO team page:

```text
HT U 3
HTU 3
HT U boss team
H team update boss team
```

The helper shows the previous and updated animals and weapon IDs.

### Open templates

```text
HT
HTM
H team
```

Open a specific slot directly:

```text
HT3
HTM3
H team 3
```

### Delete templates

```text
HT D 3
HTD 3
HT D <name>
H team delete <number or name>
```

### Guided restoration

Choose one of the buttons on a saved team:

- **Quick replace** — overwrites listed positions and equips each saved weapon.
- **Exact reset** — clears positions 1–3, then rebuilds the team.

Quick replace alternates commands:

```text
wtm a hsnake 1
ww AZWWZV 1
wtm a 2025dec_daisy 2
ww DYLYU5 2
wtm a 2026feb_huba 3
ww EEK29J 3
```

The next command appears immediately after OwO confirms the current one.

Skip the current step:

```text
HS
H skip
H escape
HT skip
```

Stop the setup:

```text
HT cancel
```

The prompt also provides owner-only **Skip step** and **Cancel** buttons.

### Team validation

- All three animal positions are required.
- Animals with missing weapons are preserved and shown in a confirmation prompt.
- Saving without a missing weapon omits only that position's invalid `ww` command.
- Renamed animals use the OwO emoji alias, not the visible nickname.
- Previously saved templates containing incorrect custom nicknames should be saved again from a fresh OwO team page.

## Boss tickets

### Configure the persistent board

A server manager runs:

```text
/boss-ticket-channel
```

Choose the channel where the board should be created and maintained.

### Visual ticket management

A server manager can open the visual ticket-user panel with:

```text
H boss settings
HBS
/boss-ticket-manage
```

The panel supports:

- **Remove from list** — deletes the current board entry, but the user can reappear after their next OwO ticket check.
- **Block tracking** — removes the user and ignores their future ticket checks in that server.
- **Unblock** — allows future ticket checks to be recorded again.
- Paginated user selection for servers with more than 25 tracked or blocked users.

The existing `/boss-ticket-remove` command remains available for direct removal by Discord user ID or mention.

### Update a user's count

A user runs any ticket command anywhere the helper can read messages:

```text
owo boss t
owo boss ticket
w boss t
w boss ticket
```

The tracker records only the ticket count OwO actually reports. It does not infer ticket usage from battles or other servers.

### Show and refresh the complete list

Public text commands:

```text
H boss list
H boss t
HBL
```

The helper intentionally keeps only these focused aliases. Older experimental aliases such as `H buzz t`, `H ticket list`, `T list`, and `HTL` are not supported.

Private slash command:

```text
/boss-ticket-list
```

These commands display the current list wherever they are used. The configured persistent board is replaced automatically whenever a member updates their ticket count.

### Ticket replenishment

Ticket cycles follow midnight in:

```text
America/Los_Angeles
```

At the daily reset, every **previously tracked member** is replenished to `3/3` and remains on the board. A later `w boss t` or `owo boss t` check replaces that reset value with the real count reported by OwO.

Discord timestamps display the corresponding local time for every viewer.

### Remove an old member

A server manager can remove a stale entry, including somebody who already left the server:

```text
/boss-ticket-remove
```

Enter the Discord user ID shown on the ticket board, or paste a user mention. The persistent board is replaced immediately after removal.

## Storage

Runtime state remains local:

```text
.env
boss_cooldown_config.json
team_templates.db
team_templates.db-shm
team_templates.db-wal
boss_tickets.db
boss_tickets.db-shm
boss_tickets.db-wal
bot_stats.db
bot_stats.db-shm
bot_stats.db-wal
logs/
```

These files are ignored by Git.

When moving the bot to another computer or host, copy `team_templates.db`, `boss_tickets.db`, `bot_stats.db`, and `boss_cooldown_config.json` to preserve saved teams, ticket data, developer statistics, board configuration, and boss watcher state.

## Project structure

```text
owo-boss-helper-discord-bot/
├── assets/
│   └── hp_digits/
├── cogs/
│   ├── __init__.py
│   ├── boss_generator.py
│   ├── team_templates.py
│   ├── ticket_tracker.py
│   └── bot_info.py
├── deploy/
│   ├── backup.sh
│   └── owo-boss-helper.service
├── logs/                         # created automatically
├── .env.example
├── .gitignore
├── bot.py
├── CHANGELOG.md
├── DEPLOYMENT_DIGITALOCEAN.md
├── LICENSE
├── PRIVACY.md
├── README.md
├── RELEASE_NOTES_v0.8.0-beta.md
├── TERMS.md
└── requirements.txt
```

Generated locally and ignored by Git:

```text
boss_cooldown_config.json
team_templates.db
boss_tickets.db
bot_stats.db
```

## Updating

Stop the running bot, replace the updated source files, then run:

```bat
py -m pip install --upgrade -r requirements.txt
rmdir /s /q cogs\__pycache__ 2>nul
py bot.py
```

Do not delete `.env`, `boss_cooldown_config.json`, `team_templates.db`, `boss_tickets.db`, `bot_stats.db`, or `logs/` during an update.

## Production hosting

For a 24/7 Linux VPS deployment with automatic restarts and protected local storage, follow [DEPLOYMENT_DIGITALOCEAN.md](DEPLOYMENT_DIGITALOCEAN.md). Deployment helpers are included under `deploy/`.

## Privacy and terms

- [Privacy Policy](PRIVACY.md)
- [Terms of Use](TERMS.md)

## License

MIT License. See [LICENSE](LICENSE).
