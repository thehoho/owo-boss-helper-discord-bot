# v0.11.4-beta - Plain Boss Stickies and Multi-Role Pings

This is a layout and permission update for the boss decision feature.

## Changed

- Boss decision stickies are now plain Discord messages instead of embeds/buttons.
- New boss alerts real-ping the configured boss-decision roles and show the available helper commands.
- `H boss hit` creates a plain `# HIT` sticky using the boss-appeared emoji and pings configured fighter roles once for the active boss.
- `H boss skip` creates a plain `# SKIP` sticky using the configured boss-escaped emoji when available, with no running-person fallback.
- Reply to any note/message with `H sticky` to copy that note as the sticky message.
- `H sticky clear`, `H sticky off`, and `H sticky on` control sticky behavior.

## Multi-role setup

- `/boss-decision-role <role>` adds a role allowed to set HIT/SKIP and manage stickies.
- Run `/boss-decision-role <another role>` again to add more allowed roles.
- Run `/boss-decision-role` with no role to clear all decision roles.
- `/boss-fighter-role <role>` adds a role to ping when a boss is marked HIT.
- Run `/boss-fighter-role` with no role to clear all fighter ping roles.

## Notes

- Members with Manage Server can always configure and use these controls.
- Fighter roles are pinged only once per active boss.
- The sticky is removed automatically when the boss is defeated or escapes.
