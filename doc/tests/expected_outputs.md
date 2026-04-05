# Expected Outputs

Export run should produce:
- per-archive directories under output root
- `.dff` for decodable models
- `.col` for exported meshes
- `.txd` buckets for decoded textures
- root `knackers.txd` when shared textures are collected
- `report.txt`
- `report.txt` includes `vcsnames.ini` coverage counts plus separate geometry-missing and non-geometry/reference-only sections

Decode-dat run should produce:
- `output/data/maps/*.ide`
- `output/data/maps/*.ipl`
- `output/data/gta.dat`

Expected quality properties:
- exported DFF local pivots align with linked IPL/GAME.dat anchors for streamed models
- streamed outputs are deterministic for the same input dump
- conflicts and skips are reported, not hidden

Expected non-properties:
- not every IMG entry converts into a RW asset
- unresolved streamed names can remain hashed
- unrecoverable streamed interiors do not need archive/sector/res synthetic export names
- corrupt fragments may be dropped to salvage a model
