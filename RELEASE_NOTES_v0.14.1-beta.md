# OwO Boss Helper v0.14.1-beta

This update makes Smart replace explicit and confirmation-safe, adds a small inclusive random-number tool, and introduces an information/download card for the separate TapDeck Lite Android keyboard.

## Confirmed Smart replace

- The Smart replace prompt now presents three clear configured-prefix choices:
  - `wtm` for the current active team.
  - `wsetteam 1` for Team 1.
  - `wsetteam 2` for Team 2.
- After the official OwO team page appears, the member must choose **Yes, replace this team** or **No, choose again**.
- Members may use OwO's own team-switch buttons before confirming.
- Confirmation fetches and parses the latest version of that exact OwO message, so a button-edited team page cannot leave Smart replace working from stale data.
- Choosing again keeps the same saved template and returns to the three team choices without restarting the whole flow.
- The planner still preserves correct animals and weapon IDs, resolves position cycles safely, interleaves add/equip steps, and alternates `ww` / `wuse` / `ww`.
- Any initial team cooldown wait is reduced by the time already spent reviewing and confirming the team.

## Inclusive RNG

- `/rng maximum:<value> [minimum:<value>]` rolls one inclusive whole number.
- `HRNG <maximum>` rolls from 1 through the supplied maximum.
- `HRNG <minimum>, <maximum>` rolls between two inclusive bounds.
- Full integers and K/M/B/T forms are accepted, including `100K`, `1M`, and `2.5M`.
- Successful responses contain the full integer digits only, with no abbreviated suffix.
- The implementation is isolated in `cogs/rng.py` and uses Python's `secrets` generator.

## TapDeck Lite card

- `H Grind`, `H TabDeck`, and `/tabdeck` open the information card.
- Custom server helper prefixes are respected.
- The card links to the public source and hosted privacy policy, and resolves the newest APK from GitHub's public latest-release API with a 24-hour in-memory cache.
- Publicly verifiable repository claims include zero Android permissions, no Internet capability, app-private local command storage, disabled cloud backup, and no ads, analytics, accounts, or tracking.
- The rules wording records that the demonstrated one-manual-tap/one-command workflow was reviewed and described as following the rules, while reminding members that current rules remain authoritative.
- Android sideload and third-party-keyboard warnings are explained without asking users to bypass or ignore them.

## Compatibility and privacy

- No database schema migration is required.
- No new runtime data file is introduced.
- No new Discord permission or privileged intent is required.
- RNG inputs and results are not persisted by the new cog.
- The TapDeck card is informational and does not install or execute the APK.

## Verification checklist

1. Start Smart replace and verify the prompt shows the configured equivalents of `wtm`, `wsetteam 1`, and `wsetteam 2`.
2. Switch the displayed OwO team with its message buttons, press **Yes**, and verify the plan matches the newly displayed team.
3. Press **No, choose again** and verify the same saved template returns to team selection.
4. Test `HRNG 1M`, `HRNG 100K, 2.5M`, and `/rng` with one and two bounds.
5. Confirm successful RNG output is digits only and lies inside the inclusive range.
6. Open `H Grind` and `/tabdeck`; verify the newest GitHub release tag, direct APK link, source link, and privacy link.
7. Run `H help` and `H about`; verify the new commands and `0.14.1-beta` version.
