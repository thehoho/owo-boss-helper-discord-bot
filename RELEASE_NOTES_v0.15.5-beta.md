# v0.15.5-beta release notes

## Portable custom emojis in `H sticky`

Custom sticky notes now preserve Discord custom emojis even when OwO Boss Helper is not a member of the server that owns the original emoji.

When a helper replies to a note and runs `H sticky`, the bot:

1. Leaves Unicode emojis and usable emojis from the current server unchanged.
2. Finds each otherwise-external Discord custom emoji in the note.
3. Downloads the artwork only from Discord's validated emoji CDN URL.
4. Creates an application-owned copy and stores that portable markup in the sticky.
5. Reuses the same application emoji in future notes and servers by deriving a stable name from the original emoji ID.

Application-owned emojis belong to OwO Boss Helper and render anywhere the app is installed. They do not depend on the bot remaining in the original emoji's server or on the destination channel's **Use External Emojis** permission.

## Safety and capacity

- Maximum 20 unique external custom emojis per sticky note.
- Maximum 500 application emojis reserved for imported sticky artwork.
- The final 50 application-emoji slots remain reserved for core bot assets.
- Downloads accept only real Discord custom-emoji markup and Discord's emoji CDN.
- Existing application emojis are reused by source ID; renaming a source emoji does not create a duplicate.
- If listing, downloading, validating, or uploading any required emoji fails, the requested sticky is not saved and the existing sticky remains unchanged.
- Existing configured stickies require no migration and continue working as stored.

## Why Dyno often appeared to work already

A bot can normally use a guild emoji when it has access to the server that owns it and the destination allows external emojis. Dyno shares servers with a very large number of emoji collections, so it naturally has broader access. Application-owned copies give OwO Boss Helper predictable portability without joining those source servers.

## Deployment

- Public version: `0.15.5-beta`
- Database migration: none
- New Discord guild permissions: none
- Slash-command count: unchanged