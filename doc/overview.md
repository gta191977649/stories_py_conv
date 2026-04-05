# Overview

Purpose:
- Extract GTA Vice City Stories PS2 map/static assets into GTA RenderWare-compatible outputs.
- Decode both standard IMG archives and streamed LVZ+IMG map archives.
- Reconstruct IDE/IPL-like metadata from `GAME.dat`.

Primary entry points:
- CLI: [`src/vcs_map_extract/cli.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/cli.py)
- Streamed texture debug helper: [`txd_extract_test.py`](/Users/nurupo/Desktop/dev/stories_py_conv/txd_extract_test.py)
- App orchestration: [`src/vcs_map_extract/app.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/app.py)
- Standard archive conversion: [`src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
- Streamed archive planning: [`src/vcs_map_extract/streamed_world.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_world.py)
- Streamed archive export: [`src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py)
- `GAME.dat` decoding: [`src/vcs_map_extract/game_dat.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/game_dat.py)

Repository model:
- Standard archives:
  - `GTA3PS2.IMG`
  - `MOCAPPS2.IMG`
- Streamed archives:
  - `BEACH.LVZ + BEACH.IMG`
  - `MAINLA.LVZ + MAINLA.IMG`
  - `MALL.LVZ + MALL.IMG`

Non-negotiable facts:
- Streamed archives are not flat named-file archives.
- Streamed export is anchor-driven, not raw-placement-driven.
- Resource resolution is multi-source and variant-aware.
- A visible model can require multiple fragments from multiple resource blobs.

Useful debug tool:
- Use [`txd_extract_test.py`](/Users/nurupo/Desktop/dev/stories_py_conv/txd_extract_test.py) to dump one streamed texture by archive and `res_id` into `OUTPUT_DEBUG/` without running a full export.

Task tracking:
- Non-trivial work is tracked under [`/doc/tasks/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md).
- Agents should create or update the corresponding task file whenever they advance a bug, feature, refactor, or research item.

Read next:
- [`/doc/architecture/pipeline.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/architecture/pipeline.md)
- [`/doc/domain/lvz_img_structure.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/domain/lvz_img_structure.md)
- [`/doc/domain/transform_model.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/domain/transform_model.md)
