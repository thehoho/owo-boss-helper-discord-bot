# v0.11.5-beta - Smart Presence Pings and Daily Boss Reports

This beta makes boss alerts more considerate, separates persistent fighter notifications from temporary stickies, and adds daily outcome reporting.

## Changed

- New-boss announcements use an embed.
- The configured decision roles are expanded into direct member mentions.
- Only members whose current Discord status is Online or Idle are mentioned.
- Offline and Do Not Disturb members are not mentioned.
- Active-helper lists are split into messages containing at most nine direct mentions.
- HIT/SKIP stickies now contain only `# HIT` or `# SKIP`.
- Fighter-role alerts are sent as separate persistent messages and are not deleted or reposted with the sticky.
- A fighter role is still pinged only once per active boss.

## Daily reports

- `/boss-report-channel <channel>` enables a daily guild-boss report.
- `/boss-report-channel` with no channel disables reports while preserving the current counters.
- Reports are sent at the existing Pacific-midnight reset.
- Confirmed defeated bosses count as HIT.
- Confirmed escaped bosses count as SKIP.
- Counts are persisted in `boss_cooldown_config.json`.
- Up to seven failed daily reports are persisted for retry after a temporary Discord or channel failure.

## Required Discord settings

Enable both privileged gateway intents for the bot in the Discord Developer Portal:

- Server Members Intent
- Presence Intent

The bot also needs View Channel, Send Messages, Embed Links, and Read Message History in its boss channels. For fighter-role notifications, make the role mentionable or grant the bot **Mention @everyone, @here, and All Roles** in the boss-alert channel.
