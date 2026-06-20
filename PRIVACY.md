# Privacy Policy

_Last updated: 21 June 2026_

OwO Boss Helper is an independent community Discord bot developed by Hassaan.

## Data the bot stores

The bot stores only the information needed to provide its features and operate safely:

- Discord server IDs, server names, owner IDs, approximate member counts, channel counts, installation status, and aggregate command-use counts.
- Discord user IDs and display names associated with saved team templates and boss-ticket entries.
- Saved team animals, positions, and weapon IDs.
- Reported boss-ticket counts and update times.
- Configured Discord channel and message IDs used for cooldown and ticket boards.
- Operational logs containing events, warnings, and errors.

The bot does not intentionally store ordinary conversation content. It temporarily reads relevant messages to recognize supported OwO commands and responses.

## Why the data is used

Data is used to generate boss commands, restore saved teams, maintain ticket and cooldown boards, diagnose problems, measure bot usage, and notify the developer when the bot joins or leaves a server.

## Storage and sharing

Runtime data is stored on the bot's private hosting server in local SQLite databases, JSON configuration files, and rotating logs. Data is not sold to advertisers or shared for advertising.

## Retention and deletion

- Server managers can remove ticket-board users with `/boss-ticket-remove`.
- Users can delete saved team templates through the documented team commands.
- Server metadata may remain as historical installation statistics after the bot leaves a server.
- The developer can remove stored records when requested and reasonably verifiable.

## Security

The Discord bot token and runtime databases are not committed to the public source repository. Hosting access is restricted and backups should be protected.

## Contact

Questions or deletion requests can be opened through the project's GitHub repository or sent to the developer through the bot's support server when one is configured.
