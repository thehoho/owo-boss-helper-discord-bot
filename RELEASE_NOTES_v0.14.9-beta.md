# OwO Boss Helper v0.14.9-beta

This correctness release improves boss outcome identity and tightens several command fallbacks before the larger team, guide, emoji, battle-log, and rewards work begins.

## Boss outcome identity

- Continues accepting defeats and escapes edited onto a message previously observed as active.
- Accepts a legitimate result published as a newer OwO replacement message only when it has an explicit result timestamp inside the current boss lifetime.
- Rejects stale completed copies, older message IDs, results arriving after the boss window, and replacement results without a tracked lifetime.
- Deduplicates replacement messages through the existing boss key so one boss still produces one report and one cooldown.
- Persists the active boss's first-seen time across restarts and clears it with the completed instance.

## Safer command behavior

- Changes the unreadable HP fallback from `80000` to `100000`.
- Makes compact `HAD <animal>` stay silent when the requested animal is absent from the saved Dex.
- Prevents ordinary messages such as `had a good day` from receiving an Animal Dex error reply.
- Keeps explicit `H animal dex <animal>` and `/animal-dex` not-found responses unchanged.
- Preserves normal `squid` as `squid` and hidden squid as `hsquid`.

## Verification

- Known active-message outcomes remain authoritative.
- A matching newer replacement defeat starts the correct cooldown.
- A stale copied result cannot finish the active boss.
- Older replacement IDs and results after expiry are rejected.
- First-seen boss state is persisted and cleared correctly.
- Compact HAD silence, 100k fallback, and squid identity have dedicated regressions.
- The complete automated suite passes.

## Compatibility

- No database migration is required.
- Existing cooldown, team, guide, Dex, and ticket data remain compatible.
- No additional Discord permission or privileged intent is required.
