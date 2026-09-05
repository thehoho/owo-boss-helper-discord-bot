# v0.14.15-beta — Live exact-HP refresh

This hotfix makes exact boss HP follow the newest OwO fight log instead of remaining at the generator's original OCR or fallback values.

OwO's Components V2 cards store each battle-log destination in a nested URL field that is separate from the visible link label. The bot now scans the complete message payload, including those URL fields. When OwO edits the active boss card with a new scroll link, the command reply under that newest active card is edited with the latest authoritative three-pet HP values.

The working fallback log host sends JSON using chunked transfer encoding. The reader now joins the complete stream while enforcing the existing six-megabyte decoded-body limit, so partial network chunks can never be mistaken for malformed logs.

Existing replies under older boss cards are preserved. Once a newer active status card is known, an older card cannot overwrite or redirect the current command. A one-minute reconciliation pass refetches only the newest known active card as a fallback when Discord does not deliver an edit event. Immutable logs are cached, and only a log with a strictly newer OwO timestamp is applied.

No database migration or new Discord permission is required.
