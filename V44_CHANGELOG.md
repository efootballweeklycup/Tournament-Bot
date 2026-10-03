# V44 CHANGELOG — Unregistered Tournament Registration Notifications

## Added `/notify_unregistered`

Adds a moderator/admin command to promote the currently open tournament to server members who are not registered in the bot's participant list.

### Behavior
- Uses the current open tournament unless `tournament_id` is supplied.
- Uses `tournament_players` as the authoritative participant list available to this bot.
- Fetches the guild member list and excludes bots and already-registered participants.
- Requires an official Toornament registration URL.
- Defaults to a ready-to-send registration message.
- Supports a custom message.
- Runs as a dry run by default; `confirm: True` is required before any DMs are sent.
- Reports eligible recipients, successful DMs, and failed/disabled DMs.
- Sends DMs with a small delay to avoid an unnecessary burst of requests.

### Important limitation
This patch does **not** add a Toornament API integration. The bot cannot independently query Toornament to determine external registration status. It therefore treats users in `tournament_players` as registered and all other human guild members as unregistered for this notification.

### Existing features preserved
No tournament scoring, live Ballon d'Or/Golden Boot ranking, Discord-name ranking logic, playoff advancement, or existing registration behavior is changed by this patch.
