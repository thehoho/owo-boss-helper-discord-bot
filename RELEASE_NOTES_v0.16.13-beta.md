# v0.16.13-beta — Knowledge Reminder State Consistency

Knowledge-switch reminder status now follows the member's explicit **Enable** or **Disable** choice everywhere.

Saving normal weapons, entering Knowledge weapon IDs, or saving a freshly equipped Knowledge team no longer turns an enabled reminder off. A preset change still cancels an unfinished switch-back step so an old weapon packet cannot be sent after the presets change.

Every preset confirmation now says whether tracking remains enabled or disabled. The same saved profile powers `H reminders`, `/xp-reminders`, and the bell under Team XP tools.

Successful Knowledge milestone prompts and normal-weapon restore prompts are now written to the operational log for easier diagnosis.

No database migration, slash-command schema, dependency, or Discord permission change is required. A member whose reminder was already disabled by an older preset save needs to press **Enable** once; future preset changes will preserve it.
