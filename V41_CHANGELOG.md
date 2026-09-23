# V41 — Immediate World Cup Emergency Command Registration

## Fix
V40 added `/world_cup_emergency_format`, but it was registered globally.
New Discord global slash commands can take time to propagate, so the command
could be absent from the mobile slash-command picker immediately after a
deployment.

V41 fixes this by:
- keeping the normal global command sync;
- copying/syncing the command set into each connected guild at startup;
- making the emergency command visible in the command picker regardless of
  the Discord "Manage Server" default-permission filter;
- still enforcing `Manage Server` when the command is executed.

## Command
`/world_cup_emergency_format`

Required:
- `tournament_id`
- optional `cancelled_group` (defaults to `D`)

No tournament data is changed merely by deploying V41.
