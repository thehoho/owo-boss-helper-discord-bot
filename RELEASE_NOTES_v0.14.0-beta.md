# OwO Boss Helper v0.14.0-beta

Saved team templates can now inspect the member's active OwO team and guide only the changes required to match the selected template.

## Smart replace

- The former **Quick replace** button is now **Smart replace**.
- Clicking it starts a private-to-user request plus a public guided prompt for the server's configured team display command, such as `wtm`.
- The helper waits for the following official OwO response and reads its three animal positions and six-character equipped weapon IDs.
- Already-correct animal positions and exact weapon IDs are preserved.
- Unrelated occupants are replaced directly only when the target animal is absent from the team.
- Two- and three-animal position cycles are resolved deterministically with one safe delete followed by ordered adds, avoiding OwO's duplicate-animal rejection.
- If the only team animal must move and OwO would refuse to delete the final pet, Smart replace stops with a clear instruction to add a temporary animal and retry.

## Cooldown-aware guided commands

- Each changed animal's required weapon equip appears immediately after its team add, using the five-second team-cooldown window instead of waiting idly.
- Every generated weapon sequence alternates `ww` / `wuse` / `ww`, including Smart replace, Exact reset, and **All commands**.
- Consecutive team edits with no weapon step between them wait five seconds before the next prompt in Smart replace and Exact reset.
- The first team edit also waits when it immediately follows the team page used for the scan.
- The final verification still uses the server's configured OwO team command.

## Compatibility

- **Exact reset** remains available for a full rebuild.
- **All commands** remains available as the complete manual restore packet.
- Per-server OwO prefixes work across the scan, team edits, `w` weapon commands, and `use` weapon commands.
- Existing saved templates and `team_templates.db` require no migration.
- No Server Members or Presence intent is used.
- The scanned current-team page is held only in the active in-memory session and is not added to a new database.
- Smart replace relies on the Message Content intent already required by the bot's core OwO-response features.

## Verification coverage

Focused tests cover:

1. A team that already matches exactly.
2. Correct animals with only weapon changes.
3. Missing target animals replacing unrelated occupants.
4. Two-animal swaps.
5. Three-animal cycles.
6. The final-animal safety edge case.
7. Custom OwO prefixes and accepted team display aliases.
8. Official team-page parsing and exact weapon IDs.
9. `wuse` success confirmations.
10. Team cooldown delays, immediate add/equip interleaving, and `ww` / `wuse` / `ww` alternation in every command generator.

## Suggested Discord smoke test

1. Open a saved team with `HT1` and click **Smart replace**.
2. Send the requested team command, normally `wtm`.
3. Confirm the scan summary counts only genuinely different positions or weapon IDs.
4. Complete each guided command and confirm OwO advances the helper.
5. Test changed animals and confirm each add is immediately followed by its equip while equips alternate `ww`, then `wuse`, then `ww`.
6. Test two animals in swapped positions and confirm Smart replace opens the cycle with one delete before adding both animals to their saved positions.
7. Run the final `wtm` check and compare all three animals and weapon IDs with the saved card.
8. Run `HT cancel` during a fresh Smart scan and during an active guided plan.

## Source validation

The command plan was checked against OwO Bot's public source behavior:

- `team add` accepts positions 1–3 and replaces the occupant at that position.
- Adding an animal already present elsewhere is rejected.
- `team remove` accepts an animal or position and refuses to remove the final team member.
- The published command cooldowns are three seconds for `team` and five seconds for `weapon` / `use`; Smart replace uses a conservative five-second team pause.
