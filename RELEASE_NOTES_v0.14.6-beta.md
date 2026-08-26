# OwO Boss Helper v0.14.6-beta

This emergency hotfix prevents old or unrelated OwO boss-result copies from ending a different boss that is still active.

## Authoritative outcome tracking

- A defeat or escape is accepted only from an official OwO message that the helper previously observed as an active card for the current boss.
- Multiple active copies of the same boss can be remembered across server channels, while the newest copy remains the one polled for updates.
- A newer completed copy that was never active for the current boss is ignored, even if it contains otherwise valid defeated wording.
- When Discord sends an edit for a known active card, the helper fetches the full final message before interpreting it. Partial Components V2 edits cannot end a boss.
- Genuine edits of a tracked active card into **Guild Boss Defeated!** or an escaped result continue to work normally.

## Compatibility

- No database migration or new runtime file is required.
- No additional Discord permission or privileged intent is required.
- Existing channel, role, report, ticket, team, guide, prefix, animal, and weapon settings remain compatible.

## Live verification checklist

1. Display an active boss in more than one server channel.
2. Confirm old completed boss cards do not start a cooldown while the active card still has a future `runs away` timer.
3. Confirm the real tracked card ending still records exactly one defeat or escape.
4. Run `H boss report` and confirm one boss is counted only once.
5. Run `H about` and confirm version `0.14.6-beta`.
