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
- `H animal dex <animal>` and `/animal-dex` search the catalog without taking
  over the existing Neon weapon `H dex` command. When OwO cannot display
  an animal that the requester does not own, the bot can return its latest cached
  public record.
- The owner can grant trusted expert access with `/guide-expert`. Experts use
  `/team-guide-create` and `/team-guide-edit` to publish versioned guides through
  a private modal-and-button editor; everyone browses with `H guide` or
  `/team-guide` and can filter by name, alias, category, or displayed author.
- The guide editor supports unique aliases, user-defined categories, displayed
  authors, Markdown descriptions, viability/ease ratings, and three composition
  slots with animal, level, tier, weapon/passive specifications, and notes.
- Startup emoji synchronization now discovers 305 assets: six UI icons, 55
  standard animals, 173 Special/event animals, 29 weapons, 28 passives, and 14
  animal-rank icons. Existing names are reused and unrelated emojis are never
  removed.
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

1. Restart once and confirm startup reports 305 configured emoji assets with no
   failed uploads. The first restart may take longer while missing images upload.
2. Run `H help` and confirm the Trusted team guides and Public animal Dex fields
   appear without an interaction or embed error.
3. Run `H animal dex 2022pridebee` and `/animal-dex animal:2022pridebee`.
4. Run a real OwO Dex command for an owned animal, then look it up through the
   helper. Try an unowned but already-cached animal and confirm the fallback.
5. As the configured bot owner, grant one test expert with `/guide-expert`, create
   a three-slot guide, publish it, search its alias, edit it, and confirm its
   version increments. Revoke access afterward if it was only a test.
6. During an active boss, test both `H boss hit` and `H boss skip`; each sticky
   should keep one server emoji through reposts.
7. Run `HW` and confirm step 5 shows the server's configured OwO inventory prefix
   and Neon's `/weapon inv check` command.

## Maintainer reference refresh

`scripts/sync_wiki_game_data.py` refreshes checked-in Special animal metadata,
rank icons, and weapon/passive reference facts from the OwO Bot Wiki. Review its
diff and rerun the full tests before publishing refreshed data or artwork.
