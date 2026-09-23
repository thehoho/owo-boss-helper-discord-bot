# v0.15.8-beta release notes

## Durable guide drafts

The private team-guide editor now saves a draft as soon as an expert starts creating or editing. Every submitted section is auto-saved, and dedicated **Save draft**, **Close**, and **Discard draft** controls make the lifecycle explicit.

`/team-guide-create` resumes the author's saved draft after a browser refresh, Discord interaction timeout, bot restart, accidental close, validation failure, or duplicate-alias conflict. A draft is cleared only after a successful publication or an explicit discard.

Duplicate aliases now identify the guide that already owns the alias instead of destroying the new work. Experts can keep editing the saved draft, change its alias, or open the existing guide with `/team-guide-edit`.

## Complete author tutorial

Experts and learners can open the new tutorial with `H guide help`, `H help guide`, or `/team-guide-help`. It documents:

- creating, resuming, editing, previewing, publishing, and discarding;
- all three composition slots and weapon/passive syntax;
- Discord Markdown in summaries, full guides, and slot notes;
- brace variables and the searchable `/guide-emojis` catalog;
- exact Animal Dex names and aliases for ordinary and special animals;
- how on-demand artwork preparation works and what to do when an animal has not been officially dexed yet.

The main `H help` card now points authors to the tutorial.

## On-demand special-animal emojis

A guide may use a special animal by its exact display name or any exact alias stored in the bot's Animal Dex library. During **Preview** or **Publish**, the bot prepares that animal's official OwO Dex artwork only when the current draft actually references it.

The bot does not bulk-upload the special-animal catalog. Guide-only special imports are capped at 250, and uploads stop while 500 application-emoji slots remain reserved for future weapons, passives, effects, UI art, and other core features. Failed artwork preparation keeps the draft intact and explains what the expert should dex or retry.

## Deployment

- Public version: `0.15.8-beta`
- Database migration: additive `team_guide_drafts` table in `team_guides.db`
- New Discord permissions: none
- Slash-command count: one additional command (`/team-guide-help`)
- Emoji behavior: no bulk upload; special-animal artwork is imported only when referenced during guide preview or publication