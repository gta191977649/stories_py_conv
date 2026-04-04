# T001 TXD Packing Opaque DXT Policy

## Status
completed

## Type
bug

---

## Goal
Make TXD packing preserve correct opaque texture compression behavior so opaque streamed textures do not get written as blurry `DXT3/RASTER_8888` rasters.

---

## Context
- the problem existed in TXD packing, not in streamed raw texture decode
- the issue showed up in [`/Users/nurupo/Desktop/ps2/vcs_gta3_img/knackers.txd`](/Users/nurupo/Desktop/ps2/vcs_gta3_img/knackers.txd) for `tex_2_13c1`
- related modules/files:
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/playbooks/add_decoder.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/playbooks/add_decoder.md)
- relevant system behavior:
  - streamed decode produced the correct raw PNG
  - packed TXD still wrote opaque textures as `DXT3` when `--dxt-level 3` was used
  - `librw` reference behavior keeps opaque rasters on `DXT1/RASTER_565`

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic

---

## Plan
1. verify that the raw streamed texture output is correct and isolate the defect to the TXD writer
2. compare TXD packing behavior against `librw` reference handling for opaque vs alpha textures
3. patch the TXD writer so requested DXT level is applied per texture, downgrading opaque rasters to `DXT1`
4. add deterministic unit coverage for opaque `--dxt-level 3` requests
5. rewrite the affected packed TXD and inspect the resulting raster metadata and roundtrip output
6. update the decoder playbook and CLI-facing docs

---

## Verification (MANDATORY)
- `tex_2_13c1` inside packed `knackers.txd` reports `D3D_DXT1`, `RASTER_565`, `depth=16`, `alpha=False`
- packed TXD extraction remains visually aligned with the raw extracted PNG
- no opaque compressed textures in the targeted rewritten `knackers.txd` remain on a non-`DXT1` format
- unit tests cover both alpha and opaque DXT selection behavior

---

## Files
- [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/playbooks/add_decoder.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/playbooks/add_decoder.md)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/architecture/cli_contract.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/architecture/cli_contract.md)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/README.md)

---

## Last Run Result
SUCCESS

Details:
- targeted rewrite of [`/Users/nurupo/Desktop/ps2/vcs_gta3_img/knackers.txd`](/Users/nurupo/Desktop/ps2/vcs_gta3_img/knackers.txd) completed
- `tex_2_13c1` now loads as `D3D_DXT1`, `RASTER_565`, `depth=16`
- targeted TXD scan found `0` opaque compressed textures still using a non-`DXT1` format
- `python -m unittest tests.test_pure_backend` passed with `18` tests

---

## Next Action
- run a full clean export when the next texture QA pass needs whole-tree confirmation instead of targeted rewritten outputs

---

## Notes (optional)
- `--dxt-level` remains a user request, but the effective compression choice must still honor texture alpha usage
- this task intentionally did not change streamed texture decoding or mesh export logic
