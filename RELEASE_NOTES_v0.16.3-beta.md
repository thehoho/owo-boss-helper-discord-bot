# v0.16.3-beta — Interactive Team XP Forecasting

This release consolidates the team XP response and adds an adjustable, paginated team forecaster.

## One persistent team panel

- Reacting to an OwO team card now produces one reply containing current XP, remaining XP, multipliers, normal battles, and maximum-Knowledge battles for all three pets.
- The helper no longer mentions the reacting member.
- The response is no longer automatically deleted after five minutes.
- The Team XP tools title has no icon, and the separate XP forecast button has been removed.

## Matching forecast assumptions

The supplied Neon-style defaults are now explicit and editable:

- 2,000 battles per day.
- 200,000 fixed XP per day for each pet.
- 21 average XP per hunt, with one hunt paired to each modeled battle.
- 0.82% tie rate.
- Maximum streak 2,600.
- Maximum-quality Knowledge at 0.15.

With the reported sample, the first pet forecasts approximately 3,248 battles versus the reference's 3,246; the two-battle difference is deterministic rounding in projected ties and streak wrapping.

## Full-team calculator

- XP Calculator opens a paginated level-event forecast for all three pets.
- Activity Settings edits battles/day, fixed XP/day, average XP/hunt, tie rate, and maximum streak.
- Knowledge & lines edits Knowledge quality and the number of generated level-event lines, up to 99.
- A text-only scenario button cycles through six plans: Always equipped, every 10, 50, 100, 500, or 1,000 battles.
- Each view compares the selected plan with normal weapons and shows its battle savings.
- Generated pages are cached, so paging is immediate after the forecast has been calculated.

## Weapon presets

- Save normal captures the weapons currently displayed on the OwO team card.
- Save Knowledge opens a form for one to three six-character weapon IDs.
- Blank positions retain their normal weapon, and at least one position must differ.

## Deployment notes

- No database migration is required.
- No slash-command schema changed.
- No dependency or Discord permission changed.
