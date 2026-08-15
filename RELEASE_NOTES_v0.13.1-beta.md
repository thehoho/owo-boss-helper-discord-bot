# OwO Boss Helper v0.13.1-beta

Active `HWD` prompts now show the exact `ww` or `wuse` command as a large Discord heading while preserving inline-code copying on desktop and mobile.

This patch makes trusted team guides easier for existing Neon users to author
and lets communities keep the persistent boss-ticket board inside a Discord
thread instead of creating another channel.

## Team guide improvements

- Public help, browse, and search surfaces use the simpler **Team Guides** name; authoring remains restricted to owner-approved trusted experts.

- Summaries, optional full-guide text, and composition-slot notes accept Discord
  Markdown plus optional Neon-style emoji variables.
- Familiar names work directly without a category prefix. The original `{w...}` weapons,
  `{fp...}` passives, `{a...}` animals, `{s...}` stats, and `{r...}` ranks remain compatible.
- Catalog and familiar compact community aliases work inside variables,
  including `{sword}`, `{pdagger}`, `{vampstaff}`, `{arcane}`,
  `{lifesteal}`, `{mana_mtap}`, `{snail_passive}`, `{fish}`, `{gfish}`,
  `{beeday}`, `{wp_stat}`, and `{legendary}`.
- Bare normal-animal names win the two natural collisions: `{wolf}` and `{snail}` are pets; use `{lwolf}` and `{snail_passive}` for the passives.
- Existing Discord Markdown, Unicode emojis, custom emoji markup, structured
  composition fields, and already-published guides remain compatible.
- The private editor includes an **Emoji variables** reference. Preview resolves
  recognized names through the bot's application emojis and lists unknown
  variables without deleting their text.
- Experts can add an optional 4,000-character full guide for detailed matchup
  notes, alternatives, and weapon-quality guidance.
- Published cards show **Full guide** only when detailed text exists. Clicking it
  opens a private response and automatically paginates the rendered text inside
  Discord's embed limits.

## Ticket-board threads

- `/boss-ticket-channel` accepts a text channel, announcement channel, public
  thread, private thread, or announcement thread that the bot can access.
- Thread destinations require **View Channel**, **Send Messages in Threads**,
  **Embed Links**, and **Read Message History**.
- Locked threads are rejected with a clear setup message.
- Accessible archived threads are reopened automatically before the board is
  refreshed.
- H help and H setup now state that the persistent ticket board can use a text channel or thread; H about shows the v0.13.1-beta version and thread-board capability.
- Replacement safety is unchanged: the new board is sent successfully before
  the previous bot-authored board message is deleted.

## Storage and compatibility

- `team_guides.db` receives one additive `full_guide` column with an empty
  default. Existing guide rows and runtime databases are preserved.
- Ticket destinations continue to use the existing configured destination ID;
  a thread ID is stored in the same field as a text-channel ID.
- No new privileged Gateway intent is required.
- Thread posting uses normal Discord channel permissions and does not require
  Guild Members or Presence access.

## Suggested smoke tests

1. Open `/team-guide-create` as a trusted expert and confirm **Emoji variables**
   shows weapon, passive, animal, stat, and rank examples.
2. Put `{pdagger}`, `{lifesteal}`, `{fish}`, and `{wp_stat}` in a summary, slot note,
   or full guide; Preview should show application emojis.
3. Add an intentionally unknown variable and confirm Preview warns about it
   while preserving the original text.
4. Publish a guide with detailed text, open **Full guide**, and confirm the
   private page navigation works when the rendered text spans multiple pages.
5. Reopen an existing v0.13.0 guide and confirm its summary and composition load
   with an empty optional full-guide field.
6. Run `/boss-ticket-channel` and select an active thread. Confirm a fresh board
   appears there and later ticket updates replace it in the same thread.
7. Temporarily archive an unlocked configured thread and trigger a board refresh;
   confirm the bot reopens it. Confirm a locked thread produces a clear error.
