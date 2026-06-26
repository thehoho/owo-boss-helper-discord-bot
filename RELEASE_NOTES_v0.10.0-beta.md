# OwO Boss Helper v0.10.0 Beta

This release adds optional ticket availability markers to Discord server nicknames while keeping the existing ticket board as the source of truth.

## Optional ticket nickname markers

Server managers can open:

```text
H boss settings
HBS
/boss-ticket-manage
```

The panel now includes:

- **Enable nickname markers** — enables the feature for that server and syncs currently tracked users.
- **Disable nickname markers** — disables the feature and restores names previously managed by the bot.
- **Sync nickname markers** — reapplies the latest recorded ticket values.

The feature is disabled by default.

## Personal member controls

Every member can privately choose whether their own suffix is shown using:

```text
/boss-ticket-nickname
H boss nickname
HBN
```

The ticket board also includes a persistent **My nickname** button. Hiding a marker removes only the nickname suffix; the member remains on the ticket board and future ticket checks continue to update the stored count. Showing it again applies the latest recorded count or waits for the next ticket check.

Successful ticket checks receive a small status reaction:

- `🏷️` when the marker is active.
- `🔕` when the member opted out.
- `⚠️` when permissions, server ownership, or role hierarchy prevent the nickname edit.

## Marker format

```text
3/3 → Falcon · 🎟🎟🎟
2/3 → Falcon · 🎟🎟▫
1/3 → Falcon · 🎟▫▫
0/3 → Falcon · ▫▫▫
```

Unicode is used for this release. Custom available/used ticket emojis remain planned for the later UI-focused update.

## Safe nickname handling

- Preserves existing server nicknames.
- Preserves prefixes added by other tools, including AFK-style prefixes.
- Removes only the OwO Boss Helper ticket suffix when restoring a changed nickname.
- Stores the prior nickname and last applied nickname in `boss_tickets.db` for safe restoration.
- Automatically clears markers when a tracked entry is removed or blocked.
- Updates previously tracked members to `3/3` during the Pacific-midnight reset.
- Serializes per-user nickname changes so resets and live ticket checks do not overwrite one another incorrectly.

## Permissions and Discord hierarchy

The bot requires **Manage Nicknames** only when a server manager enables this optional feature.

The bot role must be above the members it edits. Discord does not allow a bot to edit the server owner, so the owner is skipped. Members whose highest role is equal to or above the bot role are also skipped and reported in the sync result.

The feature does not require Guild Members Intent. The bot updates only known tracked user IDs and fetches individual members when needed.

## Database migration

No manual migration is required. The bot creates these tables automatically inside the existing `boss_tickets.db`:

- `ticket_nickname_config`
- `ticket_nickname_state`
- `ticket_nickname_preferences`

Existing ticket entries, blocked users, boards, templates, cooldowns, and statistics are preserved.

## Help and documentation

`H help`, the README, the changelog, and the privacy policy now document the nickname-marker workflow, personal opt-out controls, and permission requirements. `/bot-stats` shows how many servers have markers enabled and how many members have opted out.

## Credits

Thanks to **Falcon** for suggesting visible ticket availability beside server names.

Developed and maintained by **Hassaan**.
