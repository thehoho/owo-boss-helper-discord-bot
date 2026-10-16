# v0.16.4-beta — Compact XP Forecasting

This release makes the interactive team forecast denser, clearer, and easier to configure without changing reminder data or command permissions.

## Compact forecast

- The level-event table now focuses on pet levels, battle count, streak, and days.
- Progress percentages are removed from both the main team panel and the full forecast.
- Pet statistics are optional through **Show stats**.
- The streak column can be shown or hidden independently.
- Active assumptions stay visible in one compact footer line.

## One settings form

The separate Knowledge/line-count form is gone. **Settings** now controls:

- battles per day;
- fixed XP per day, such as Huntbot and boss XP;
- average XP per hunt (16 baseline or 21 with Patreon);
- tie and loss rates;
- maximum streak; and
- Knowledge quality.

Activity defaults start at zero so the bot does not silently apply one member's grinding pattern to everyone. A note points members to Neon Global when they need an estimated team tie/loss rate.

## More complete modeling

- Ties award tie XP without advancing the projected streak.
- Losses award loss XP and reset the projected streak.
- Maximum streak remains optional; zero means no forced forecast reset.
- Knowledge's benefit is shown as an additive multiplier and an effective percentage. For example, `2.70× → 2.85×` is a 5.6% effective increase, while `1.00× → 1.15×` is 15%.

No database migration, dependency update, slash-command schema change, or new permission is required.
