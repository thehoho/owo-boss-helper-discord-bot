# v0.14.14-beta — Exact boss HP from battle logs

## What changes

Generate the three-pet Neon boss command normally with an OwO boss-inventory request and all three pages. The bot saves that command for the current guild boss.

When OwO posts an active guild-boss status card, the helper replies directly beneath it with the saved command. A card with no fight log keeps the generator's OCR values or the safe 100,000 fallback. As soon as public battle-log links appear on that card, the helper reads them and edits its existing reply with the exact post-fight HP:

    neon b myself vs ... -hp <boss 1> <boss 2> <boss 3> -m

The HP order comes from OwO's enemy-team IDs, not animal-name matching or image OCR. A defeated pet is therefore preserved as zero in its correct slot.

## Freshness and safety

- The newest log is chosen by the battle timestamp inside OwO's payload, not leaderboard rank or link position.
- A newer exact result becomes the guild's current command. An older status copy can receive that current command but cannot roll HP backward.
- Commands are bound to the boss escape timestamp. A status card for another boss is ignored.
- Unbound commands expire after 20 minutes; bound commands expire after four hours.
- Only official OwO active guild-boss layouts qualify. Mail, inventory, outcome, and unrelated messages do not.
- Malformed payloads, non-three-enemy battles, invalid HP, stale timestamps, oversized responses, and unavailable endpoints leave the existing command unchanged.
- Log fetches have bounded concurrency and a total deadline. Successful immutable logs are cached, failures retry shortly, and stored reply history is capped.

## Deployment

No database migration or new Discord permission is required. State is stored atomically beside the existing cooldown configuration and survives restarts. The existing protected production backup already covers that file.
