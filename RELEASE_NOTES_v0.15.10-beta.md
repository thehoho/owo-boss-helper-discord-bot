# v0.15.10-beta release notes

## Reliable Full Guide interactions

One-page Full Guide responses no longer pass an explicit empty view into Discord's interaction handler. They now open privately with only their embed, while multi-page guides continue to receive the existing private Previous/Next pager.

## Lower Discord request pressure

Guild-boss updates remain gateway-first and immediate. When Discord supplies a complete active or completed boss payload, the bot processes it directly without fetching the message again.

Partial edit bursts now share a two-second, edit-version-aware fetch result. Repeated fragments from the same edit cause one REST request, while a distinct edit timestamp always bypasses the short cache so a new hit or outcome cannot be hidden.

The active-card watcher is now a once-per-minute safety poll instead of a 15-second poll, and restored watchers are spread across startup. The separate one-minute exact-HP reconciliation remains in place, manual cooldown checks still refresh immediately, and the gateway path continues to update exact HP, tickets, rewards, and outcomes as events arrive.

## Deployment

- Public version: `0.15.10-beta`
- Database migration: none
- New Discord permissions: none
- Slash-command changes: none
- Dependency changes: none
