# OwO Boss Helper v0.14.12-beta — Phase three

## Find guide icons

Run `/guide-emojis`. Choose Animals, Weapons, Passives, Ranks, Stats, or Other; search by a name or alias, page through the results, and choose an icon for its enlarged preview.

Copy the displayed **Guide syntax** into a summary, full guide, or optional slot note. Exact registry keys also work: `{weapon_sword}`, `{passive_snail}`, `{stat_hp}`, and `{clown}`. Exact syntax avoids alias collisions. The guide editor's **Emoji variables** button includes this browser.

## Replace artwork (bot owner only)

1. Run `/emoji-replace target:weapon_sword`.
2. Provide exactly one source:
   - `source`: select/paste a custom Discord emoji, or
   - `image`: attach the original PNG, JPEG, WebP, or GIF.
3. Inspect the proposed large preview and current-emoji thumbnail.
4. Press **Use this emoji**, or **Cancel**.

The new application emoji works globally wherever the bot renders that logical icon. Existing guide names and saved team data do not change. Open a guide again to see new artwork; already-posted messages retain their previous emoji IDs.

Use `/emoji-reset target:weapon_sword` and confirm to restore the packaged artwork. Reset does not delete the owner's uploaded Discord emoji. Old application emojis are retained for historical messages and count toward Discord's application limit. Reusing identical artwork reuses its existing application emoji. If capacity is reached, the change is refused rather than deleting icons.

Only `BOT_OWNER_ID` can modify artwork when configured. If it is not configured, discord.py's application-owner check applies. Server administrators and trusted guide authors do not gain replacement access. Previews expire after three minutes, and only their invoking owner can confirm. A newer change invalidates old previews, including after a reset.

## Quality and limits

- Static game assets use a revision-three application name and fill a centered 128×128 canvas after transparent padding is cropped.
- Small pixel art is enlarged with nearest-neighbor sampling; larger originals are downsampled with Lanczos. Higher-resolution source art can improve detail; enlarging a low-resolution source cannot restore missing detail.
- Existing supported animations are retained. Owner uploads accept at most 2 MiB and 4 megapixels. Animated GIF/WebP uploads must already be at most 128×128, 200 frames, and 256 KiB; larger animations are rejected rather than flattened.
- Discord controls inline emoji display size. The reference browser offers a larger preview; this does not change guide text sizing.
- Application emoji upload format and size constraints follow [Discord's emoji API](https://docs.discord.com/developers/resources/emoji).

## Persistence and deployment

`emoji_overrides.db` contains normalized artwork, active mappings, and an owner audit trail. The bundled backup script includes this database. Keep it during migrations and restores, and install the updated backup helper when deploying this version. Never commit runtime databases or credentials.

Startup reuses saved replacements, recreates missing remote replacements from saved artwork when possible, and falls back to packaged/previous-version icons if an upload fails. New artwork is uploaded before a replacement is committed; existing active mappings survive failed uploads or database transactions. An uploaded but uncommitted emoji can be reused on retry.

The first deployment creates revision-three game icons without deleting revision-two IDs. This can take longer than a normal restart; old icons remain available while synchronization runs.

This release does not change boss status tracking, HP recognition, reward parsing, notifications, or team-import behavior.
