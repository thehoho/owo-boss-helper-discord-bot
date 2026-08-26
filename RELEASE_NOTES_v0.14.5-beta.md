# OwO Boss Helper v0.14.5-beta

This emergency hotfix restores reliable guild-boss status tracking after OwO's August 2026 active-card update and expands HP recognition for the current boss-image rendering.

## Active boss status fix

- Recognizes the new **A Guild Boss Appeared!** summary with its future `runs away` timestamp, fighter count, and numeric defeated reward statistic.
- Treats values such as `3,020 defeated` as historical reward counters, not as the outcome of the active boss.
- Preserves detection of explicit completed wording such as `the guild boss was defeated`, `slain`, `killed`, or `ran away`.
- Repairs a falsely completed saved state only when a newer official active card has an unexpired escape timestamp. Older or duplicate cards remain ignored.
- Prevents false five-minute cooldowns, false ready alerts, and missing HIT/SKIP stickies caused by the reward counter.

## HP recognition fix

- Splits a two-pixel comma when OwO's current pixel rendering touches it to an adjacent digit.
- Handles both observed forms: comma-plus-digit (for example the failing cow image) and digit-plus-comma (for example the failing owl image).
- Keeps the existing confidence, leading-zero, and current-versus-maximum HP validation in place.
- Adds the exact cow `166,463` and owl `207,864` images as permanent regression fixtures.

## Compatibility and verification

- No database migration or new runtime data file is required.
- No new Discord permission or privileged intent is required.
- Existing channels, reports, tickets, saved teams, guides, prefixes, animal data, and weapon data remain compatible.
- The complete local suite passes 74 tests, including the new active-status, state-repair, and HP-image regressions.

## Live verification checklist

1. Send the normal OwO boss-info command while a boss is active.
2. Confirm the helper tracks the future escape time and does not announce defeat or readiness.
3. Mark the active boss HIT or SKIP and confirm the sticky is available normally.
4. Scan the supplied cow and owl boss images and confirm the generated HP values are `166463` and `207864`.
5. Run `H about` and confirm version `0.14.5-beta`.
