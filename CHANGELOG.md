# Changelog

## 0.7.1-beta

- Fixed ticket responses being armed but not recorded when OwO's Components V2 text was missing from the high-level message object.
- Added bounded raw-message fallback reads only for explicitly awaited ticket responses.
- Added `on_raw_message_edit` handling for OwO ticket responses that finish rendering through an edit.
- Broadened ticket-count parsing to support both `3/3 boss tickets` and `3/3 tickets` wording.
- Increased the pending ticket-response window from 25 to 60 seconds.
- Added manual ticket-list and persistent-board refresh commands:
  - `H ticket list`
  - `H tickets`
  - `H boss list`
  - `H boss t`
  - `H buzz list` / `H buzz t`
  - `T list`
  - `HTL`
  - `/boss-ticket-list`
- Added ticket-board refresh logs showing entry and page counts.
- Updated `H help` with the new ticket-list commands.
- Replaced the accumulated README with a single cleaned and current guide.

## 0.7.0-beta

- Added persistent per-server guild-boss ticket tracking.
- Added `/boss-ticket-channel` for choosing the ticket-board channel.
- Added automatic capture of `owo boss t`, `owo boss ticket`, `w boss t`, and `w boss ticket` results.
- Added persistent paginated ticket boards containing usernames, Discord IDs, reported ticket counts, and update times.
- Added automatic daily ticket replenishment at `America/Los_Angeles` midnight with DST-safe Discord timestamps.
- Added local SQLite storage in `boss_tickets.db`.
- Added team-template validation for missing positions.
- Stopped silently dropping animals that do not have an equipped weapon.
- Added an explicit Save without weapons / Cancel confirmation flow.
- Added `HT U <number or name>`, `HTU`, and `H team update` for updating an existing stable template slot from a fresh OwO team page.
- Added before-and-after summaries after template updates.
- Updated restoration packets so positions without a saved weapon omit only the unavailable `ww` command.
- Extended `H help` with boss-ticket tracking instructions.

## 0.6.3-beta

- Fixed team templates for renamed pets by reading the animal's OwO emoji alias instead of the visible custom nickname.
- Normalized rank-prefixed standard aliases such as `gfish`, `gspider`, `llion`, `deagle`, and `hlizard` to normal OwO command names.
- Preserved unknown custom and event pet emoji aliases exactly rather than incorrectly replacing them with the displayed nickname.
- Added support for both Discord emoji payload forms: `<:name:id>` and pasted `:name:` text.
- Updated `HT help` to explain that emoji identity is used when saving teams.

## 0.6.2-beta

- Removed the artificial delay between confirmed guided setup steps.
- Interleaved animal-add and weapon-equip commands for faster team switching.
- Added `HS`, `H skip`, `H escape`, and `HT skip` guided-step shortcuts.
- Added owner-only **Skip step** and **Cancel** buttons to each guided prompt.
- Added live handling for OwO's short-lived `This animal is already in your team!` error.
- Simplified duplicate-animal recovery with `wtm d <animal>` before retrying the add command.
- Added occupied-position handling while keeping the current step active.
- Treated already-equipped weapon responses as successful steps.
- Added optional cleanup of completed user command messages after OwO responds.
- Preserved concurrent guided sessions by server, channel, and user.
- Expanded the project credits to recognize Pencilvester's original saved team-template with weapon-ID idea.

## 0.6.1-beta

- Increased the personal team-template limit from 10 to 25.
- Added stable numbered slots from 1–25 for every user's templates.
- Added automatic migration for existing `team_templates.db` files.
- Added `HT`, `HTM`, `HT 3`, and `HTM 3` shortcuts.
- Added compact create commands such as `HT C <name>` and `HTC <name>`.
- Added deletion by number or name using `HT D`, `HTD`, and full commands.
- Added guided Quick replace and Exact reset sessions.
- Guided mode waits for the user's exact command and OwO's confirmation before continuing.
- Added a five-second safety delay between confirmed steps.
- Added retry behavior when OwO reports a cooldown or does not confirm a step.
- Added concurrent guided-session handling for multiple users and channels.
- Added `HT cancel` to stop an active guided session.
- Added a final `wtm` verification prompt after setup completes.
- Kept rotating file logs; SQLite remains dedicated to structured template data.

## 0.6.0-beta

- Added personal OwO team templates with a limit of 10 per user.
- Added `H team create <name>` / `H team save <name>` by replying to an OwO team page.
- Preserved exact six-character weapon IDs alongside animal names and positions.
- Added `H team` with a private dropdown-based template selector.
- Added Quick Replace and Exact Reset command packets.
- Added `H team delete <name>` and `H team help`.
- Added a save reaction to recognized OwO team pages.
- Added persistent SQLite storage in `team_templates.db`.
- Extended `H help` with team-template instructions.

## 0.5.1-beta — 2026-06-19

- Sends generated Neon commands as normal Discord messages using single-backtick inline code instead of placing them inside embeds.
- Adds a short 1.25-second outcome settling window before defeat or escape notifications.
- Serializes outcome handling per guild so simultaneous watcher and gateway events cannot send the same alert twice.
- Deduplicates completed bosses using the tracked boss expiry as a stable boss identity, even when OwO replacement messages expose slightly different result timestamps.
- Prevents a delayed result from clearing or announcing over a newer active boss.
- Reworked guild-boss discovery to avoid REST-fetching every OwO message.
- Uses gateway payloads for discovery and polls only the single tracked boss message every 15 seconds.
- Added per-guild fetch locking and staggered restored watchers to reduce Discord API request bursts.
- Updated Bleeding Gaze to use the normal blueprint order: weapon values first, Weapon Cost last.

## 0.5.0-beta — 2026-06-18

- Added rotating file logs in `logs/bot.log`.
- Added `H help` with a focused command and setup guide.
- Added `H boss cd` and `H boss cooldown` public status commands.
- Active-boss status now shows the expected escape time using Discord timestamps.
- Added automatic new-guild-boss announcements in the configured alert channel.
- New-boss announcements include instructions to run `owo boss i` or `w boss i`.
- The latest active boss message continues to replace older status messages.
- Corrected guild-boss timing: only defeat starts a five-minute cooldown.
- Escaped bosses are marked ready immediately and can be replaced without a cooldown.
- The 15-second watcher can mark an escaped boss ready directly from the stored expiry time, even if OwO has not edited the card yet.
- Added migration cleanup for incorrect escape cooldowns saved by earlier beta builds.
- Improved outcome deduplication so a new boss can legitimately appear and end immediately after an escape.
- Preserved HP extraction, correct `1/3 → 2/3 → 3/3` ordering, cooldown persistence, and mobile-friendly command output.

## 0.4.1-beta — 2026-06-18

- Fixed touching HP digits such as `74,589`.
- Added stricter HP validation.
- Improved active-boss status handling.

## 0.4.0-beta — 2026-06-18

- Added automatic HP extraction from individual boss images.
- Added bundled digit templates for `0–9`, comma, and slash.
- Changed generated commands to mobile-friendly inline code.
- Added latest guild-boss message tracking with a 15-second watcher.
- Preserved cooldown channel and watcher state across restarts.
- Kept authoritative boss ordering based on the visible `1/3`, `2/3`, and `3/3` counter.
- Removed the Change HP button from the public workflow.
