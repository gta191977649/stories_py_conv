# CLI Contract

Command shape:
- `vcs-map-extract INPUT OUTPUT [--clean] [--export] [--buildimg] [--decode-dat] [--dxt-level {1,2,3,4,5}]`

Arguments:
- `INPUT`
  - game root directory
  - or `.DIR` sidecar path for standard archives
- `OUTPUT`
  - destination root

Flags:
- `--clean`
  - delete existing output contents before other actions
- `--export`
  - required for asset extraction
- `--buildimg`
  - requires `--export`
  - packs generated RW outputs into IMG files
- `--decode-dat`
  - decodes `GAME.dat` into IDE/IPL text outputs
- `--dxt-level`
  - controls TXD compression during export
  - applies per texture; opaque rasters still emit `DXT1/RASTER_565` when a higher alpha-capable DXT level is requested
  - may fall back to uncompressed per texture when the compressed roundtrip exceeds the current deterministic quality gate

Validation rules:
- At least one of `--clean`, `--export`, or `--decode-dat` must be set.
- `--buildimg` without `--export` is invalid.
- `--dxt-level` only matters during export.

Behavior contract:
- `--decode-dat` can run without `--export`.
- `--export` on a game root processes standard and streamed archives.
- Output layout is archive-partitioned plus shared root artifacts.

Do not change without updating:
- [`README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/README.md)
- [`src/vcs_map_extract/cli.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/cli.py)
- tests covering CLI error handling and behavior
