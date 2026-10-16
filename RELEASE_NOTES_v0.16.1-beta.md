# v0.16.1-beta — Streak-aware XP Forecasts

This patch corrects and reorganizes the Pet XP forecast introduced in v0.16.0-beta.

## Corrected calculations

- The current OwO streak now determines the expected XP per battle automatically.
- The team reaction reads that streak directly; members no longer enter or interpret an average-XP value.
- `/xp-calc` now asks for the current streak and optionally accepts fixed XP earned per day.
- Catch-up is recalculated whenever the pet gains a level.
- Fixed daily XP is spread across the configured battles per day and is not incorrectly multiplied by catch-up or Knowledge.
- The supplied streak-1,265 example now estimates 358.3 XP per battle. Its first pet needs about 4,128 battle-only rounds, or 3,741 rounds when 200,000 fixed XP/day and 2,000 battles/day are included.

## Clearer presentation

- Team forecasts are displayed as three compact inline pet cards.
- Each card shows its pet, level transition, remaining XP, catch-up multiplier, normal estimate, and maximum-Knowledge estimate.
- The XP panel and its buttons reuse the bot's existing application-owned pet, calculator, weapon, reminder, and Knowledge emojis, with safe Unicode fallbacks.

## Deployment notes

- No database migration is required.
- No new dependency or Discord permission is required.
- Discord will resync the revised `/xp-calc` parameters during startup.
