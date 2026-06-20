# OwO Boss Helper v0.8.0 Beta

This release brings together every improvement since v0.6.2 and prepares the hosted bot for reliable public use.

## New developer identity and operations

- Added `H about` and `/about` with a clear public description, version, developer credit, and source link.
- Added owner-only `/bot-stats` and `/bot-servers`.
- Added persistent server history and aggregate usage tracking in `bot_stats.db`.
- Added private join/leave notifications to the configured developer.
- Added production hosting documentation, a systemd service, protected environment settings, and SQLite-safe backups.
- Added privacy and terms documents.

## Boss-ticket tracking

- Added per-server persistent ticket boards.
- Reads `0/3`, `1/3`, `2/3`, and `3/3`, including OwO's “ran out of boss tickets” wording.
- Replaces the configured board after ticket updates so only the current board remains.
- Keeps previously tracked members and replenishes them to `3/3` at Pacific midnight.
- Lets later OwO checks replace the reset value with the real count.
- Added `/boss-ticket-channel`, `/boss-ticket-list`, `/boss-ticket-remove`, `H boss t`, `H boss list`, and `HBL`.
- Uses DST-safe `America/Los_Angeles` reset handling.

## Team-template improvements

- Reads animal identity from OwO emoji aliases, so renamed pets work correctly.
- Normalizes standard rank-prefixed animals and preserves unknown custom pets.
- Validates all three positions and warns about missing weapons.
- Added update-by-slot or name with `HT U` / `HTU`.
- Preserves stable team numbers and exact weapon IDs.
- Improved guided restoration, skip/cancel controls, response handling, and optional cleanup.

## Boss helper reliability

- Improved Components V2 parsing and bounded raw-message fallback behavior.
- Prevented repeated polling of deleted boss-status messages.
- Preserved rate-limit-friendly tracking, HP extraction, correct boss order, duplicate alert protection, cooldown handling, and mobile-friendly commands.

## Upgrade notes

1. Replace the updated source files.
2. Add the new environment variables from `.env.example`.
3. Keep `.env`, all SQLite databases, `boss_cooldown_config.json`, and logs private.
4. Restart the bot and confirm the slash commands sync.
5. Configure `BOT_OWNER_ID` to unlock private operational statistics.

This remains a beta release while the new 24/7 hosting environment and operational statistics are observed under live use.

## Credits

Special thanks to **Pencilvester** for the original exact-command parsing logic, weapon/passive ranges, and the saved team-template concept that inspired the team-management system.
