# v0.16.19-beta — Optional Top.gg Voting and Reminders

## Voting stays optional

Members can open the OwO Boss Helper vote page with:

- `H vote`
- `HV`
- `/vote`

Servers with a custom helper prefix automatically receive matching forms. Voting does not unlock, improve, or restrict any bot feature. The panel shows the member's recorded vote count and weighted vote points only as a record of support.

## One reminder, with a clear opt-out

The vote panel includes an optional **Remind me in 12 hours** control. When enabled, the helper sends one DM after the member's latest vote becomes available again.

- Ignoring the DM does not create repeated reminders.
- Every reminder includes **Turn off vote reminders**.
- `H vote off` and the vote panel can also disable reminders.
- The preference and vote history are global across mutual servers.

## Reliable Top.gg synchronization

The helper reads Top.gg's authenticated, timestamped vote history every five minutes and deduplicates overlapping results in `topgg_votes.db`. This preserves cumulative per-member counts across restarts without exposing a new webhook port. It also publishes the current Discord server count every 30 minutes.

The public vote link works before an API token is available. Vote-history synchronization and metrics begin automatically after `TOPGG_TOKEN` is added to the production environment.

## Deployment

- Public version: `0.16.19-beta`
- Database migration: creates `topgg_votes.db` automatically
- Slash commands: adds `/vote`
- New dependencies: none
- New Discord permissions: none
- Network: outbound HTTPS to Top.gg only; no inbound port required
