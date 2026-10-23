# v0.16.17-beta — Persistent Server Access Control

The configured bot owner can now deny a Discord server access to the helper with `/bot-ban-server`. The command accepts an exact server ID and an optional private note, displays a private preview, and requires confirmation before saving the restriction.

If the bot is currently connected, it sends a best-effort notice explaining that access has been denied and then leaves. If anyone invites it again later, the persistent denylist is checked immediately, the same notice is attempted, and the bot leaves automatically. The restriction also survives bot restarts.

Only `BOT_OWNER_ID` can manage this system. `/bot-banned-servers` displays the denylist, and `/bot-unban-server` restores eligibility for future invitations without automatically re-adding the bot. Operational logs and owner DMs record blocked join attempts; private notes are never posted inside the denied server.
