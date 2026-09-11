# v0.15.3-beta release notes

## Complete battle-effect set

The application emoji catalog now includes all 22 supplied current OwO battle effects. This release adds the 10 that were missing from v0.15.2:

- Exposed
- Frostbite
- Heavy Arrow
- Pest
- Sacred Ward
- Sin
- Spellskin
- Stoneskin
- Tether
- Virtue

Each has a stable `EF_` application-emoji name, a transparent lossless 128px source, an enlarged `/guide-emojis` preview, and an owner replacement target. Guide authors can use friendly variables such as `{exposed}`, `{frostbite}`, `{heavyarrow}`, `{pest}`, `{sacredward}`, `{sin}`, `{spellskin}`, `{stoneskin}`, `{tether}`, and `{virtue}`. Explicit forms such as `{EF_sacred_ward}` continue to work.

The exact Discord source IDs supplied from the official OwO Support server are recorded in `assets/game_emojis/effects/README.md`. These reference artworks are not relicensed by this repository's MIT license.

## Guide dropdown fix

Production logs showed the exact category failure: refreshing a category rebuilt the Discord view, which detached the select component and cleared its `self.view` reference before the response edit. The callback now keeps the parent browser reference before rebuilding.

Both interaction paths now acknowledge Discord immediately:

- Category selection defers, refreshes the matching guide list, then edits the browser message.
- Guide selection defers an ephemeral response, loads the guide, then renders the private guide card.

This removes both the direct callback exception and genuine three-second timeout risk. Regression tests execute both dropdown callbacks through the real rebuild lifecycle.

## Deployment

- Public version: `0.15.3-beta`
- Application emoji catalog: 334 assets, including 22 battle effects
- Database migration: none
- New Discord permissions: none
