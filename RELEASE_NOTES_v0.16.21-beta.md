# v0.16.21-beta — Top.gg Pagination and Secret Safeguards

## Reliable Top.gg v1 pagination

Top.gg's v1 vote-history API returns an opaque cursor on every page and documents an empty `data` page as the end of pagination. The helper now stops on that empty page, rather than following its cursor again.

Two additional safety controls prevent request storms:

- a repeated non-empty cursor stops the synchronization immediately; and
- an HTTP 429 pauses both vote imports and server-count metrics for Top.gg's requested retry period, or one hour when no period is supplied.

Already imported active votes remain eligible for their one-time thank-you DM during an API pause.

## Local and production secret boundaries

- `.env`, every `.env.*` variant except `.env.example`, private-key containers, credential JSON files, and secret directories are ignored by Git.
- `scripts/secret_guard.py` checks both filenames and content without printing suspected values.
- Repository pre-commit and pre-push hooks automatically run the guard.
- `AGENTS.md` permanently instructs coding agents never to reveal `.env` or token values, even when asked later.
- The live Top.gg token exists only in the production host's owner-only `.env`; it is not stored in this repository.

## Deployment

- Public version: `0.16.21-beta`
- Database migration: none beyond the existing v0.16.20 acknowledgement column
- New dependencies: none
- New slash commands: none
- New Discord permissions: none
- Network: outbound HTTPS to Top.gg only; no inbound port required
