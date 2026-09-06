# v0.15.1-beta — Notification Guide and Reward-specific x2 Rules

This update makes the notification system easier to discover and uses the community's standard reward shortcuts.

## Guide and shortcuts

Run `H boss notify` to open the full guide. It automatically uses the server's configured helper prefix and shows the member's currently enabled rules. The main `H help` guide now includes a Personal boss notifications section linking to it.

- `WS` — weapon shards
- `WC` — weapon crates
- `BWC` — boss weapon crates
- `XP` — experience

Examples:

- `H boss notify ws 175`
- `H boss notify wc 4`
- `H boss notify bwc 3`
- `H boss notify xp 20k`

The old `crate` and `bcrate` shorthand is no longer accepted. The slash command continues to show full descriptive labels alongside the standard shortcuts.

## x2 filters

`H boss notify x2` matches any doubled reward. A member can instead watch only one reward's x2 badge:

- `H boss notify ws x2`
- `H boss notify wc x2`
- `H boss notify bwc x2`
- `H boss notify xp x2`

Add `current` to any enable command for the active boss only, or the next boss when none is active. Disable a specific x2 rule with a command such as `H boss notify wc x2 off`, or remove every notification rule in the server with `H boss notify off`.

The same normal, specific-x2, any-x2, and boss-end choices are available through `/boss-notify`. Existing consent, DM verification, deduplication, and authoritative boss-outcome protections are unchanged.
