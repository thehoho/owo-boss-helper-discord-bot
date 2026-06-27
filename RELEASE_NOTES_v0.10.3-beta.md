# OwO Boss Helper v0.10.3 Beta

This release is a stability and performance refinement for large ticket boards and
optional ticket nickname markers. It preserves the existing sticky-board behavior while
moving slow or repetitive work away from Discord interaction response paths.

## Faster sticky ticket board

The configured ticket board continues to work as a sticky message: after ticket data
changes, the bot sends the fresh board first and removes the previous board only after
the replacement is visible.

The update improves that flow by:

- Coalescing several ticket updates received close together into one board replacement.
- Caching the complete sorted ticket snapshot used by all board pages.
- Making Previous, Next, Text view, and Ping view switch pages without fetching Discord
  members or rebuilding identities through the Discord API.
- Updating stored names naturally when members run their own ticket command instead of
  scanning the full guild member list.
- Recording timing information for page rendering and sticky-board replacement.

Pages remain limited to 15 entries so the three application-owned ticket emojis and
identity details stay safely within Discord embed limits.

## Explicit nickname opt-in

Nickname markers now require consent at both levels:

1. A server manager makes the feature available through `HBS`, `H boss settings`, or
   `/boss-ticket-manage`.
2. Each member explicitly enables their own marker.

Enabling the server feature no longer renames all tracked members. Members who take no
action remain unchanged. Existing suffixes inherited from the older implicit-opt-in
behavior are cleaned up in a throttled background job unless that member has an explicit
enabled preference.

The personal controls now use the clearer labels:

- **Enable my marker**
- **Disable my marker**

The existing board-entry and tracking controls remain unchanged.

## Functional reaction shortcut

When the server nickname feature is available, a successful `w boss t` or `owo boss t`
check receives one action reaction under the member's own command:

- `🏷️` — enable the member's nickname marker.
- `🔕` — disable the member's nickname marker.

Only the author of that ticket command can activate its shortcut. The bot updates the
preference and nickname immediately, then changes its action icon to represent the next
available action. The bot also attempts to remove the member's click reaction; Discord
requires **Manage Messages** for removing another member's reaction, but the preference
action still completes when that optional permission is unavailable.

## Responsive HBS controls

Server-wide nickname work no longer blocks the management interaction:

- Enabling markers returns immediately, queues legacy-marker cleanup, and reapplies only previously saved explicit opt-ins.
- Disabling markers returns immediately and queues managed-name restoration.
- Manual Sync returns immediately and queues a sync for explicitly opted-in members.
- Background nickname edits are serialized per server and paced more conservatively to
  reduce Discord rate-limit pressure.

Individual ticket updates and personal marker toggles reuse the member object already
received from Discord whenever possible, avoiding an unnecessary member fetch.

## Reliability changes

- Added a SQLite busy timeout and `synchronous=NORMAL` while keeping WAL mode.
- Moved startup ticket-board restoration into background queues.
- Moved Pacific-midnight board and nickname refreshes into the same queues.
- Added race-safe runtime cleanup and successor scheduling for board-refresh and nickname-job tasks.
- Scoped reaction handling to shortcuts the bot actually created, avoiding message fetches for unrelated uses of the same emoji.
- Reduced routine logging for members who correctly remain opted out by default.
- Kept all existing database tables, ticket records, tracking preferences, board
  configuration, and nickname restoration state. No manual database migration is
  required.

## Updated files

- `cogs/ticket_tracker.py`
- `cogs/bot_info.py`
- `README.md`
- `CHANGELOG.md`
- `RELEASE_NOTES_v0.10.3-beta.md`

The public bot version is now `0.10.3-beta`. The expected slash-command count remains
10.
