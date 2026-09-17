# V35 — World Cup Result Role-Mention Fix

Fixes the result-report display paths that were still using the old:
`Result submitted by X for Y: score`

World Cup result messages now:
- show player1 -> player2 fixture order (`Austria 5 - 0 Mexico`)
- use the assigned Discord country roles (`@Austria 5 - 0 @Mexico`)
- mention the opposing country role for confirmation
- use role-only allowed mentions for World Cup result messages
- keep non-World-Cup tournaments on normal player mentions
- preserve the existing result confirmation, dispute, standings, and database logic
- preserve `/world_cup_sync_country_roles`
