# v0.11.2-beta — Server Prefix Hotfix + Boss Decision Sticky

This is a feedback-driven beta update after v0.11.1.

## Fixed

- The server OwO prefix now applies beyond team restores.
- Boss inventory reader now accepts the configured server prefix:
  - default: `w boss i`
  - custom prefix example: `o boss i`
  - full form still works: `owo boss i`
- Boss-ticket tracking now accepts the configured server prefix:
  - default: `w boss t`
  - custom prefix example: `o boss t`
  - full form still works: `owo boss t`
- New boss instructions now show the server's configured OwO prefix instead of always showing `w`.

## Added

- Boss hit/skip decision sticky for the configured boss alert channel.
- `H boss hit`, `Hboss hit`, and `H set hit` mark the active boss as **HIT**.
- `H boss skip`, `H skip boss`, and `H set skip` mark the active boss as **SKIP**.
- Sticky hit/skip buttons are included on the boss decision message.
- `/boss-decision-role` lets server managers choose a role that can mark bosses as hit/skip.
- Members with Manage Server can always mark hit/skip.

## Notes

- If no decision role is configured, only members with Manage Server can use the hit/skip controls.
- The sticky decision message is created in the same configured boss alert channel and is removed when the boss ends.
