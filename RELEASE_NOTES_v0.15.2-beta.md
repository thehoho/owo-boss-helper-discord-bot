# v0.15.2-beta release notes

## Cross-channel boss guidance

The persistent sticky message still exists only in the server's configured boss channel and keeps its existing refresh and cleanup behavior.

When an official, active OwO guild-boss card is posted in another channel, the helper now sends:

1. The generated Neon battle command with the latest known HP.
2. A separate, one-time copy of the current configured HIT, SKIP, or custom decision.

A decision is mirrored only when the server has an enabled, active sticky decision. The bot does not mirror expired or inactive decisions, does not mirror into the configured channel, and records the attempt on the source card so gateway replays, restarts, or later exact-HP edits cannot spam it. HP refreshes continue to edit only the command reply.

## Battle-effect emojis

The application emoji catalog now includes 12 official OwO battle effects:

- Attack Up, Attack Up+, and Attack Up++
- Celebration
- Defense Up
- Flame
- Freeze
- Leech
- Mortality
- Poison
- Stinky
- Taunt

They use stable `EF_` application-emoji names and are available in `/guide-emojis` and the owner replacement tools. Guide authors can use friendly variables such as `{taunt}`, `{poison}`, `{freeze}`, and `{defup}`, or explicit forms such as `{EF_taunt}` and `{effect_poison}`. Existing weapon and passive resolution remains unchanged.

The lossless 128-pixel sources and Discord IDs are pinned to OwO Bot's published source commit. Attribution and source paths are recorded in `assets/game_emojis/effects/README.md`; the artwork is not relicensed under this repository's MIT license.

## Guide categories

Running `H guide` or `/team-guide` without a search now opens a category dropdown built from the categories on currently saved guides. Category spelling and whitespace variants are merged for counting and filtering. The browser shows the newest matching guides, while selecting a guide opens its full details privately so public browsing does not flood the channel.

Exact guide-name and alias searches continue to work as before. No category migration or hard-coded category list is required.

## Deployment

- Public version: `0.15.2-beta`
- Database migration: none
- New Discord permissions: none
- Startup uploads any missing effect application emojis through the existing emoji synchronization path.
