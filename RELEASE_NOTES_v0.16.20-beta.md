# v0.16.20-beta — Complete Top.gg Vote Acknowledgements

## Every new vote is recorded and acknowledged

OwO Boss Helper now checks its approved Top.gg project every minute. When a new active vote is imported, the voter receives one thank-you DM showing:

- the points awarded by that vote;
- their total recorded votes and weighted vote points;
- the relative and exact time when they can vote again; and
- a direct link to the bot's Top.gg voting page.

Voting remains completely optional and does not lock or unlock any bot feature.

## Persistent history without duplicate messages

Every Top.gg vote is stored against the voter's Discord user ID with its event time, expiry time, and weight. A persisted acknowledgement timestamp ensures that restarts and overlapping API pages cannot send the same thank-you twice.

The first production synchronization can safely import older history: expired votes count toward totals but do not generate delayed DMs. If a member has DMs closed, the helper records one failed acknowledgement attempt instead of repeatedly messaging them.

## Existing reminder controls still apply

The optional 12-hour next-vote reminder remains separate from the immediate thank-you. Members can manage it through `/vote`, `H vote`, `HV`, or the button in a reminder DM. Ignoring a reminder never causes repeated messages for that vote.

## Deployment

- Public version: `0.16.20-beta`
- Database migration: adds `thanked_at` to `topgg_vote_events` automatically
- Vote import interval: one minute
- New dependencies: none
- New slash commands: none
- New Discord permissions: none
- Network: outbound HTTPS to Top.gg only; no inbound port required
