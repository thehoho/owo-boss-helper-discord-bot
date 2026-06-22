# OwO Boss Helper v0.9.0 Beta

This release focuses on making large-server ticket coordination easier, increasing team-template capacity, and making manual guild-boss status checks more trustworthy.

## Paginated boss-ticket board

The ticket board is now one clean message instead of several messages stacked in the configured channel.

- Previous and Next buttons navigate groups of 20 users.
- The same pagination is available through `H boss t`, `H boss list`, `HBL`, and `/boss-ticket-list`.
- The persistent board buttons are registered across restarts.
- Ticket updates still replace the configured board safely, but only one current board message remains.

## Visual ticket-user management

Server managers can use:

- `H boss settings`
- `HBS`
- `/boss-ticket-manage`

The panel provides a dropdown and buttons for:

- **Remove from list:** remove the current entry while allowing a later ticket check to add it again.
- **Block tracking:** remove the entry and ignore future ticket checks from that user in the server.
- **Unblock:** allow that user to be tracked again.

The management list is also paginated for large servers. `/boss-ticket-remove` remains available for direct ID-based removal.

## 100 team templates

The personal template limit increased from 25 to 100.

- `HT` and `HTM` now show templates in pages of 25.
- Previous and Next buttons move between template pages.
- Direct access such as `HT73` still opens the selected stable slot immediately.
- Existing templates and slot numbers require no migration.

## More reliable guild-boss status

Manual status commands now reconcile their saved state before answering:

- Late active cards from a defeated or escaped boss are ignored.
- Completed bosses cannot reactivate themselves through a delayed OwO edit.
- Missing tracked messages are shown as **status unconfirmed** instead of definitely active.
- Ready and escaped responses clearly state that no new guild boss has been detected and grinding can spawn one.

## Current command help

`H help` was rewritten to list only active commands and now includes ticket management, current list aliases, team shortcuts, and project information.

## Upgrade notes

Replace:

- `cogs/ticket_tracker.py`
- `cogs/team_templates.py`
- `cogs/boss_generator.py`
- `cogs/bot_info.py`

No dependency installation or manual database migration is required. The ticket tracker automatically creates its new blocked-user table in `boss_tickets.db`.

After updating, restart the bot and confirm that the slash-command sync count increases by one for `/boss-ticket-manage`.
