# v0.14.13-beta — Complete emoji picker and Animal Dex artwork

## Owner replacement workflow

Run `/emoji-replace` with no arguments. Choose a category, page through every target, and choose an icon. **Replace selected** opens a box for a custom Discord emoji, followed by the existing preview/confirmation.

For an original uploaded image, use `/emoji-replace target:W_sword image:<attachment>`. You can also attach an image without a target and select its target from the paginated picker. The optional category filters autocomplete, but [Discord limits autocomplete responses to 25 suggestions](https://docs.discord.com/developers/interactions/receiving-and-responding); pages make the complete catalog reachable.

The picker shows the number of manually replaced icons in the selected category and whether the selected animal uses Dex artwork. All 29 weapons, 28 passives, and 6 stats remain separate targets.

## Consistent names

- Passives: `PS_crit`
- Weapons: `W_sword`
- Stats: `ST_hp`
- Animals: `AN_fish`
- Ranks: `R_legendary`
- Other icons: `UI_clown`

Use these in braces in guides, for example `{PS_crit}`, `{W_sword}`, and `{AN_fish}`. Existing guide aliases (including the previous `BS_` prefix) and exact logical keys still work. Actual Discord application names include a compact version/hash suffix to distinguish retained artwork versions. Active legacy emojis are renamed, not deleted or reuploaded: IDs, artwork, and historical messages are preserved.

## Animal Dex artwork

After startup, a background job imports original Discord emoji artwork recorded from official OwO Dex messages, including custom animals beyond the bundled catalog. It accepts only canonical Discord emoji CDN sources, retains animation within the existing safe upload limits, and saves normalized bytes and source metadata in `emoji_overrides.db`.

Manual replacements always win over automatic Dex imports. **Reset** for a Dex-backed animal restores its saved Dex artwork; other icons reset to packaged artwork. Source URLs already imported are reused on subsequent restarts.

Run owner-only `/emoji-dex-sync` for the JSON report of missing and failed sources. While work is running it reports that status. After collecting fresh official OwO Dex messages, run `/emoji-dex-sync refresh:true` to pick them up.

An animal record is not necessarily an image source: seeded records may have no original image saved. Missing sources retain packaged artwork where available; animals that lack both a valid source and packaged artwork are listed rather than given an invented icon. Unsupported names, oversized animations, unavailable CDN images, and application capacity failures are reported. No existing manual artwork is overwritten to accommodate them.

## Deployment

The existing protected database backup includes the new Dex image table; no extra secret or permission is required. First-time name normalization and artwork import take longer than an ordinary restart. Old IDs remain usable, and Dex imports run separately from normal boss tracking after initial registry synchronization. No boss parsing, rewards, or HP-reading behavior changes.
