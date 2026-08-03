
## v0.13.0-beta notes

The helper now learns public animal facts from official OwO Dex responses, includes all 173 Special/event animals, and can return a cached Dex when OwO cannot find an animal in the requesting member's zoo. The bot owner can grant trusted expert access for a visual, versioned team-guide editor. HIT and SKIP stickies both keep one random usable server emoji, and the HW guide now explains Neon's `/weapon inv check` reconciliation step.

# OwO Boss Helper

A community Discord bot for OwO guild-boss fights. It generates Neon battle commands, tracks guild-boss timing, saves reusable teams with exact weapon IDs, scans Neon weapon pages for dex queues, maintains a public animal Dex catalog, publishes trusted expert team guides, and maintains a per-server boss-ticket board.

Developed and maintained by **Hassaan**.

Use `H about` or `/about` inside Discord for the public project profile.

The default helper prefix is `h`. A server manager can change it with `/helper-prefix` or `H prefix <new prefix>`. Run `/setup-guide` after changing it; examples shown by the bot automatically use that server's current prefix.

> Independent community project. Not affiliated with Discord, OwO Bot, or NeonUtil.

## Features

### Boss command generator

- Automatically reacts to `owo boss i` and `w boss i`.
- Reads all three boss pages in OwO's visible `1/3 → 2/3 → 3/3` order.
- Extracts current HP from each individual boss image using bundled digit templates.
- Generates the finished Neon battle command as normal inline-code text for easier mobile copying.
- Handles two or more touching HP digits, including three-digit groups such as `444` and `744`, and validates suspicious readings.
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
- Posts new-boss announcements without pinging decision roles or individual members.
- Sends a persistent fighter-role alert once when an active boss is marked HIT.
- Posts a daily confirmed HIT/SKIP outcome report at the Pacific-midnight reset in a manager-configured channel.
- Persists up to seven failed daily reports for retry instead of discarding counts during a temporary Discord/channel failure.
- Adds one stable random usable custom emoji from the current server when a helper marks an active boss HIT or SKIP; servers without a usable custom emoji keep a plain sticky.

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
- Equips every saved weapon by the saved animal identifier, such as `ww ALGOB8 snail`, instead of relying on a changeable team position.
- Supports concurrent guided sessions by server, channel, and user.
- Handles already-present animals, occupied positions, missing weapons, retries, skips, and cancellations.
- Can optionally clean completed command messages when the bot has **Manage Messages**.

### Public animal Dex catalog

- Watches strict animal-Dex responses from the official OwO bot ID and stores public game facts in `animal_dex.db`.
- Stores animal names, ranks, aliases, global caught totals, economy values, six base stats, descriptions, and public image/emoji metadata.
- Deliberately does not store the requesting member's personal zoo count, original command content, or source message/channel/server IDs.
- Seeds all 173 Special/event animals from the checked-in OwO Wiki catalog and learns newer official OwO responses as they are seen.
- Searches with `H animal dex <animal>` or `/animal-dex`. The existing `H dex` command remains reserved for Neon's weapon-dex queue.
- If OwO refuses or does not answer because the requesting member does not own the animal, the helper replies with its latest cached public record when available.

### Trusted team guides

- The bot owner grants or revokes global trusted-expert access with `/guide-expert`.
- Trusted experts create guides with `/team-guide-create` and revise them with `/team-guide-edit`.
- The private editor uses Discord modals and buttons for guide basics, unique aliases, user-created categories, displayed authors, viability, ease of creation, and all three composition slots.
- Composition slots support optional animal/weapon ranks, levels, up to three weapon specifications, passives, and notes using the checked-in animal, weapon, passive, and tier emoji catalogs.
- Search aliases are globally unique, edits increment the guide version, and the last editor is recorded.
- Everyone can browse with `H guide`, search by name, alias, category, or displayed author with `H guide <query>` or `/team-guide`, and open related teams selected from shared categories.

### Boss-ticket board

- Watches explicit ticket checks:
  - `owo boss t`
  - `owo boss ticket`
  - `w boss t`
  - `w boss ticket`
- Associates each OwO response with the requesting Discord user.
- Stores reported `0/3`–`3/3` counts per server.
- Maintains one persistent board in a configured channel.
- Shows display name, exact Discord account username, numeric user ID, ticket count, update time, and next replenishment.
- Keeps the persistent board and manual ticket-list responses in a single Discord message, with Previous and Next buttons for large lists. Pages contain up to 15 entries so custom ticket emoji markup remains safely within Discord embed limits.
- Keeps the configured board sticky by sending a fresh replacement first and deleting the previous board afterward.
- Caches the complete ticket snapshot used by Previous, Next, Text view, and Ping view, so page switching performs no Discord member lookups.
- Coalesces bursts of ticket updates into a single background sticky-board replacement.
- Updates stored names from the member who actually ran the ticket command instead of scanning or loading the full server member list.
- Provides a visual ticket management panel for server managers, including remove, block tracking, and unblock actions.
- Supports optional per-server ticket markers in member nicknames, disabled by default and managed through `HBS`.
- Uses explicit per-member opt-in: enabling the server feature does not change any nickname until that member enables their own marker.
- Gives every member private controls to enable or disable their marker, remove their current board entry, stop ticket tracking completely, or resume it later.
- Adds a functional action reaction under successful ticket commands: `🏷️` enables the member's marker and `🔕` disables it.
- Adds a persistent **My settings** button to ticket boards plus `/boss-ticket-nickname`, `H boss nickname`, and `HBN`.
- Safely restores managed nicknames when markers are disabled, a member opts out, or a ticket entry is removed or blocked.
- Supports paginated managed user selection for servers with more than 25 tracked or blocked users.
- Uses `America/Los_Angeles` so Pacific daylight-saving changes are handled automatically.
- Supports manual list display and board refresh commands.
- Reads Components V2 ticket responses from both message-create and message-edit events, with a bounded raw-message fallback only after an explicit ticket request.
- Watches the public Top 10 section of the active OwO guild-boss card and subtracts one ticket for every newly observed public battle-log UUID, but only for members already on that server's ticket list.
- Deduplicates public battle logs permanently during their short retention window, supports two or three hits by the same member, and never allows a count below `0/3`.
- Keeps manual `w boss t` / `owo boss t` results authoritative and reminds members outside the visible Top 10 to update manually.
- Removes entries after 48 hours without a manual check or confirmed Top 10 hit while preserving block, tracking, and nickname preferences.
- Adds `HBT <name, username, mention, or ID>` for fast current-ticket lookups without loading server members.
- Loads 305 application-owned Discord emojis on startup: six UI emojis, 55 standard animals, 173 Special/event animals, 29 weapons, 28 passives, and 14 animal-rank icons. Existing names are reused and only missing names are created.
- Keeps Patreon and Custom Patreon animals in the learned Dex database instead of bulk-uploading their open-ended artwork catalog as application emojis.

### Neon weapon dex helper

The helper can watch public NeonUtil weapon inventory pages from Neon bot ID `851436490415931422`. When Neon shows **M** / max possible quality on a weapon row, the helper stores that weapon under the owner shown in Neon's title. The scan is deduped by Discord owner ID and weapon ID, so paging backward and forward does not create duplicates.

A successful scan adds a visible `🧾` reaction to the Neon page. Battle helpers can scan another member's filtered Neon pages; the resulting queue belongs to the member shown in Neon's title, so that member can later dex their own weapons. The `HW` guide prominently reminds members to click through every Neon weapon page because only displayed pages can be scanned.

Setup guide:

```text
H weapons / HW
```

Dex queue commands:

```text
HWD
H dex
H weapon dex
H weapondex
HW dex
HWD dagger
H dex dagger
HWD dagger mtap sg
H weapon stats
H weapon clear
```

`HWD` / `H dex` previews the queue with one **Start dexing session** button. Each active step shows the exact command directly in the channel beneath a small instruction telling the member to copy and send it there. **Skip weapon** and **Stop** remain available while the session is active. When the member sends `ww <weapon_id>` and Neon replies with a blueprint such as `sword 31,7 mtap 77`, the helper marks that weapon saved and learns any available weapon/passive context.

The scanner does not assume one stable emoji ID per weapon or passive. It tags weapon/passive context from Neon filter headers, command context, learned blueprint replies, and known aliases. Unknown rows still remain in the unfiltered dex queue.

After paging through Neon, the `HW` guide also asks members to run their server's OwO inventory command (for example `winv`) and then Neon's `/weapon inv check`. Neon can then compare the live inventory with its tracked list and show any difference before a guided dex session begins.

### Developer information and operational statistics

Public commands:

```text
H about
/about
```

The public profile identifies Hassaan as the developer, shows the current version, summarizes the bot, and links to the source repository.

Server-manager diagnostics:

```text
/channel-diagnostics
```

Run it inside a problem channel or thread to inspect the bot's effective text, history, embed, reaction, thread, message-management, and nickname permissions. Prefix replies now fall back to a normal channel message when Discord rejects a reply reference.

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
- Guild Members and Presence Intents are not required

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

Optional permissions:

- Send Messages in Threads — required when prefix commands are used inside threads.
- Manage Messages — allows guided team setup to remove completed commands and lets nickname action reactions flip cleanly by removing the member's click reaction.
- Manage Nicknames — allows a server manager to enable optional ticket markers through `HBS`. The bot role must be above members whose nicknames it edits.
- Mention @everyone, @here, and All Roles — required for reliable fighter-role notifications when the configured fighter role is not itself mentionable.

The bot does not require Administrator permission.

Required privileged gateway intent in the Discord Developer Portal:

- Message Content Intent

Server Members and Presence are not requested. Decision roles authorize trusted helpers to use HIT/SKIP and sticky controls, but new-boss announcements do not ping those roles or individual members.

### Application-owned UI emojis

Keep these files in `assets/ui_emojis/`:

```text
ticket_available.png
ticket_used.png
boss_appeared.png
boss_escaped.png
boss_defeated.png
```

On startup the bot lists its application-owned emojis, reuses matching names, and creates any missing names from these source PNGs. Application emojis do not require the destination server's **Use External Emojis** permission. If artwork is changed later, delete the old application emoji from the Developer Portal once so the bot can recreate it from the new file.

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
/boss-decision-role
/boss-fighter-role
/boss-report-channel
/setup-guide
```

`/boss-cooldown-channel` selects the channel for new-boss, defeat, escape, cooldown, and ready alerts. Add one or more decision roles with `/boss-decision-role`; they authorize HIT/SKIP and sticky controls but are not pinged by new-boss announcements. Add one or more fighter roles with `/boss-fighter-role`; they receive one persistent role alert when a helper chooses HIT. Make fighter roles mentionable, or grant the bot **Mention @everyone, @here, and All Roles** in the alert channel. `/boss-report-channel` selects the channel that receives the completed daily outcome report at Pacific midnight. Run `/boss-report-channel` without a channel to disable reports while preserving the current counters. `/setup-guide` shows server managers the complete configuration checklist; `H setup` posts the same guide publicly.

Channel troubleshooting for server managers:

```text
/channel-diagnostics
```

### Help

```text
H help
```

`H help` lists only commands currently supported by the bot, including the setup-guide shortcut, ticket-management panel, optional nickname markers, and the 100-template workflow. It is recognized at the start of the first non-empty line; any text after `H help` is ignored.

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

Open a specific slot or name directly:

```text
HT3
HTM3
H team 3
HT ddc tank boss
H team ddc
```

Name lookup supports exact or partial matches. If more than one saved team matches, the helper shows the matching teams so you can choose the right one.

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
ww AZWWZV hsnake
wtm a 2025dec_daisy 2
ww DYLYU5 2025dec_daisy
wtm a 2026feb_huba 3
ww EEK29J 2026feb_huba
```

The next command appears immediately after OwO confirms the current one. Saved weapons are equipped to the animal name instead of the position, so the weapon still goes to the right pet even if positions change.

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
- **Enable/Disable nickname markers** — makes the optional feature available or unavailable without blocking the panel on a server-wide loop.
- **Sync nickname markers** — queues a background sync for members who explicitly opted in.
- Paginated user selection for servers with more than 25 tracked or blocked users.

Nickname markers are disabled for the server by default, and they are also off for every member by default. Enabling the server feature only exposes the opt-in controls; it does not rename the tracked member list. Each member must choose **Enable my marker** or click the `🏷️` action reaction under their own successful ticket command. Disabling the server feature returns immediately and restores managed names through a throttled background job.

Enabling the feature requires the bot role to have **Manage Nicknames** and to be above the members it edits. Discord does not allow bots to edit the server owner's nickname, so the owner is skipped. Members at or above the bot's highest role are also skipped.

The marker format uses Unicode so it works without custom server emojis:

```text
3/3 → Falcon · 🎟🎟🎟
2/3 → Falcon · 🎟🎟▫
1/3 → Falcon · 🎟▫▫
0/3 → Falcon · ▫▫▫
```

The helper changes only the server nickname, preserves prefixes added by tools such as AFK bots, and removes only its own suffix when restoring a name. Discord nicknames are plain text, so the nickname suffix continues to use Unicode; the custom PNG emojis are used in bot messages and embeds.

### Personal ticket controls

Every member can privately control their own nickname marker and ticket-list participation with:

```text
/boss-ticket-nickname
H boss nickname
HBN
```

They can also click **My settings** under any ticket-board message. The private panel provides:

- **Enable my marker / Disable my marker** — explicitly opts the member's nickname in or out.
- **Remove me from list** — deletes the current board entry, but the next `w boss t` can add it again.
- **Stop tracking me** — removes the current entry and ignores later ticket checks until the member resumes tracking.
- **Resume tracking** — allows the next ticket check to add the member again.

When the server feature is available, a successful ticket check receives one action reaction:

- `🏷️` — click to enable your own nickname marker.
- `🔕` — click to disable your own nickname marker.

Only the author of that ticket command can use its reaction shortcut. The icon flips after the preference changes. Permission or role-hierarchy details remain available in **My settings** and the bot logs.

The existing `/boss-ticket-remove` command remains available for direct removal by Discord user ID or mention.

### Update a user's count

A user runs any ticket command anywhere the helper can read messages:

```text
owo boss t
owo boss ticket
w boss t
w boss ticket
```

The manual OwO response is authoritative and replaces the saved count. During an active guild boss, the helper also reads the public **Top 10 Damage Dealt** section. Each new public `owobot.com/battle-log?uuid=...` link is one confirmed ticket use, so two scroll links for the same member subtract two tickets and three links subtract three.

Automatic subtraction applies only when that Discord user is already on this server's ticket list, and it is deduplicated by battle-log UUID. Members outside the visible Top 10 must still run `w boss t` or `owo boss t` manually. The first snapshot of an already-running boss is stored as a baseline so restarting or upgrading the helper cannot retroactively subtract old hits.

### Look up one member

```text
HBT <name or part of name>
HBT @mention
HBT <Discord user ID>
```

A unique match shows the member's current `0/3`–`3/3` count, account username, numeric ID, last confirmed activity, and whether the latest value came from a manual check or a public Top 10 battle log. Multiple names can be checked in one message, and ambiguous matches are listed without guessing.

### Show and refresh the complete list

Public text commands:

```text
H boss list
H boss t
HBL
```

The helper intentionally keeps only these focused aliases. Older experimental aliases such as `H ticket list`, `T list`, and `HTL` are not supported.

Private slash command:

```text
/boss-ticket-list
```

These commands display the current list wherever they are used. The configured persistent board is replaced automatically whenever a member updates their ticket count.

### Text and Ping views

Every ticket list includes a display-mode button:

- **Text view** — shows the saved display name, exact Discord account username, and numeric user ID.
- **Ping view** — shows a clickable `<@user>` mention, exact account username, and numeric user ID.

Opening a page refreshes the names of the visible tracked users using their known Discord IDs. Mention rendering is configured not to notify members.

### Ticket replenishment

Ticket cycles follow midnight in:

```text
America/Los_Angeles
```

At the daily reset, every still-active tracked member is replenished to `3/3`. A later manual ticket check or confirmed Top 10 hit replaces that reset value. Resetting the visible count does not count as member activity.

An entry expires after 48 hours without either a successful manual ticket check or a confirmed Top 10 battle-log event. Removal clears only the current board row and managed nickname; personal tracking, block, and nickname preferences are preserved. Running `w boss t` later adds an eligible member back normally.

When v0.10.4 first migrates an existing database, all current rows receive rollout grace through the **second upcoming Pacific reset**: they remain after the next reset and are removed at the following reset only when no new activity was recorded. Discord timestamps display the corresponding local time for every viewer.

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
animal_dex.db
animal_dex.db-shm
animal_dex.db-wal
team_guides.db
team_guides.db-shm
team_guides.db-wal
boss_tickets.db
boss_tickets.db-shm
boss_tickets.db-wal
bot_stats.db
bot_stats.db-shm
bot_stats.db-wal
neon_weapons.db
neon_weapons.db-shm
neon_weapons.db-wal
logs/
```

These files are ignored by Git.

When moving the bot to another computer or host, copy `.env`, `boss_cooldown_config.json`, and every database listed above. This preserves saved teams, learned public animal facts, trusted guide access and content, ticket data, Neon queues, personal preferences, developer statistics, board configuration, boss watcher state, daily boss counters, and report-channel configuration.

## Project structure

The v0.13 catalog and guide implementation lives in `cogs/animal_dex.py`, `cogs/game_catalog.py`, and `cogs/team_guides.py`. Checked-in reference data is under `data/`; application-emoji sources are under `assets/game_emojis/`; and the repeatable maintainer importer is `scripts/sync_wiki_game_data.py`.

```text
owo-boss-helper-discord-bot/
├── assets/
│   ├── hp_digits/
│   └── ui_emojis/                 # source PNGs auto-synced as application emojis
├── cogs/
│   ├── __init__.py
│   ├── ui_emojis.py
│   ├── boss_generator.py
│   ├── team_templates.py
│   ├── ticket_tracker.py
│   ├── bot_info.py
│   ├── message_utils.py
│   └── neon_weapons.py
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
animal_dex.db
team_guides.db
boss_tickets.db
bot_stats.db
neon_weapons.db
```

## Updating

Stop the running bot, replace the updated source files, then run:

```bat
py -m pip install --upgrade -r requirements.txt
rmdir /s /q cogs\__pycache__ 2>nul
py bot.py
```

Do not delete `.env`, `boss_cooldown_config.json`, any runtime `.db`/`.db-wal`/`.db-shm` file, or `logs/` during an update.

## Production hosting

For a 24/7 Linux VPS deployment with automatic restarts and protected local storage, follow [DEPLOYMENT_DIGITALOCEAN.md](DEPLOYMENT_DIGITALOCEAN.md). Deployment helpers are included under `deploy/`.

## Privacy and terms

- [Privacy Policy](PRIVACY.md)
- [Terms of Use](TERMS.md)

## License

MIT License. See [LICENSE](LICENSE).


### Guided Neon weapon dex sessions

`HWD`, `H dex`, `H weapon dex`, `H weapondex`, and `HW dex` show queued alternating `ww <weapon_id>` / `wuse <weapon_id>` commands from scanned Neon weapon pages. Select **Start dexing session** to receive one command at a time. Each active prompt displays the current command directly in the channel for easy mobile copying; no extra copy button is required. After the member sends the shown command, the helper waits for Neon to confirm the weapon, waits about two seconds, removes the previous prompt, and posts the next one. Use `H stop` to pause and continue later.

### Mobile-friendly dex prompts

Guided Neon dex sessions post one short plain-text command at a time, such as `ww CX9974` or `wuse CX9974`, so mobile users can tap/copy it easily. The helper advances only after Neon confirms the shown weapon, waits about two seconds, removes the old prompt, and posts the next one. Use `H stop`, `Hstop`, or `HS` to pause the session.

### Cross-user Neon dexing

Unsaved empowered weapon-crate and boss-weapon rows that Neon shows without `M` are queued when they do not have the green saved tick.

Battle helpers can run `HWD @member` or `HWD @member dagger mtap sg` to dex another member's scanned Neon queue. Guided prompts show the weapon owner's name in bold, plus the runner name when someone else is doing the dexing. When Neon confirms a blueprint, the helper marks that weapon dexed for every matching owner queue, so a weapon dexed by a helper is removed from the original owner's queue too.
### Neon max-quality reaction scans

When a user clicks Neon's reaction on an OwO `ww` weapon-list message, the helper can read the resulting Neon max-quality report. Exact/green-tick rows clear matching dex queues, and rows without a green saved/exact tick stay queued for dex even when Neon does not show an `M` marker.

### Team command export

Saved team views include an **All commands** button. It posts the clean quick-replace command list publicly in the channel so advanced users can copy only the commands they need.

### Neon emoji context enrichment

Neon max-quality reports and weapon pages include compact emoji names such as `raedge`, `cswarm`, `mresonance`, and `eawand`. OwO Boss Helper normalizes those names by removing rarity/frame prefixes, then stores the inferred weapon and passive context on the matching weapon ID. This makes filters like `HWD mtap`, `HWD resonance`, and `HWD dagger mtap` work even when the original queue entry was scanned as unknown.

## v0.11.0-beta notes

### Long Neon dex sessions

`HWD` supports optional session lengths for large weapon cleanups:

```text
HWD
HWD 100
HWD 250
HWD all
HWD mtap 100
HWD dagger mtap 50
HWD @member all
```

The default session remains 20 weapons. `all` and large numbers are capped at 1,000 queued commands for safety. A session still alternates `ww <weapon_id>` and `wuse <weapon_id>`, and it still advances only after OwO replies and Neon confirms the weapon.

Use `HWD skip` or the **Skip weapon** button when a queued weapon was sold, dismantled, or no longer exists. Skipping deletes that weapon ID from the owner’s dex queue; if Neon scans it again later, it can be re-added.

### Owner server navigation

The developer-only `/bot-servers` command has Previous, Next, and Refresh buttons. Each row includes the current server owner ID/mention. This identifies the current Discord guild owner, not necessarily the person who invited the bot. The bot does not create invite links automatically; if a guild already has a vanity code visible to the bot, it is shown as a hint.

### Owner server insight tools

Owner-only operational views now include `/bot-servers` for paged server reach and `/bot-server server_id:<id>` for a detailed server view. The detail view shows current owner, activity timestamps, per-command usage breakdowns, current bot permissions, and safe vanity invite visibility. A daily owner DM report summarizes active servers, new/removed servers, top usage, recent usage, and servers that may need review.

Discord does not reliably expose who invited a bot, so server insight views focus on the current server owner and actual bot usage.

### Team editor and order controls

Saved teams can be edited without recreating them from a fresh OwO team page.

- `HTE 3` / `HT edit 3` opens the editor for team #3.
- `HT rename 3 new name` renames a saved team.
- `HT rename old name to new name` renames by name.
- `HT move 8 1` moves team #8 to the top of the saved-team list and shifts the others.
- `HT swap 1 with 5` swaps two saved-team slots.
- `HT order` opens button controls for saved-team order.

The editor can update each team position's animal/pet identity and weapon ID. Weapon restore commands still equip by animal identity, not by fragile position.

## v0.11.1-beta notes

### Per-server OwO prefix for team restores

Saved-team restore commands now respect the OwO prefix used in each Discord server. The default remains `w`, so generated commands are still `wtm` and `ww` unless a server manager changes it.

Examples:

```text
HT prefix
HT prefix o
H team prefix g
```

After setting `HT prefix o`, guided team restore commands use `otm ...` and `ow <weapon_id> <animal>`. Only members with **Manage Server** can change the prefix, but anyone can run `HT prefix` to check the current setting.

### Combined Neon dex filters

Weapon dex filters can now include more than one weapon type in the same command. This lets members clean multiple selected categories in one guided session instead of starting separate sessions.

Examples:

```text
HWD dagger shield 100
HWD sword shield crane all
HWD @member dagger shield mtap 250
```

Weapon type filters are OR-based, while passive filters are still matched together. For example, `HWD dagger shield mtap` shows queued daggers or shields that include Mana Tap.

### Daily owner report fix

The scheduled owner DM report now starts with the bot. The owner can also run `/bot-daily-report-test` to send a report immediately for verification.
### v0.11.2-beta feedback update

- Server OwO prefixes now apply to team restores, boss inventory (`o boss i`), and boss tickets (`o boss t`).
- Boss alert channels can show a temporary hit/skip sticky while a guild boss is active.
- Managers can set a trusted role for hit/skip decisions with `/boss-decision-role`; Manage Server still works.

### v0.11.3-beta boss decision stickies

Boss alert channels now support helper-controlled stickies: `H boss hit`, `H boss skip`, `H sticky`, `H sticky clear`, `H sticky on`, and `H sticky off`. Server managers can configure `/boss-decision-role` for helpers and `/boss-fighter-role` for one-time HIT pings.
