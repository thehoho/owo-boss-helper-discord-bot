# OwO Boss Helper v0.13.0-beta

This release adds a privacy-limited public animal Dex catalog, trusted expert
team guides, the complete current Special/event emoji set, and clearer Neon
inventory reconciliation.

## Highlights

- Official OwO animal Dex responses teach the bot public facts such as aliases,
  rank, global caught total, economy values, base stats, description, and public
  image metadata.
- The bot never stores the requester's personal zoo count or the source command,
  message, channel, server, or user IDs with an animal record.
- `H animal dex <animal>`, `HAD <animal>`, and `/animal-dex` search the catalog
  without taking over the existing Neon weapon `H dex` command. Ordinary OwO
  Dex activity teaches the database silently and never creates a duplicate reply.
- Explicit animal results use one compact Reaction-style block with copyable
  aliases and official HP/ATT/PR then WP/MAG/MR icons. Personal Count text is
  excluded from both storage and display.
- The owner can grant trusted expert access with `/guide-expert`. Experts use
  `/team-guide-create` and `/team-guide-edit` to publish versioned guides through
  a private modal-and-button editor; everyone browses with `H guide` or
  `/team-guide` and can filter by name, alias, category, or displayed author.
- The guide editor supports unique aliases, user-defined categories, displayed
  authors, Markdown descriptions, viability/ease ratings, and three composition
  slots with animal, level, tier, weapon/passive specifications, and notes.
- The editor now explains that experts type catalog aliases and use **Preview**
  to see them resolved into visual emojis before publishing.
- Startup emoji synchronization now discovers 311 assets: six UI icons, 55
  standard animals, 173 Special/event animals, 29 weapons, 28 passives, 14
  animal-rank icons, and six official base-stat icons.
- Static game artwork is cropped and scaled to fill Discord's emoji canvas. The
  corrected game set uses revisioned internal names, so the existing 305 emojis
  remain untouched as rollback protection during the first upload.
- `H boss rebirth` and `H brebirth` repost the latest completed daily HIT/SKIP
  report for the current server.
- Both HIT and SKIP stickies keep one stable random usable custom emoji from the
  current server when one is available.
- `HW` now explains the `winv` plus Neon `/weapon inv check` comparison step.

## Storage, privacy, and permissions

- New `animal_dex.db` and `team_guides.db` files are created automatically.
- The backup helper now preserves `.env`, `boss_cooldown_config.json`, and all
  runtime databases, including `neon_weapons.db`.
- No Guild Members or Presence intent is required.
- Message Content remains required for the existing message-driven helper,
  official OwO response parsing, prefix commands, and Dex learning.
- App emoji upload uses the bot application's existing emoji capability; it does
  not require an additional server permission or privileged intent.

## Suggested smoke tests

1. Restart once and confirm startup reports 311 configured emoji assets with no
   failed uploads. The first restart may take longer while the corrected game
   artwork uploads; no existing application emoji is deleted.
2. Run `H help` and confirm the Trusted team guides and Public animal Dex fields
   appear without an interaction or embed error.
3. Run `HAD 2022pridebee`, `H animal dex 2022pridebee`, and
   `/animal-dex animal:2022pridebee`; confirm the compact layout and full-size
   stat icons.
4. Run a real OwO Dex command and confirm OwO/Reaction Bot can answer while this
   helper stays silent. Then use `HAD <animal>` to confirm the learned record.
5. As the configured bot owner, grant one test expert with `/guide-expert`, create
   a three-slot guide, publish it, search its alias, edit it, and confirm its
   version increments. Revoke access afterward if it was only a test.
6. During an active boss, test both `H boss hit` and `H boss skip`; each sticky
   should keep one server emoji through reposts.
7. Run `HW` and confirm step 5 shows the server's configured OwO inventory prefix
   and Neon's `/weapon inv check` command.
8. After at least one completed reset report exists, run `H boss rebirth` and
   confirm it reposts the latest totals in the current channel.

## Maintainer reference refresh

`scripts/sync_wiki_game_data.py` refreshes checked-in Special animal metadata,
rank icons, and weapon/passive reference facts from the OwO Bot Wiki. Review its
diff and rerun the full tests before publishing refreshed data or artwork.
