# OwO Boss Helper v0.10.2 Beta

This refinement fixes large ticket-board interactions, activates the custom UI emoji
artwork already stored in the repository, and gives every member direct control over
whether they appear in the ticket system.

## Ticket-board interaction fix

Previous/Next and Text/Ping view interactions now acknowledge the click immediately
before refreshing member identities. Visible-page lookups run with bounded concurrency
and a short cache, so large four- or five-page boards no longer exceed Discord's
interaction response window. Callback failures are logged and return a useful private
error instead of only showing `This interaction failed`.

Pages now contain up to 15 entries to keep three custom ticket emoji mentions per row
within Discord's embed-size limits.

## Application-owned emojis

The bot now automatically uses these repository files:

- `assets/ui_emojis/ticket_available.png`
- `assets/ui_emojis/ticket_used.png`
- `assets/ui_emojis/boss_appeared.png`
- `assets/ui_emojis/boss_escaped.png`
- `assets/ui_emojis/boss_defeated.png`

At startup it reuses application emojis with the same names and creates any missing
ones through Discord's application emoji API. Ticket rows display available and used
ticket artwork. Guild-boss active/appeared, escaped, and defeated/cooldown messages use
the corresponding artwork. Unicode remains in member nicknames because Discord
nicknames cannot contain custom emoji objects.

## Personal tracking controls

The ticket-board button is now **My settings**. The same private panel remains
available through `/boss-ticket-nickname`, `H boss nickname`, and `HBN`. Members can:

- Show or hide only their nickname marker.
- Remove their current entry from the list while allowing a future check to add it.
- Stop ticket tracking completely, removing the current entry and ignoring later
  ticket checks.
- Resume tracking whenever they choose.

A new `ticket_tracking_preferences` table is created automatically inside the existing
`boss_tickets.db`. No manual migration is required. Server-manager blocks remain
separate and cannot be overridden by the member.

## Deployment

Updated files include `bot.py`, the new `cogs/ui_emojis.py`, `cogs/boss_generator.py`,
`cogs/ticket_tracker.py`, `cogs/bot_info.py`, and documentation. The expected slash
command count remains 10.
