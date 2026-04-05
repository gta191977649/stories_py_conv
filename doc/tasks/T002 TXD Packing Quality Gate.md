# T002 TXD Packing Quality Gate

## Status
completed

## Type
bug

---

## Goal
Prevent obviously destructive DXT packing for billboard and text-heavy textures by falling back to uncompressed TXD rasters when compressed roundtrip quality is too poor.

---

## Context
- the problem exists in the TXD packing path after raw streamed texture decode is already correct
- the clearest repro is `tex_2_01c0` from `MAINLA` `res_id=448`, which looked heavily pixelated after packed TXD export
- related modules/files:
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/README.md)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/architecture/cli_contract.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/architecture/cli_contract.md)
- relevant system behavior:
  - the raw helper output from [`/Users/nurupo/Desktop/dev/stories_py_conv/txd_extract_test.py`](/Users/nurupo/Desktop/dev/stories_py_conv/txd_extract_test.py) is correct
  - some opaque textures are valid `DXT1` candidates, such as `tex_2_13c1`
  - some opaque billboard/text textures produce unacceptable `DXT1` loss and should not remain compressed

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic

---

## Plan
1. verify raw-vs-packed mismatch on a real billboard texture using the helper output and current `knackers.txd`
2. measure compressed roundtrip error deterministically inside the TXD writer
3. keep good compressed cases compressed and fall back to uncompressed rasters only when error crosses a fixed threshold
4. add unit coverage for both a good facade case and a lossy billboard-like case
5. rewrite the current packed `knackers.txd` and inspect the resulting texture metadata
6. update task/docs so future agents maintain the same behavior intentionally

---

## Verification (MANDATORY)
- `tex_2_01c0` no longer writes as a compressed DXT raster when `--dxt-level 3` is used
- `tex_2_01c0` packed roundtrip remains visually close to the raw helper output
- `tex_2_13c1` still stays compressed because its DXT roundtrip is within the acceptable threshold
- unit tests cover a billboard-like fallback case and a good compressed facade case

---

## Files
- [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/README.md)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/architecture/cli_contract.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/architecture/cli_contract.md)

---

## Last Run Result
SUCCESS

Details:
- `tex_2_01c0` raw-vs-packed DXT roundtrip error before the fix was about `mean_rgb=39.03`, `max_rgb=240`
- after the quality gate, the same texture selects uncompressed `D3D_565` with `compressed=False` and roundtrip error about `mean_rgb=1.93`, `max_rgb=4`
- facade probe `tex_2_13c1` still stays compressed because its DXT roundtrip stays within threshold
- a full export crash exposed one more case: non-block-aligned textures such as `1x32` cannot safely roundtrip through the DXT gate
- the gate now rejects non-`4x4`-aligned textures before attempting compressed roundtrip validation
- `python -m unittest tests.test_pure_backend` passed with `20` tests

---

## Next Action
- run a full clean export when broad TXD quality validation across the whole output tree is needed instead of the current targeted rewrite

---

## Notes (optional)
- the fallback is deterministic and based on measured roundtrip quality, not filename heuristics
- this task intentionally treats `--dxt-level` as a best-effort compression request rather than a command to degrade obviously unsuitable textures
