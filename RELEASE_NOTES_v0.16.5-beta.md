# v0.16.5-beta — Forecast Layout Refinements

This release polishes the compact team XP forecaster and its settings flow.

## Forecast table

- Level-up rows use Discord's native `🆙` emoji.
- Other pet levels use Discord's native `⬛` emoji.
- Each pet occupies a visibly separated column before Battles, Streak, and Days.
- The default forecast contains 20 level-event lines and remains paginated when needed.

## Optional settings

All five Settings fields are optional and show their active values. Clearing a field no longer produces Discord's “field is required” error; an empty field keeps its current value.

- **Activity** contains battles/day, fixed XP/day, and XP/hunt.
- **Tie/loss rates** contains the two percentages.
- **Maximum streak** has its own field.
- **Knowledge quality** has its own field.
- **Forecast lines** has its own field and accepts 1–99.

Typing `0` explicitly resets settings that permit zero. No database migration, dependency update, slash-command schema change, or new permission is required.
