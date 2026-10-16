# v0.16.2-beta — OwO Footer Streak Parsing

This patch fixes team XP forecasts that displayed streak 0 even though the OwO team card visibly contained a current streak.

## Correct streak extraction

- OwO can place `Current Streak` in the embed footer rather than the main description or fields.
- The helper now reads visible footer text from raw Discord payloads and normal discord.py messages.
- The reported streak-1,734 card now calculates 369.5 XP per battle instead of falling back to 200.
- Pet levels, current XP, catch-up multipliers, and the three-column forecast remain unchanged.

## Familiar team-panel icons

- XP forecast: Stats chart.
- XP calculator: Abacus.
- Save normal: Sword.
- Save Knowledge: the bot's Knowledge application emoji, with a brain fallback.
- Reminders: Bell.
- Save-team help: the existing save icon.

## Deployment notes

- No database migration is required.
- No slash-command schema changed.
- No dependency or Discord permission changed.
