# v0.16.0-beta — XP Switching and Reliability

## Pet XP guide and calculator

- `H XP`, `HXP`, `H EXP`, and `HEXP` open a short explanation of level XP, battle outcomes, catch-up multipliers, Knowledge, and streak milestones. Custom helper prefixes are respected.
- `/xp-calc` forecasts any valid level range through level 250 from the pet's current in-level XP. It recalculates catch-up after every level instead of treating the starting multiplier as permanent.
- The calculator accepts expected raw XP per battle, battles per day, Knowledge quality from 0 to 0.15, the highest companion level, and a validated current streak up to 122,500.
- Results keep battle XP separate from hunting and fixed daily XP. They also compare the extra XP from switching every 10, 50, 100, 500, or 1,000 wins.
- Completed-streak totals preserve formula precision and round the total once, matching the supplied spreadsheet reference values: 334,391 XP at 1,000 wins, 699,772 at 2,000, and 1,906,197 at 5,000.

## Team-aware tools

The helper's existing reaction beneath an official OwO team page now opens a private five-minute panel for the member who clicked it. The panel can:

- read each visible pet's level and current/required XP;
- forecast the next level from the visible team and current streak;
- open a calculator prefilled from one of the three visible pets;
- save that exact team as the member's normal weapon preset;
- save the matching team as the member's Knowledge preset;
- open reminder settings; and
- show the existing reply command used to save a team template.

The bot does not put buttons on OwO's message—Discord does not allow one bot to edit another bot's components. It posts its own short-lived panel as a reply after the member clicks the helper reaction.

## Opt-in Knowledge switching

- Open `H reminders`, `Hreminders`, or `/xp-reminders` after saving both presets.
- Choose every 10, 50, 100, 500, or 1,000 wins, optionally disable the user mention, then explicitly enable tracking.
- The bot tracks only an enabled member's own `wb` / `owo b` command and the matching official OwO result. Ambiguous simultaneous battles are ignored instead of assigning a result to the wrong member.
- After the result immediately before a selected milestone, the bot sends copy-friendly Knowledge commands. Multi-weapon packets alternate `ww`, `wuse`, then `ww` to use OwO's independent weapon-command cooldowns.
- A tie leaves the Knowledge step active. The next win or loss sends the normal restore packet.
- Saving either preset disables tracking until the member enables it again, preventing an accidental reminder run after a team change.
- Settings, presets, result deduplication, and pending restore state survive restarts in `xp_reminders.db`.

## Two-week reliability audit

The production service stayed active without a systemd restart, kernel out-of-memory event, or storage shortage. Memory remained below roughly 465 MB; the root disk was about 23% used and the attached data volume about 28% used at audit time.

The visible lag was primarily Discord HTTP pressure, not a process crash. The journal contained roughly 102,000 rate-limit retries for message GETs over the inspected two-week window. Rapid Neon message edits were fetching the same message solely to add a reaction, so this release uses Discord partial-message objects and performs no GET for that action.

Additional containment:

- reply failures now contain a second missing-permission failure instead of surfacing an unhandled `on_message` traceback;
- `/boss-notify` omits the optional view argument when no confirmation view exists;
- saved-team dropdowns and heavier actions acknowledge the interaction before database or rendering work;
- guild-boss gateway edits remain immediate, while both REST safety polls move from one minute to five minutes to reduce background load across all configured servers.

## Data, permissions, and deployment

- New runtime database: `xp_reminders.db` (created automatically).
- New slash commands: `/xp-calc` and `/xp-reminders`.
- No new Discord permissions and no dependency changes.
- The bundled online backup script includes `xp_reminders.db`; reinstall the updated backup helper during deployment.

## Sources and credit

The implementation is based on the public [OwO Pet XP reference](https://owo.bwep.net/exp/) and its linked community streak/XP sheets. Thanks to Heather / Hasse for assembling and sharing the reference material used to verify the model. The helper remains an independent community project and does not claim affiliation with OwO Bot or NeonUtil.
