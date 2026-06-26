# OwO Boss Helper v0.10.1 Beta

This refinement release fixes HP-number edge cases and makes large ticket boards easier to use when members change their Discord names.

## HP extraction fix

OwO's pixel font sometimes joins several adjacent digits without a blank column. Earlier builds could split only two touching digits. A connected run such as `444` or `744` could therefore fail validation and cause that boss page to use the `80000` fallback.

The HP reader now evaluates bounded 2–6 digit segmentations and selects the strongest template match. Existing safeguards remain active:

- Average and minimum glyph-confidence thresholds
- Full `current / maximum` format validation
- Leading-zero rejection
- Current HP cannot exceed maximum HP
- No OCR dependency

The supplied `13,727 / 121,444` and `75,744 / 75,744` edge cases were used as regression examples during validation.

## Ticket-board identity improvements

Ticket boards now have:

- **Text view**
- **Ping view**
- Existing Previous, Next, and My nickname controls

Text view shows:

- Current stored display name
- Exact Discord account username
- Numeric Discord user ID
- Ticket count
- Last update time

Ping view shows a clickable Discord member mention while retaining the exact account username and numeric ID. Allowed mentions are disabled, so changing views does not notify the listed members.

When a page is opened, the bot refreshes the identities of the visible tracked users by their known IDs. It fetches individual known members only; Guild Members Intent remains unnecessary.

## Database migration

No manual migration is required. The existing `ticket_status` table automatically gains:

```text
account_username
```

Existing ticket counts, boards, nickname preferences, nickname restoration state, templates, cooldowns, and statistics remain intact.

## UI emoji preparation

The repository now reserves these source filenames under `assets/ui_emojis/`:

```text
ticket_available.png
ticket_used.png
boss_appeared.png
boss_escaped.png
boss_defeated.png
```

The files are not activated yet. They can be uploaded as Discord application emojis in the later UI-focused update.

## Version

```text
0.10.1-beta
```

Developed and maintained by **Hassaan**.
