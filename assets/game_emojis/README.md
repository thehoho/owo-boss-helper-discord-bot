# Game emoji source assets

These files are source artwork for application-owned Discord emojis. The bot
normalizes static files to transparent PNG and preserves small animated WebP
files when Discord accepts them.

## Included sets

- `animals/`: 55 standard OwO animals from Common, Uncommon, Rare, Epic,
  Mythical, Gem, Legendary, Fabled, Bot, Hidden, and Distorted ranks, plus 173
  Special/event animals from the current wiki catalog.
- `ranks/`: 14 animal-rank icons from Common through Distorted, including
  Patreon, Custom Patreon, Special, and Bot ranks.
- `weapons/`: 29 weapon icons from the OwO Boss Command Generator desktop app.
- `passives/`: 28 passive icons from the OwO Boss Command Generator desktop app.
- `stats/`: six official HP, ATT, PR, WP, MAG, and MR base-stat icons.

Together with the six interface emojis in `assets/ui_emojis/`, startup discovers
311 configured application-owned emojis. Static artwork is cropped to its visible
alpha bounds and scaled to fill Discord's canvas. The game catalog uses a revised
Discord-side name set so corrected artwork can upload without deleting the previous
working emojis. Patreon and Custom Patreon animals are not
bulk-included because those catalogs are open-ended; official Dex responses can
still teach their public facts to the runtime animal catalog.

## Provenance

Standard animal artwork was retrieved from the rendered
[OwO Bot Wiki All Animals catalog](https://owobot.fandom.com/wiki/All_Animals)
on 3 August 2026. Special/event thumbnails and rank icons were retrieved from
the same wiki through its MediaWiki API on 4 August 2026. The checked-in
`data/special_animals.json` records the source page, source filenames, and local
asset paths. The wiki states
that community content is available under
CC BY-SA unless otherwise noted; individual image pages may carry additional
source or license information.

Weapon and passive artwork came from the project-owned desktop source tree at
`D:\OwO-Boss-Command-Generator\assets`.

The six base-stat icons were supplied from their OwO Bot Wiki reference pages:
[Health Points](https://owobot.fandom.com/wiki/Health_Points),
[Physical Attack](https://owobot.fandom.com/wiki/Physical_Attack),
[Physical Resistance](https://owobot.fandom.com/wiki/Physical_Resistance),
[Weapon Points](https://owobot.fandom.com/wiki/Weapon_Points),
[Magical Attack](https://owobot.fandom.com/wiki/Magical_Attack), and
[Magical Resistance](https://owobot.fandom.com/wiki/Magical_Resistance).

These artwork files are not relicensed by the repository's MIT license. Their
respective source terms continue to apply.
