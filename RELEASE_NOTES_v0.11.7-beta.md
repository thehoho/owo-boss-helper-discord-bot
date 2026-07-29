# v0.11.7-beta - Mobile-Friendly Dex Prompts

This small beta update removes Discord copy-button workarounds that were less useful on mobile and makes the guided Neon dex command visible and self-explanatory.

## HWD preview

- **Start dexing session** is now the only button on the HWD queue preview.
- Removed **Copy first command**.
- Removed the unnecessary preview **Close** button.
- The preview tells members that each command will appear directly in the channel.

## Active dex steps

- Removed **Copy command** because Discord buttons cannot place text directly on a user's clipboard.
- Each step now shows the weapon owner, runner when applicable, session progress, a small “copy this command and send it in this channel” note, and the current `ww` or `wuse` command.
- **Skip weapon** and **Stop** remain available.
- The existing OwO/Neon confirmation and automatic-advance behavior is unchanged.

## HW guide

- Step 4 now stays in the numbered sequence.
- It explicitly tells members to click the right arrow through every Neon weapon page.
- The reminder that the helper can scan only pages Neon actually displays appears on its own line.

## Discord intents

This release continues to request Message Content only. It does not request Guild Members or Presence.
