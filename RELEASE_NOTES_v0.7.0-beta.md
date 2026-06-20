# OwO Boss Helper v0.7.0 Beta

This update adds a complete guild-boss ticket board and strengthens the team-template system so incomplete teams can no longer be saved silently.

## Guild boss ticket board

Server managers can now select a dedicated ticket-board channel using `/boss-ticket-channel`.

When a user runs `owo boss t`, `owo boss ticket`, `w boss t`, or `w boss ticket` anywhere the helper can read messages, the bot records the exact ticket count reported by OwO and updates that server's board.

Each entry includes:

- Discord username
- Discord user ID
- Reported boss tickets from 0/3 to 3/3
- Relative last-update time

The board is edited in place rather than posting a new message for every check. Large lists are divided into multiple pages automatically.

The tracker does not guess ticket usage. If somebody uses tickets in another server, their saved count remains unchanged until they check again in the tracked server.

## Daily replenishment

Tracked users are restored to 3/3 at midnight in `America/Los_Angeles`.

Using the IANA timezone rather than a fixed UTC offset means the reset follows both PST and PDT automatically. The ticket board uses Discord timestamps so every user sees the correct local replenishment time.

## Persistent ticket storage

Ticket data and board configuration are stored in `boss_tickets.db`.

The database is ignored by Git and should be copied together with `team_templates.db` when moving the bot to another computer or host.

## Complete team validation

The team-template parser now distinguishes between:

- A complete animal with an equipped weapon
- A complete animal without a weapon
- A genuinely empty team position

Templates with an empty position are rejected until all three animals are present.

Animals without weapons are no longer silently discarded. Instead, the user receives a warning and must explicitly choose either **Save without weapons** or **Cancel**.

When saved without a weapon, restoration still adds that animal but skips the unavailable weapon-equip command.

## Update existing templates

Users can update a saved stable slot by replying to a fresh OwO team page with:

- `HT U 3`
- `HTU 3`
- `HT U boss team`
- `H team update boss team`

Updating preserves the existing team number and name. Afterward, the helper displays the previous and new animal/weapon configuration.

## Existing features retained

- Correct boss-page ordering
- Individual-image HP recognition
- Mobile-friendly Neon commands
- Rate-limit-friendly active-boss tracking
- Defeat cooldown and escape handling
- New-boss and ready alerts
- Up to 25 stable personal team templates
- Emoji-based animal identity for renamed pets
- Exact weapon-ID preservation
- Quick replace and Exact reset
- Guided setup, skip controls, and concurrent user sessions
- Rotating logs

## Setup changes

Run:

`py -m pip install --upgrade -r requirements.txt`

The new `tzdata` dependency ensures `America/Los_Angeles` works reliably on Windows and other systems without a bundled IANA timezone database.

Configure the board once per server with `/boss-ticket-channel`.

## Credits

Special thanks to **Pencilvester** for the original exact-command parsing foundation, weapon/passive ranges, and the saved team-template idea that inspired the team-management system.

## Beta notice

Please report incorrect ticket-user matching, stale ticket boards, unexpected reset behavior, incomplete team parsing, or unusual OwO message formats.
