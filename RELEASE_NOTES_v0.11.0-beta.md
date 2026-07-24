# OwO Boss Helper v0.11.0 Beta

This release starts the v0.11 line with longer Neon dex sessions, skip controls for stale weapon IDs, and better developer navigation for servers using the bot.

## Long Neon dex sessions

- `HWD` now supports optional session lengths:
  - `HWD 100`
  - `HWD 250`
  - `HWD all`
  - `HWD mtap 100`
  - `HWD dagger mtap 50`
  - `HWD @member all`
- The default remains 20 queued weapons.
- Large requests are capped at 1,000 commands for safety.
- Active dex sessions can run for several hours instead of expiring after the old short window.
- Dex prompts show progress while keeping the copyable command short.

## Dex skip controls

- Added `HWD skip` / `H dex skip` / `H weapon dex skip`.
- Added a **Skip weapon** button on the active dex prompt.
- Skipping removes the current weapon ID from that owner’s queue, which is useful when a weapon was sold, dismantled, or no longer exists.
- If Neon sees that weapon ID again later, it can be added back by a fresh scan.

## Confirmation behavior preserved

- Sessions still alternate `ww <weapon_id>` and `wuse <weapon_id>` to reduce same-command cooldown waiting.
- The helper still only advances after OwO replies and Neon confirms the weapon.
- If cooldown blocks the command or Neon does not confirm, the prompt stays on the same weapon.

## Developer server navigation

- `/bot-servers` now has Previous, Next, and Refresh buttons.
- Server rows now show the current Discord guild owner ID/mention.
- The bot does not create invite links automatically. If a guild has an existing vanity code visible to the bot, it may be shown as a safe hint.

## Notes

Team editing and ordering controls are planned as the next v0.11 phase because they require careful template mutation logic and more interactive UI testing.

### Phase 2A server insight controls
- Added `/bot-server server_id:<id>` for owner-only server detail views.
- Added per-server usage breakdown tracking for commands and slash commands.
- Added future join inviter detection when audit logs are available.
- Invite behavior stays safe: the bot does not create server invite links automatically.
- Added a daily owner DM report around reset time, configurable with `BOT_DAILY_REPORT_UTC_HOUR` and `BOT_DAILY_REPORT_UTC_MINUTE`.

### Team editor and saved-team order controls

This release adds the final v0.11 team-management layer:

- open a saved-team editor with `HTE <team>` or `HT edit <team>`;
- edit position 1, 2, or 3 with Discord modals;
- rename teams without replacing them from a fresh OwO page;
- move or swap saved teams with `HT move`, `HT swap`, and `HT order`;
- use the **Edit order** button from the saved-team list.

Discord does not support real drag-and-drop inside bot messages, so the helper uses safe buttons, selectors, and explicit move/swap commands.
