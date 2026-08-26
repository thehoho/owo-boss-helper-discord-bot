# OwO Boss Helper v0.14.8-beta

This focused hotfix adds another verified glyph variant from OwO's current individual boss-image renderer.

## HP recognition

- Correctly reads the supplied crocodile image as `139746` instead of using the safe `80000` fallback.
- Handles the 16-pixel compound in which OwO joins the comma, `7`, and alternate `4` into one `,746` run.
- Adds the exact normalized alternate `4` as a trusted template rather than reducing the global confidence threshold.
- Preserves the existing punctuation structure, leading-zero, current/maximum, and per-glyph validation.
- Retains `80000` whenever an image genuinely cannot be read safely.

## Verification

- Crocodile `139746`: 98.9% aggregate glyph confidence.
- All previous pig, cow, owl, boss-state, team, guide, ticket, and command regressions continue to pass.
- Complete suite: 81 passing tests.

## Compatibility

- No database migration or new runtime data file is required.
- No additional Discord permission or privileged intent is required.
- Existing settings and stored data remain compatible.
