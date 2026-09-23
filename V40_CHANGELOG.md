# V40 — World Cup 32 Group D Emergency Format

Adds a tournament-specific staff command:
`/world_cup_emergency_format tournament_id:<ID> cancelled_group:D`

Rules:
- Group D is cancelled and never eligible for qualification.
- Groups A, B, C, E, F, G, H send their top 2 directly to the R16.
- The best two 3rd-place players across those seven groups take the two wildcard slots.
- Wildcard ranking: PTS, GD, GF, W, then GA (lower), then Discord ID as a deterministic final fallback.
- The override is stored per tournament and does not change the normal World Cup template.
- R16 is generated only after all seven eligible groups are complete.
- If R16 already has completed matches, the bot refuses to rewrite history.
- Existing Group D data is preserved; it is excluded only from qualification.
- Generated R16 fixtures use the existing World Cup country-role mention system.
