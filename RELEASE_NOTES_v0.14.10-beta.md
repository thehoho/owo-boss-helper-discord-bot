# OwO Boss Helper v0.14.10-beta

This incident hotfix separates OwO guild-boss status cards from the newer OwO mailbox reward system.

## Root cause

OwO mail can contain `You defeated a guild boss!`, a recent `Received` timestamp, rewards, and the same official OwO author ID as a real boss card. That allowed a mail reward received during an active boss lifetime to satisfy the replacement-result checks introduced in v0.14.9.

## Fix

- Boss state tracking now requires the actual `Top 10 Damage Dealt` leaderboard marker.
- Mail, inventory, and standalone reward notices are rejected before outcome parsing.
- The rule is global and contains no guild-specific or channel-specific exceptions.
- Known active-card edits and legitimate newer result cards retain their existing instance and timestamp checks.
- The boss-ticket snapshot classifier follows the same leaderboard boundary.

## Verification

- The exact production mail payload is a permanent regression fixture.
- Its generic text still parses as the English outcome `defeated`, but it cannot qualify as a boss status or reach the outcome handler.
- Real active and defeated leaderboard cards remain recognized.
- Previous stale-card, replacement-result, active-counter, HP, Dex, team, guide, and ticket regressions continue to pass.

## Compatibility

- No database migration is required.
- Existing active boss state and saved settings remain compatible.
- No additional Discord permission or privileged intent is required.
