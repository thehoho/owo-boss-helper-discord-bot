# v0.15.7-beta release notes

## Reliable Pacific-midnight nickname refill

The daily boss-ticket reset now synchronizes every server where ticket nickname markers are enabled, even when another read normalized that server's database rows just before the reset worker ran.

Nickname synchronization runs directly after stale-entry cleanup and before the slower ticket-board refresh fan-out. Every still-active, explicitly opted-in member is therefore updated to the new `3/3` marker without depending on the old `changed_guilds` result or the generic nickname-job queue.

Failures are isolated per server and included in the reset log, so one server cannot prevent the remaining servers from refreshing.

## Collaborative Neon dex sessions

A guided Neon dex session now follows the expected channel and weapon ID instead of requiring the person who started the session to paste every command.

Another helper may send the displayed `ww <weapon_id>` or `wuse <weapon_id>` command. Once OwO and Neon confirm that exact weapon, every matching active session in that channel advances. Unrelated channels and unrelated weapon IDs remain isolated.

A valid Neon blueprint also advances the session when the weapon was already saved by an earlier helper, preventing an already-dexed weapon from leaving the queue stuck.

## Deployment

- Public version: `0.15.7-beta`
- Database migration: none
- New Discord permissions: none
- Slash-command count: unchanged