# v0.15.0-beta — Opt-in Boss Reward and End DMs

This release adds personal, consent-based guild-boss notifications while keeping OwO's authoritative boss lifecycle as the only source of active and completed states.

## Member usage

Run `/boss-notify` without a reward to view your rules, or use the server's configured helper prefix:

- `H boss notify shards 175`
- `H boss notify crate 4`
- `H boss notify bcrate 3`
- `H boss notify xp 20k`
- `H boss notify x2`
- `H boss notify end`

Rules repeat for every boss unless `current`, `once`, or the slash-command **Current or next boss only** choice is used. A one-boss rule attaches to the active boss when one is known; otherwise it attaches to the next boss. Minimum rules use `at least` comparison. Multiple rules are ORed and produce one combined reward DM per boss.

Disable one rule with `H boss notify xp off` or all rules in the current server with `H boss notify off`. The first enable asks for explicit consent and sends a test DM before saving anything.

## Reliability and privacy

- Reads only an official-looking `reward.png` hosted on Discord's CDN and only after the existing strict OwO guild-boss layout check passes.
- Requires the current 620×60 reward-card layout and strict local digit matching; uncertain or changed layouts fail closed without alerting.
- Samples up to three newer copies of a boss card. Conflicting values are logged and the first trusted snapshot remains authoritative.
- Uses the already-hardened boss identity, mailbox isolation, stale-card rejection, defeat deduplication, and escape timer paths for end alerts.
- Sends no DMs until the member confirms and the test DM succeeds.
- Keeps one delivery record per member/boss/kind to avoid duplicate DMs.
- Removes reward snapshots and delivery state after seven days. Recurring preferences remain until disabled, while one-boss rules expire with that boss.

## Deployment

The new `boss_notifications.db` schema is created automatically. Reinstall `deploy/backup.sh` as `/usr/local/bin/owo-boss-helper-backup` so notification preferences are included in future backups. No dependency change and no additional Discord permission are required.
