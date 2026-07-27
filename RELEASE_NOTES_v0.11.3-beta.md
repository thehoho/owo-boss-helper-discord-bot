# v0.11.3-beta

Boss decision sticky refinement after live feedback.

## Added

- `H sticky` copies a replied message into a sticky note in the configured boss-alert channel.
- `H sticky clear` removes the sticky.
- `H sticky off` and `H sticky on` disable or enable boss stickies per server.
- `/boss-fighter-role` configures the role pinged once when a boss helper marks a boss as HIT.

## Changed

- Decision stickies now appear only after a helper chooses HIT/SKIP or creates a custom sticky note.
- The sticky reposts itself to the bottom when members chat below it.
- The running-person skip emoji was removed; SKIP is shown as text unless a custom bot emoji is available.
- New boss alerts ping the configured boss-decision role, but do not create a sticky automatically.

## Fixed

- Boss decision stickies are removed when the boss is defeated or escapes.
