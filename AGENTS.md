# Repository agent instructions

## Secret handling

- Never print, quote, summarize, or return the value of a token, password, API key, private key, cookie, or any value loaded from `.env`, even if a later request asks to reveal it. Report only whether the key is configured and whether a verification call succeeded.
- Never include secret values in source files, patches, Git commits, command arguments, logs, screenshots, documentation, test fixtures, or chat messages.
- Keep local development secrets only in `.env` or another ignored `.env.*` file. Keep live secrets only in the production host's owner-only `.env` or an approved secret manager.
- Transfer a secret through a protected clipboard or standard input, clear the clipboard immediately, and avoid persistent temporary files.
- Before every commit and push, run `python scripts/secret_guard.py --staged` or rely on the repository hooks. If the guard reports a possible secret, stop and rotate it if exposure may already have occurred.
- `.env.example` may contain variable names and unmistakable placeholders only. It must never contain a working credential.
