# V39 — Live Ranking Open-Tournament Fix

## Fix
V38 still excluded tournaments whose database status was `open`.

A tournament can remain `open` while it is already running and may already
contain confirmed/completed match results. V39 includes all three valid live
states:

- `open`
- `closed`
- `completed`

Only matches with `status = 'completed'` are counted, so unplayed fixtures
still contribute nothing.

## Monthly rule
The tournament's start month remains locked for its entire life:

- Starts August → all confirmed results remain in August ranking.
- Starts September → all confirmed results remain in September ranking.
- Finishing in a later month does not move the tournament to that later month.

## Applies to
- `/golden_boot_standings`
- `/ballon_dor_ranking`

KO Match remains excluded.
