# Privacy Policy

_Last updated: 2 July 2026_

OwO Boss Helper is an independent community Discord bot developed by Hassaan.

## Data the bot stores

The bot stores only the information needed to provide its features and operate safely:

- Discord server IDs, server names, owner IDs, approximate member counts, channel counts, installation status, and aggregate command-use counts.
- Discord user IDs, display names, and Discord account usernames associated with saved team templates and boss-ticket entries.
- When a server manager enables optional ticket nickname markers, the bot stores the member's prior server nickname, the last nickname applied by the bot, and whether that member chose to show or hide the suffix so it can update or restore the name safely.
- The bot stores whether a member has personally paused ticket tracking in a server. Paused members are not added by later ticket checks until they resume tracking.
- Saved team animals, positions, and weapon IDs.
- Reported boss-ticket counts, update sources, last ticket activity times, and automatic expiration times.
- Public OwO guild-boss message IDs and public `owobot.com` battle-log UUIDs used temporarily to deduplicate confirmed Top 10 ticket uses. The helper does not copy or store the full linked battle-log page.
- Configured Discord channel and message IDs used for cooldown and ticket boards.
- Operational logs containing events, warnings, and errors.

The bot does not intentionally store ordinary conversation content. It temporarily reads relevant messages to recognize supported OwO commands and responses.

## Why the data is used

Data is used to generate boss commands, restore saved teams, maintain ticket and cooldown boards, reconcile public Top 10 boss hits, remove inactive ticket entries, optionally display ticket availability in server nicknames, restore managed nicknames, diagnose problems, measure bot usage, and notify the developer when the bot joins or leaves a server.

## Storage and sharing

Runtime data is stored on the bot's private hosting server in local SQLite databases, JSON configuration files, and rotating logs. Data is not sold to advertisers or shared for advertising.

## Retention and deletion

- Current ticket-board entries expire after 48 hours without a successful manual ticket check or a confirmed public Top 10 hit. Personal tracking, block, and nickname preferences are preserved so the user's chosen controls continue to work.
- Public battle-log UUID deduplication records and boss-message observations are automatically removed after approximately seven days.
- Server managers can remove ticket-board users with `/boss-ticket-remove`.
- Ticket nickname markers are disabled by default. Each member can hide or show their marker, remove their current board entry, pause ticket tracking, or resume it later. Disabling the server feature attempts to restore every nickname managed by the bot and remove the associated restoration state after a successful cleanup.
- Users can delete saved team templates through the documented team commands.
- Server metadata may remain as historical installation statistics after the bot leaves a server.
- The developer can remove stored records when requested and reasonably verifiable.

## Security

The Discord bot token and runtime databases are not committed to the public source repository. Hosting access is restricted and backups should be protected.

## Contact

Questions and deletion requests can be opened through the project's GitHub issue tracker:

https://github.com/thehoho/owo-boss-helper-discord-bot/issues/new

Users should not post sensitive information publicly. The developer will arrange private verification when needed before deleting user-associated records.
