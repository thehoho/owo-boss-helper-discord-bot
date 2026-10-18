# v0.16.14-beta — OwO Fight Reminder Compatibility

Knowledge-switch reminders now recognize OwO's `fight` battle alias.

Members using `owo fight` or `owofight` are now tracked exactly like members using `owo b` or `owo battle`. The same applies to a server's configured OwO prefix, including forms such as `w fight` and `wfight`.

Previously, OwO accepted the fight and displayed the result, but the helper did not arm member-specific tracking for that command. The result therefore could not update the member's stored streak or produce the Knowledge prompt before a milestone.

Regression coverage now verifies all supported battle aliases in spaced and compact forms. No database migration, slash-command schema, dependency, or Discord permission change is required.
