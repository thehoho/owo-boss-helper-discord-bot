# OwO Boss Helper v0.10.4 Beta

This release adds safe public guild-boss hit reconciliation, faster ticket lookups, automatic stale-list cleanup, more reliable prefix responses, channel diagnostics, and animal-targeted team weapon commands.

## Automatic ticket updates from public boss logs

OwO's edited guild-boss card publicly shows each Top 10 fighter as a Discord mention and places one public battle-log link beside that member for every successful ticket used.

v0.10.4 now:

- watches that public Top 10 section through Discord gateway message-create and message-edit events;
- extracts the Discord user ID and every `owobot.com/battle-log?uuid=...` link shown for that user;
- treats each new unique UUID as one confirmed ticket use;
- supports two or three hits from the same fighter on the same boss;
- deduplicates repeated message edits and never reduces a count below `0/3`;
- updates only members who already have a current ticket entry in that server;
- ignores blocked and personally opted-out members;
- updates opted-in nickname markers and the sticky ticket board in the existing background queues.

The first snapshot observed for a boss message is stored as a baseline and does not subtract anything. This prevents a restart or an upgrade in the middle of a boss from retroactively applying every hit already visible on the card.

Manual `w boss t` and `owo boss t` responses remain authoritative. Fighters outside OwO's visible Top 10 still need to update manually.

## HBT ticket lookup

Use:

```text
HBT <name or part of name>
HBT @mention
HBT <Discord user ID>
```

A unique result shows the current ticket count, account username, numeric ID, last confirmed activity, and whether the latest value came from a manual ticket check or a public Top 10 battle log. Ambiguous partial searches return a short list instead of guessing.

## Automatic 48-hour cleanup

A ticket entry now expires after 48 hours without either:

- a successful manual ticket check; or
- a confirmed public Top 10 battle-log event.

Expiration removes the current board row and restores the helper-managed nickname, but preserves personal tracking, blocking, and nickname preferences. An eligible member can return by running `w boss t` later.

Existing rows receive a one-time rollout grace period through the second upcoming Pacific reset. They remain after the next reset and are removed at the following reset only when no new activity was recorded.

## Team restoration improvement

Guided team setup now equips weapons by the saved animal identifier:

```text
ww ALGOB8 snail
```

instead of relying on a position such as:

```text
ww ALGOB8 1
```

Existing saved templates already contain both the animal identifier and weapon ID, so no team database migration is required.

## More reliable prefix replies

Prefix command responses still reply to the member's message when Discord allows it. If a channel or thread rejects the reply reference but permits normal messages, the helper now falls back to `channel.send` and records the original error in the bot log.

Server managers can run:

```text
/channel-diagnostics
```

inside a problem channel or thread to inspect effective permissions including View Channel, Send Messages, Read Message History, Embed Links, Add Reactions, Send Messages in Threads, Manage Messages, and Manage Nicknames.

Only `H help` received relaxed prefix parsing. It is recognized at the start of the first non-empty line, and text after it is ignored. Other helper and OwO triggers keep their existing strict behavior.

## Storage and migration

`boss_tickets.db` is migrated automatically with:

- member activity and expiration timestamps;
- the latest ticket-update source;
- observed boss-message state;
- deduplicated public battle-log UUID events.

No manual SQL migration or dependency installation is required.

## Version

```text
0.10.4-beta
```
