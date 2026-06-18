# Changelog

## 0.5.0-beta — 2026-06-19

- Reworked guild-boss discovery to avoid REST-fetching every OwO message.
- Discovery now uses gateway payloads and runs only while a boss is active or the guild is able to spawn one.
- During a five-minute defeat cooldown, unrelated OwO traffic is ignored.
- Only the single tracked boss message is polled every 15 seconds.
- Added per-guild fetch locking and staggered restored watchers to reduce request bursts.
- Updated Bleeding Gaze to use the normal blueprint order: weapon values first, Weapon Cost last.
- Preserved the `H` helper commands, rotating logs, HP recognition, alerts, and cooldown rules.

- Added rotating file logs in `logs/bot.log`.
- Added `H boss cd` and `H boss cooldown` public status commands.
- Active-boss status now shows the expected escape time using Discord timestamps.
- Added automatic new-guild-boss announcements in the configured alert channel.
- New-boss announcements include instructions to run `owo boss i` or `w boss i`.
- The latest active boss message continues to replace older status messages.
- The 15-second watcher can mark an escaped boss ready directly from the stored expiry time, even if OwO has not edited the card yet.
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
