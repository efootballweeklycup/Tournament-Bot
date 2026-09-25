# V43 — World Cup Knockout Advancement Fix

## Fixed: Round of 16 no longer advances to Quarterfinals

### Root cause
The World Cup Group D emergency-format override was checked inside `_advance_if_ready()`.
In v42, once the emergency-format record existed, the function returned immediately whenever Round of 16 fixtures already existed:

- R16 matches could be completed normally.
- `_stage_complete(tournament_id, "round_of_16")` was never reached.
- The generic knockout advancement loop therefore never created the Quarterfinals.

### Fix
The emergency-format logic now only controls **creation of the first knockout stage**.
After the R16 fixtures exist, `_advance_if_ready()` falls through to the normal knockout progression logic.

Therefore:

- Group D emergency format → R16 generation still works.
- Completed R16 → automatically creates Quarterfinals.
- Completed Quarterfinals → automatically creates Semifinals.
- Completed Semifinals → automatically creates Final.
- Completed Final → crowns the champion normally.

No changes were made to the existing emergency qualification rules, wildcard ranking, live Ballon d'Or/Golden Boot logic, or ranking name-resolution logic.
