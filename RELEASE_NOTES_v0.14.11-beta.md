# OwO Boss Helper v0.14.11-beta

Phase two adds intentional empty saved teams and restores a compact, emoji-first team-guide composition.

## Empty team creation

- Replying to an official OwO team message with `HT C <name>` keeps the existing exact import flow.
- Running `HT C <name>` without a reply does not create anything immediately.
- The helper asks whether to create an empty team with that name and offers **Yes, create empty team** and **Cancel**.
- Only the member who invoked the command can use the confirmation.
- A confirmed empty team opens with position 1, 2, and 3 edit controls so it can be built manually.
- Empty creation cannot overwrite an existing same-name template; normal official-reply imports can still update one.

## Compact guide composition

- Each composition slot keeps its level or `Any level`, animal emoji and name, animal rank, weapons, passives, and weapon rank on one row.
- Optional slot notes remain below their slot and continue resolving guide emoji variables.
- Ranked standard aliases such as `gfish` now use the existing base-animal artwork (`pet_fish`) instead of falling back because `pet_gfish` does not exist.
- Displayed guide authors remain editable through **Basics**, and emoji variables remain available in summaries and full guides.
- Emoji asset replacement and quality improvements remain reserved for phase three.

## Reliability

- Team-template SQLite connections now close deterministically after each operation.
- Focused tests cover confirmation, cancellation, ownership, duplicate-name safety, official-reply compatibility, empty editing, compact rendering, ranked pet emojis, optional-note emojis, and author display.

## Compatibility

- No database migration is required.
- Existing saved teams and public guides remain compatible.
- No additional Discord permission or privileged intent is required.
