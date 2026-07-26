# OwO Boss Helper v0.11.1 Beta

This feedback update improves real-world server setup compatibility and smooths large Neon dex cleanup sessions.

## Per-server OwO prefix for team restores

Servers can now configure the OwO prefix used by generated team commands. The default is still `w`, but servers using `o`, `g`, or another short prefix can set it once.

```text
HT prefix
HT prefix o
H team prefix g
```

After setting `HT prefix o`, saved-team restore flows use commands like `otm a ...`, `otm d ...`, and `ow <weapon_id> <animal>`. This applies to Quick replace, Exact reset, All commands, guided prompts, and the final team check.

Only members with **Manage Server** can change the prefix. Anyone can run `HT prefix` to view the current server setting.

## Combined Neon dex weapon filters

`HWD` can now combine multiple weapon types in one queue.

```text
HWD dagger shield 100
HWD sword shield crane all
HWD @member dagger shield mtap 250
```

Weapon type filters are OR-based. Passive filters remain grouped together. For example, `HWD dagger shield mtap` returns queued daggers or shields that include Mana Tap.

## Daily owner report fix

The daily owner DM report loop now starts when the bot loads. A new owner-only `/bot-daily-report-test` command sends the report immediately so the schedule can be verified without waiting for reset time.

## Server insight cleanup

The inviter/audit-log trail was removed from user-facing server insight views. Discord does not reliably expose who invited the bot, and the bot does not request audit-log permissions for normal use. Server insight views now focus on current owner, usage, activity, permissions, and safe vanity visibility.
