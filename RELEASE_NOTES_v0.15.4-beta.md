# v0.15.4-beta release notes

## Both boss-notify command formats

Members can now open and manage boss notifications with either spacing style:

- `H boss notify`
- `Hboss notify`

Arguments work identically in both forms, for example `Hboss notify wc x2` and `H boss notify wc x2`. The alias is generated from the server’s configured helper prefix, so a server using `B` accepts both `B boss notify` and `Bboss notify`; the old `H` forms remain disabled there.

The notification guide and main help card display both forms. `/boss-notify` remains unchanged.

## TapDeck Lite on Google Play

`H Grind`, `H TapDeck`, and `/tapdeck` now show **Get it on Google Play** as the first button, and the card title opens the same listing:

`https://play.google.com/store/apps/details?id=app.tapdeck.keyboard.lite`

The package ID comes from TapDeck Lite’s public Android build metadata. Google Play can take time to propagate a new production listing across accounts and regions, but the listing URL itself is stable for that application ID.

The existing GitHub options remain available:

- Latest release APK
- Release notes
- Public source
- Privacy policy

Google Play is presented as the standard installation path. The unknown-source warning now refers only to members who intentionally choose the GitHub APK fallback. No tester-group link is included.

## Deployment

- Public version: `0.15.4-beta`
- Database migration: none
- New Discord permissions: none
- Slash-command count: unchanged
