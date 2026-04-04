# Debug Missing Model

Goal:
- Determine why an expected exported model is absent or empty.

Procedure:
1. Classify the archive:
   - standard IMG
   - streamed LVZ+IMG
2. For standard IMG:
   - confirm the entry exists
   - confirm the queued job is created
   - inspect conversion failure or skipped suffix handling
3. For streamed archives:
   - confirm the sector/area traversal sees the placement
   - confirm `world_id -> linked entity id -> model name` resolution
   - inspect cluster grouping
   - inspect resource candidates for each `res_id`
   - inspect decode success per candidate
   - inspect fragment validation after localization
4. Read `report.txt` diagnostics and archive summary metrics.
5. Add a regression test once root cause is identified.

Validation:
- model appears in the planned export set
- at least one valid fragment survives localization and validation
- output name is deterministic

Do not:
- assume a missing streamed model means missing sector traversal only
- assume one bad fragment means the model has no valid candidate
