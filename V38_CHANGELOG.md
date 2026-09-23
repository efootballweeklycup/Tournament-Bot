# V38 — Live Monthly Awards + Tournament Start-Month Lock

- Golden Boot now includes ongoing (closed) competitive tournaments.
- Ballon d'Or now includes ongoing (closed) competitive tournaments.
- Only completed/confirmed match results contribute goals and wins.
- Tournament placement bonuses are awarded only after a tournament is completed.
- Every tournament is permanently assigned to the month in which it started.
- A tournament that starts in August keeps all later results in August's
  Golden Boot/Ballon d'Or ranking even if it finishes in September or October.
- A tournament that starts in September keeps all later results in September's
  ranking even if it finishes in October or later.
- Added `tournament_started_at` with an automatic legacy backfill from
  `registration_closed_at`/`created_at`.
- Live Golden Boot totals are calculated directly from completed match rows,
  so newly confirmed results appear without waiting for tournament completion.
- KO Match remains excluded.
- Existing fixtures, results, country-role mentions, scheduling, and
  tournament progression are unchanged.
