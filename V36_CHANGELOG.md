# V36 — World Cup Result Role-Mention Delivery Fix

Fixes the remaining V35 issue where the code generated World Cup country-role
mentions but the interaction response did not forward Discord's
`AllowedMentions`, so Discord displayed plain country names.

Changes:
- `/report` now forwards role-only AllowedMentions for World Cup result posts.
- The submitted result displays in fixture order: `Austria 2 - 1 Portugal`.
- The two country labels and the confirmation target use the actual Discord
  country-role mentions (`@Austria`, `@Portugal`).
- Result confirmation responses also preserve World Cup role mentions.
- `/post_pending_confirmations` continues to use role mentions.
- `/world_cup_sync_country_roles` is preserved.
- No database schema, fixtures, scores, schedules, or tournament progression
  logic was changed.
- Non-World-Cup tournaments keep their existing mention behavior.
