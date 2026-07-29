# v0.11.6-beta - Intent-Safe Setup and Dex Clarity

This release keeps the useful daily-report and boss-sticky work from v0.11.5 while removing the unapproved Guild Members and Presence dependency.

## Discord intents

- The bot requests Message Content only.
- Guild Members and Presence are no longer requested.
- New-boss announcements do not ping configured decision roles or individual members.
- Decision roles still authorize trusted helpers to use HIT/SKIP and sticky controls.
- Optional fighter roles can still receive one persistent role alert after a helper chooses HIT.
- A configured fighter role must be mentionable, or the bot needs **Mention @everyone, @here, and All Roles** in the boss-alert channel.

## Server setup guide

- Added `/setup-guide` for a private server-manager checklist.
- Added `H setup` and `H setup guide` for the same guide in a channel.
- The checklist covers boss alerts, daily reports, ticket boards, decision roles, fighter roles, channel permissions, the server OwO prefix, and member help commands.
- `H help` now points server owners to the setup guide.

## Neon weapon clarity

- The existing `HW` / `H weapons` guide now highlights `➡️ Click through every weapon page` so the complete visible queue is scanned.
- The HWD queue preview has clearer step-by-step instructions.
- Added **Copy first command**, which opens the first queued command in a private copy-ready code block.
- Active guided dex prompts now include **Copy command** for the exact current `ww` or `wuse` command.

## Preserved from v0.11.5

- Daily confirmed HIT/SKIP reports at Pacific midnight.
- Retry storage for up to seven failed daily reports.
- Clean `# HIT` and `# SKIP` sticky messages without the deciding-member line.
- Persistent fighter-role alerts separated from the self-reposting sticky.
- New-boss announcements displayed as embeds.

## Deployment safety

This release does not require Guild Members or Presence to be approved or enabled. Message Content remains required for the bot's prefix commands and automatic parsing of OwO and NeonUtil messages and edits.
