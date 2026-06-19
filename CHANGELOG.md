# Changelog

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
