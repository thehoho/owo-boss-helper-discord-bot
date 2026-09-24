# v0.15.9-beta release notes

## Reliable guided team changes

Smart Replace now recognizes OwO's newer compact `Successfully changed the team!` response for both team deletions and additions. A successful `wtm d ...` step advances immediately to the next command instead of waiting for a full refreshed team page or retrying a change OwO already applied.

## Reliable ticket commands and Top 10 hits

The standard `w boss t`, `w boss ticket`, `owo boss t`, and `owo boss ticket` forms are now recognized even when a server has a different saved OwO prefix. Custom configured prefixes continue to work. After OwO returns the ticket count, the existing muted-bell or tag control reaction is still attached to the member's original command when nickname controls are enabled.

For members already tracked on the ticket board, a newly observed Top 10 battle-log UUID now subtracts a ticket even when it is the first snapshot seen for that boss card. Durable boss observations and UUID-level deduplication prevent repeat subtraction across edits and restarts. Users who were not tracked when a link first appeared are not charged retroactively.

## Clearer guide author help

The tutorial now teaches the usual `weapon + passive @ rank` composition while still allowing extra passives for boss weapons and other advanced setups. Special-animal instructions focus on exact Animal Dex names, aliases, previewing, and publishing without exposing internal emoji-capacity details. The internal guide-only special-animal allowance is now 300.

## Deployment

- Public version: `0.15.9-beta`
- Database migration: none
- New Discord permissions: none
- Slash-command changes: none
- Existing reaction behavior: preserved; no new confirmation reaction was added