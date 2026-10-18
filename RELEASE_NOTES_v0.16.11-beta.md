# v0.16.11-beta — Optional Knowledge Presets

Knowledge-switch reminders no longer require saved weapon IDs. A member can choose an interval and enable reminders immediately. At the selected milestone, the helper says it is time to switch to Knowledge; after the battle ends in a win or loss, it says to switch back to the normal weapons. A tie keeps the Knowledge step open.

Saved weapon presets remain useful but optional. When compatible normal and Knowledge presets exist, the same reminders automatically include the ready-to-copy weapon commands. Without presets, they stay as clear text prompts for members who keep their commands in TapDeck or somewhere else.

The Knowledge button now opens a private setup guide instead of immediately showing an unexplained form. It offers two methods:

- **Enter IDs:** type the six-character Knowledge weapon IDs for only the positions that change.
- **Save equipped:** equip the Knowledge weapons, open a fresh OwO team page, reopen Team XP tools, and save the weapons read from that page.

The reminder panel also shows whether the current setup uses simple prompts or copyable weapon commands.

No database migration, slash-command schema change, dependency update, or new Discord permission is required.
