# OwO Boss Helper v0.14.4-beta

This update makes Smart Replace faster and clearer, recognizes OwO's refreshed team page after deletions, expands team-selection aliases, and corrects TapDeck's public rules wording.

## Faster Smart Replace planning

- When both weapon and team work are required, the first guided command is a weapon correction. This uses the team cooldown that already began when the member displayed the team instead of immediately asking for another team command.
- The remaining plan interleaves team edits and weapon equips where possible.
- Generated weapon commands continue alternating `ww` and `wuse`.
- Already-correct animals, positions, and weapon IDs remain untouched.

## Reliable delete progression

- A requested position deletion is now confirmed when OwO responds with a refreshed team page where that exact slot is absent.
- The confirmation remains fail-closed: an unrelated incomplete page does not confirm deletion of a different slot.
- If the next command shares OwO's team, weapon, or use cooldown, the helper posts a visible confirmation-and-wait status with the remaining seconds.
- The next guided command appears automatically as soon as that cooldown is safe.

## More team-selection aliases

Smart Replace now accepts both forms for saved OwO teams, using the server's configured OwO prefix:

- `setteam 1` or `teams 1`
- `setteam 2` or `teams 2`
- The existing current-team command remains supported.

## Correct TapDeck rules wording

The TapDeck card and `H help` now state that the demonstrated **one tap = one command** workflow was shared with OwO's staff team for review and confirmed as allowed under the rules at the time. The card continues to remind members that rules can change and that current OwO and Discord rules remain authoritative.

## Compatibility and privacy

- No database schema migration is required.
- No new runtime data file is introduced.
- No new Discord permission or privileged intent is required.
- Existing saved teams, guides, prefixes, tickets, reports, animal Dex data, and Neon weapon data remain compatible.

## Verification checklist

1. Open Smart Replace and verify the prompt lists the configured equivalents of `setteam 1`, `teams 1`, `setteam 2`, and `teams 2`.
2. Scan a team needing both edits and weapon corrections; verify the first guided command is a weapon command.
3. Verify later weapon commands alternate `ww` and `wuse` while team and weapon actions interleave where possible.
4. Delete a required position and verify OwO's refreshed two-animal team page advances the guided session.
5. Trigger consecutive team commands and verify the helper visibly reports the confirmation and cooldown wait before posting the next command automatically.
6. Open `H Grind`, `H TapDeck`, `/tapdeck`, and `H help`; verify the staff-team review wording and current-rules disclaimer.
7. Run `H about` and verify version `0.14.4-beta`.