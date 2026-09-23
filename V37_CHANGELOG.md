# V37 — Ranking Commands Reliability Fix

Fixes the monthly ranking commands that could fail while `/matches` continued to work.

Changes:
- Hardened `/ballon_dor_ranking` so one malformed/legacy completed tournament cannot abort the entire ranking.
- Hardened Ballon d'Or placement calculation with per-tournament error isolation and logging.
- Hardened `/golden_boot_standings` and `/ballon_dor_ranking` interaction acknowledgement/error handling.
- Added a legacy database migration for `goal_records.nation_name`.
- Added indexes supporting tournament/template and monthly ranking lookups.
- Existing result reporting, country-role mentions, fixtures, scores, and tournament progression are unchanged.
