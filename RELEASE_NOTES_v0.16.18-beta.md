# v0.16.18-beta — Saved Knowledge Plans and Category-First Guides

## XP model and forecast

Each saved forecast model now includes a Knowledge switching plan: always equipped or every 10, 50, 100, 500, or 1,000 battles. The setting is global per member, persists across restarts, and automatically powers both the compact Team XP **Your model** result and the full level forecaster.

Combined Knowledge quality now accepts values from 0% to 30%, covering a weapon with two fabled 15% Knowledge passives. Existing models migrate safely with their previous behavior of always-equipped Knowledge until the member chooses an interval.

The full forecast now:

- displays up to 20 progression rows per page by default;
- names all three pets in the table header;
- opens on the member's saved Knowledge plan;
- removes the separate pet-statistics control; and
- keeps Settings, optional streak display, scenario preview, and pagination.

The Team XP card keeps the normal comparison and one **Your model** result. The removed **Model + Max Knowledge** line is no longer needed because the saved model now includes both Knowledge quality and switching frequency.

Forecast refreshes also avoid redundant simulations that were previously needed only for the removed statistics and max-Knowledge comparisons.

## Team-guide organization

Running plain `H guide` or `/team-guide` now presents categories without listing teams first. After a member selects a category, the second selector displays teams in it. A team may appear in several categories.

The guide editor recommends the broad **Boss**, **Streak**, and **Breaker** categories, and the browser places those categories first when present. Existing custom categories remain compatible. Direct `H guide <query>` and slash-command searches still match guide names, aliases—including weapon aliases—categories, and displayed authors.

## Deployment

- Public version: `0.16.18-beta`
- Database migration: adds `knowledge_interval` to existing saved XP forecast settings with a backward-compatible always-equipped default
- New dependencies: none
- New Discord permissions: none
