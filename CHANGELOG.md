# Changelog

## 0.10.3-beta

- Kept the persistent ticket board sticky by continuing to send the replacement first and delete the previous message afterward.
- Added a cached ticket-board snapshot so Previous, Next, Text view, and Ping view no longer perform Discord member lookups.
- Coalesced bursts of ticket updates into one background board replacement instead of blocking every ticket response on a send/delete cycle.
- Changed nickname markers to explicit per-member opt-in; enabling the server feature no longer changes everyone automatically.
- Made HBS enable, disable, and sync actions return immediately while bulk nickname cleanup or opted-in synchronization runs in the background; saved explicit opt-ins are reapplied when the feature is enabled again.
- Added functional `🏷️` / `🔕` action reactions under successful ticket commands so the command author can enable or disable their own marker directly, while unrelated emoji reactions are ignored without message fetches.
- Reused the member object from the ticket command or interaction for immediate nickname changes, reducing individual member fetches and Discord 429 pressure.
- Limited bulk nickname synchronization to opted-in members and slowed background nickname edits to a safer bounded pace.
- Added automatic cleanup of nickname suffixes inherited from the old implicit-opt-in behavior.
- Added SQLite busy-timeout and synchronous settings plus timing logs for board navigation and replacement.
- Moved startup and Pacific-midnight board/nickname restoration into background queues.
- Updated the public bot version to `0.10.3-beta`.

## 0.10.2-beta

- Fixed large ticket-board button interactions timing out while visible-page member identities were fetched.
- Added immediate interaction acknowledgement, bounded concurrent member refreshes, and a 10-minute identity cache.
- Added an error response and detailed logging instead of silent `This interaction failed` messages.
- Reduced ticket-board pages to 15 entries so three custom ticket emojis per member remain safely below Discord embed limits.
- Activated application-owned UI emojis from `assets/ui_emojis/`; missing emojis are created automatically on startup and existing names are reused.
- Replaced ticket-board ticket indicators and guild-boss appeared, escaped, and defeated titles with the new application emojis.
- Added **My settings** controls for members to remove their own current board entry, pause ticket tracking completely, or resume tracking later.
- Added persistent per-server member tracking preferences in `boss_tickets.db`; no manual database migration is required.
- Kept nickname-marker reactions and personal show/hide controls.
- Updated the public bot version to `0.10.2-beta`.

## 0.10.1-beta

- Fixed HP extraction when three or more OwO pixel-font digits touch horizontally, including endings such as `444` and groups such as `744`.
- Replaced the old two-digit-only split with bounded dynamic segmentation for 2–6 touching digits.
- Preserved confidence checks, leading-zero rejection, and current-HP/max-HP validation.
- Added Text view and Ping view controls to paginated ticket boards.
- Text view now shows the stored display name, exact Discord account username, numeric user ID, ticket count, and update time.
- Ping view shows a clickable Discord member mention while retaining the exact account username and numeric ID.
- Prevented board mention views from notifying users.
- Added automatic identity refresh for the visible page using only known tracked user IDs; Guild Members Intent remains unnecessary.
- Added automatic SQLite migration for the new `account_username` ticket field.
- Prevented the bot's own ticket suffix from being saved as part of a member's board name.
- Added reserved UI emoji source-file names under `assets/ui_emojis/`.
- Updated the public bot version to `0.10.1-beta`.

## 0.10.0-beta

- Added optional per-server ticket nickname markers, disabled by default.
- Added Enable, Disable, and Sync nickname-marker controls to `H boss settings`, `HBS`, and `/boss-ticket-manage`.
- Added a persistent **My nickname** ticket-board button plus `/boss-ticket-nickname`, `H boss nickname`, and `HBN` for private member controls.
- Added per-server, per-member opt-out preferences so members can hide their own suffix without leaving the ticket board.
- Added `🏷️`, `🔕`, and `⚠️` reactions to successful ticket commands to indicate applied, personally hidden, or blocked-by-permissions nickname states.
- Added four Unicode ticket states: `🎟🎟🎟`, `🎟🎟▫`, `🎟▫▫`, and `▫▫▫`.
- Added Manage Nicknames permission checks and role-hierarchy checks before enabling or syncing markers.
- Explicitly skips the server owner and members whose highest role is equal to or above the bot role.
- Preserves existing server nicknames and compatible prefixes, including AFK-style prefixes from other bots.
- Restores managed nicknames when the feature is disabled or when a tracked user is removed or blocked.
- Reapplies `3/3` nickname markers during the existing Pacific-midnight ticket reset.
- Added persistent nickname configuration, personal preferences, and restoration state to `boss_tickets.db` with automatic schema creation.
- Updated `H help`, the README, and the privacy policy for the optional nickname feature.
- Added enabled nickname-marker server counts and personal opt-out counts to `/bot-stats`.
- Updated the public bot version to `0.10.0-beta`.

## 0.9.0-beta

- Replaced multi-message ticket boards with one paginated message using Previous and Next buttons.
- Added persistent ticket-page navigation that continues working after bot restarts.
- Added `H boss settings`, `HBS`, and `/boss-ticket-manage` for visual ticket-user administration.
- Added Remove from list, Block tracking, and Unblock actions.
- Added per-server blocked-ticket-user storage and prevented blocked users from being recorded.
- Kept `/boss-ticket-remove` for direct removal by Discord user ID or mention.
- Increased the personal team-template limit from 25 to 100.
- Added 25-item pagination to the `HT` template selector so all 100 slots remain accessible.
- Improved `H boss cd`, `H boss cooldown`, and `/boss-cooldown` reconciliation to prevent completed bosses from appearing active again.
- Added an unconfirmed status when the tracked OwO boss message is unavailable instead of reporting a possibly stale active boss.
- Updated `H help` to remove obsolete aliases and document only current commands.
- Updated the public bot version to `0.9.0-beta` and added ticket-management usage statistics.

## 0.8.0-beta

- Added the public `H about` and `/about` project profile identifying Hassaan as the developer.
- Added a clear bot description and a persistent Discord presence pointing users to `H help`.
- Added owner-only `/bot-stats` and `/bot-servers` commands.
- Added persistent `bot_stats.db` storage for active/historical servers and aggregate feature usage.
- Added private developer notifications when the bot joins or leaves a server.
- Added server reach, approximate member counts, saved-template counts, ticket-board counts, uptime, latency, database sizes, and usage summaries.
- Added `PRIVACY.md` and `TERMS.md` for public deployment.
- Added a production DigitalOcean deployment guide, hardened systemd service, and SQLite-safe backup script.
- Updated `.env.example` with developer, repository, support, description, and owner settings.
- Updated `.gitignore` to protect the operational statistics database and local backups.
- Includes the complete ticket-tracking, renamed-pet, team-update, missing-weapon, zero-ticket, reset, and admin-removal improvements developed after v0.6.2.

## 0.7.4-beta

- Changed the Pacific-midnight reset to keep previously tracked members and replenish each entry to `3/3`.
- A later OwO ticket check replaces the reset value with the user's real reported count.
- Added `/boss-ticket-remove` for server managers to remove a stale member by Discord user ID or mention.
- The persistent ticket board is replaced immediately after an admin removes an entry.
- Updated ticket-board wording to explain the reset behavior.

## 0.7.1-beta

- Fixed ticket responses being armed but not recorded when OwO's Components V2 text was missing from the high-level message object.
- Added bounded raw-message fallback reads only for explicitly awaited ticket responses.
- Added `on_raw_message_edit` handling for OwO ticket responses that finish rendering through an edit.
- Broadened ticket-count parsing to support both `3/3 boss tickets` and `3/3 tickets` wording.
- Increased the pending ticket-response window from 25 to 60 seconds.
- Added manual ticket-list commands: `H boss list`, `H boss t`, `HBL`, and `/boss-ticket-list`.
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
