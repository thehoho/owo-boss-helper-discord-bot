# v0.16.15-beta — Global Knowledge Reminder Profiles

Knowledge-switch reminders now follow each member across every server shared with the helper.

Enabling or disabling reminders, choosing an interval, changing mention behavior, and saving normal or Knowledge weapons now update one global member profile. A preset saved in one server is therefore available when that member grinds in another server. An unfinished Knowledge switch also follows the member, allowing the switch-back reminder to appear with their next matching battle result even after changing servers.

The existing server-scoped database is migrated automatically during startup. When more than one old profile exists for a member, the migration keeps the newest reminder settings and carries forward the newest available normal and Knowledge presets.

Regression coverage includes cross-server reads and updates, cross-server milestone delivery, restart persistence, and migration of legacy server profiles into one member profile. No slash-command schema, dependency, or Discord permission change is required.
