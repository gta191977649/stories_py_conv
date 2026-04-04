# Data Flow

Metadata flow:
- `ide/*.ide` -> `parse_ide_directory()` -> IDE catalog
- `ipl/*.ipl` -> `parse_ipl_directory()` -> IPL summary
- `GAME.dat` -> `GameDat.from_path()` -> model info + streamed anchor summary
- IDE + `GAME.dat` -> `NameResolver`
- IPL summary + `GAME.dat` summary -> merged anchor source

Standard asset flow:
- IMG entry -> temp raw file -> pure backend conversion -> exported RW file

Streamed asset flow:
- `LVZ + IMG` -> `LevelChunk`
- `LevelChunk` -> reachable sectors + area tables + sector instances
- sector instances -> `StreamedPlacement`
- placements + resolver -> `StreamedModelPlan`
- model plan + merged IPL summary -> localized fragment transforms
- resource variants -> decoded geometry
- decoded fragments -> validated merged mesh -> `.dff/.col`
- decoded textures -> TXD buckets -> `.txd`

Transform flow:
- streamed instance placement matrix
- linked entity id from `vcs_links.inc`
- linked entity transform from merged IPL summary
- `local_matrix = inverse(anchor_matrix) * placement_matrix`
- local matrix applied to each fragment vertex before merge

Resource flow for streamed `res_id`:
- collect AREA-patched variants
- collect LVZ master variants
- collect sector overlay variants
- decode candidate variants
- score candidates against placement-localized geometry
- choose best valid candidate per placement

Report flow:
- export metrics by archive -> `summary_by_archive`
- warnings/conflicts -> `report.txt`
