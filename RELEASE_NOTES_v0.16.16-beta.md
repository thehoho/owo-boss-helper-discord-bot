# v0.16.16-beta — Owner-Controlled Server Removal

The configured bot owner can now use `/bot-leave-server` with a Discord server ID to disconnect the helper from a server remotely.

The command is restricted to `BOT_OWNER_ID`. It resolves the live server and shows its name, ID, approximate member count, and owner in a private preview. Nothing happens until the bot owner presses the red **Leave this server** confirmation button. A cancel button safely closes the prompt.

Successful removals are written to the operational log and the persistent guild registry. The existing owner DM also reports the server departure. Invalid IDs, servers the bot has already left, and servers the bot cannot currently resolve fail safely without taking action.
