# Application-owned UI emoji source files

Keep the artwork here using these exact names:

- `ticket_available.png`
- `ticket_used.png`
- `boss_appeared.png` — sword/new boss artwork
- `boss_escaped.png`
- `boss_defeated.png`

Use transparent PNG files with the important artwork centered and readable at emoji
size. Discord application emoji uploads must remain below 256 KiB each.

`cogs/ui_emojis.py` lists the application's emojis at startup, reuses matching
names, and creates any missing names from these files. Uploading the PNGs to the
repository is intentional: the repository stores the source artwork, while Discord
stores the application emoji objects and IDs used in messages.

If an existing artwork file is replaced later, delete the matching application emoji
from the Discord Developer Portal once. The next bot restart will recreate it from
the new PNG.

Do not place UI artwork in or replace the recognition templates under
`assets/hp_digits/`.
