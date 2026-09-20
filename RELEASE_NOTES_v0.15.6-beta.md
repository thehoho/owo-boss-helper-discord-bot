# v0.15.6-beta release notes

## Use the bot's existing emojis in `H sticky`

`H sticky` now understands the same emoji variables already supported by team guides. Write familiar names inside braces in the note, then reply to it with `H sticky`.

Examples:

- Weapons: `{sword}`, `{pdagger}`, `{arcane}`
- Passives: `{crit}`, `{lifesteal}`, `{mtap}`
- Battle effects: `{taunt}`, `{poison}`, `{freeze}`
- Animals: `{dog}`, `{gfish}`, `{hsquid}`
- Ranks: `{common}`, `{mythical}`, `{fabled}`
- Stats: `{hp_stat}`, `{wp_stat}`, `{mag_stat}`
- Exact keys also work, such as `{weapon_sword}` and `{passive_crit}`

Use `/guide-emojis` to browse and search the complete catalog, see enlarged previews, and find aliases.

## No automatic external imports

This release removes v0.15.5's automatic copying of arbitrary external server emojis. Sticky notes now reuse only application emojis the bot already owns, so this feature consumes no additional application-emoji capacity.

Direct Discord custom-emoji markup remains unchanged in the saved note. It will render only when Discord allows the bot to use that source emoji. Unicode emojis, Markdown, links, and ordinary text remain unchanged.

Known variables are converted before the sticky is stored, so reposts and cross-channel mirrors use the bot-owned emoji IDs. Unknown brace variables remain as readable text and the save confirmation lists them as a typo warning.

## Deployment

- Public version: `0.15.6-beta`
- Database migration: none
- New application emojis: none
- New Discord guild permissions: none
- Slash-command count: unchanged