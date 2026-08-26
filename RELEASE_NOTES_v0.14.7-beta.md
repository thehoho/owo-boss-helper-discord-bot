# OwO Boss Helper v0.14.7-beta

This focused hotfix improves HP recognition for another compact punctuation shape in OwO's current individual boss images.

## HP recognition

- Detects the five-pixel compound formed when OwO renders its two-pixel thousands comma directly against the narrow digit `1`.
- Correctly reads the supplied pig image as `103177` and cow image as `125175` instead of using the safe `80000` fallback.
- Preserves the existing comma-touching support for the earlier cow `166463` and owl `207864` cases.
- Keeps the strict requirement for exactly one comma in a compound punctuation run.
- Keeps per-glyph and average-confidence thresholds, leading-zero rejection, and current/maximum HP validation.
- Retains `80000` when an image genuinely cannot be read safely.

## Verification

- Pig `103177`: 98.4% aggregate glyph confidence.
- Cow `125175`: 100% aggregate glyph confidence.
- Earlier cow `166463`: 99.5% aggregate glyph confidence.
- Earlier owl `207864`: 98.9% aggregate glyph confidence.
- Complete suite: 80 passing tests.

## Compatibility

- No database migration or new runtime file is required.
- No additional Discord permission or privileged intent is required.
- Existing boss tracking, reports, ticket boards, teams, guides, prefixes, and stored data remain compatible.
