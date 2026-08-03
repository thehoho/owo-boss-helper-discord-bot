# OwO Boss Helper v0.12.0-beta

This phase repairs the public help command, gives every server its own helper
prefix, and prepares a much larger application-owned game emoji catalog.

## Highlights

- `H help` works again. The old duplicate help override was removed, and the new
  guide stays within Discord's embed limits.
- Server managers can change the default `h` helper prefix with
  `/helper-prefix` or `H prefix <new prefix>`. The bot immediately disables the
  old helper prefix in that server and shows the new commands in its guides.
- The helper prefix and OwO prefix are independent. Changing one never changes
  the other.
- `H boss skip` now adds one random usable custom emoji from the current server
  when available. The selected emoji remains stable when the sticky is reposted.
- Startup emoji synchronization now knows about 118 assets: six existing UI
  emojis, 55 standard animals, 29 weapons, and 28 passives.
- The animal set includes Common, Uncommon, Rare, Epic, Mythical, Gem,
  Legendary, Fabled, Bot, Hidden, and Distorted ranks. Patreon, Custom Patreon,
  and monthly Special/event animals are intentionally excluded.

## Setup and compatibility

- No Guild Members or Presence intent is required.
- Message Content remains required for the bot's existing message-driven core.
- No new runtime database is introduced. The helper-prefix setting is stored in
  the existing `team_templates.db`, which remains part of the production backup.
- Existing servers continue to use `h` automatically until a manager changes it.
- Existing application emoji names are reused. The bot creates only missing
  configured names and does not delete unrelated application emojis.

## Suggested smoke tests

1. Run `H help` and confirm the guide opens.
2. Run `/helper-prefix prefix:?`, then test `?help`, `?setup`, `?T`, `?WD`,
   `? boss cd`, and `?BT <member>`.
3. Restore the prefix with `/helper-prefix prefix:h` if desired.
4. During an active boss, run `H boss skip` and confirm `# SKIP` includes one
   usable custom server emoji when the server has one.
5. Check startup logs for the application emoji registry totals and confirm
   failed uploads are reported without stopping the bot.
