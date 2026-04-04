# Pipeline

Top-level run order:
1. Normalize and validate input root.
2. Optionally clean output.
3. Optionally decode `GAME.dat`.
4. Load IDE/IPL metadata and merge with `GAME.dat` model/link information.
5. Queue and convert standard archive jobs.
6. Plan streamed archives.
7. Export streamed archives.
8. Write `knackers.txd`, `report.txt`, and optional packed IMG outputs.

Standard archive path:
1. Read IMG directory entries.
2. Queue `.mdl -> .dff`.
3. Queue texture entries to `.txd`.
4. Copy unsupported/non-converted raw payloads when applicable.
5. Convert through the standard backend.

Streamed archive path:
1. Parse LVZ metadata into a `LevelChunk`.
2. Traverse reachable world, swap, and interior sectors.
3. Read sector instance records and area resource tables.
4. Resolve streamed placements to canonical model names with `NameResolver`.
5. Group placements into fragment clusters.
6. Export each model plan:
   - choose base transform
   - resolve exact linked anchor when the active placement set maps to one linked entity
   - evaluate candidate resource variants
   - validate transformed fragment vertices
   - merge accepted fragments
   - write `.dff`, `.col`, and per-TXD textures

Generated outputs:
- `OUTPUT/<archive>/*.dff`
- `OUTPUT/<archive>/*.col`
- `OUTPUT/<archive>/*.txd`
- `OUTPUT/knackers.txd`
- `OUTPUT/report.txt`
- optional packed IMG outputs

Failure reporting:
- Streamed problems are written into `ReportData.streamed_diagnostics`.
- Interior-name and anchor issues are written into `ReportData.interior_diagnostics`.
- IPL decode notes are written into `ReportData.ipl_diagnostics`.
