# T003 Streamed Texture Name Noise Filter

## Status
completed

## Type
bug

---

## Goal
Prevent streamed texture blobs from misnaming textures by treating incidental uppercase byte runs like `HUD` as real embedded asset names.

---

## Context
- the problem exists in streamed texture name recovery inside [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py)
- `wshbuildws07` and similar models were getting one material slot named `hud` instead of the expected synthetic streamed name `tex_1_0455`
- related modules/files:
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py)
- relevant system behavior:
  - `_recover_texture_name()` scanned the entire blob for any ASCII token that matched a known VCS name
  - texture `0x0455` contained incidental bytes `HUD` at offset `2574`, causing a false embedded-name match
  - the real texture should fall back to `tex_<level>_<resid>` when no trustworthy embedded/hash name exists

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic

---

## Plan
1. reproduce the wrong `hud` name on a real streamed texture used by `wshbuildws07`
2. confirm the geometry actually references `0x0455`, not a real `hud` resource id
3. tighten embedded-name recovery to accept only plausible asset names instead of arbitrary uppercase byte runs
4. add a regression that proves `HUD`-style noise falls back to `tex_1_0455`
5. refresh the current output tree enough to remove stale `hud` references and align `knackers.txd`

---

## Verification (MANDATORY)
- `_recover_texture_name()` returns `tex_1_0455` for the `HUD` noise case instead of `hud`
- real BEACH texture `0x0455` decodes as `tex_1_0455`
- exported [wshbuildws07.dff](/Users/nurupo/Desktop/ps2/vcs_gta3_img/BEACH/wshbuildws07.dff) references `tex_1_0455`
- current [knackers.txd](/Users/nurupo/Desktop/ps2/vcs_gta3_img/knackers.txd) contains `tex_1_0455` and no `hud`
- current exported DFF scan finds `0` models still referencing `hud`

---

## Files
- [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md)

---

## Last Run Result
SUCCESS

Details:
- real BEACH texture `0x0455` now resolves as `tex_1_0455` instead of `hud`
- `python -m unittest tests.test_streamed_backend` passed with `37` tests
- current output verification shows:
  - `wshbuildws07` material list contains `tex_1_0455`
  - `knackers.txd` contains `tex_1_0455` and no `hud`
  - exported DFF scan found `0` remaining `hud` texture references

---

## Next Action
- let the next full clean export rebuild the entire output tree under the corrected streamed texture-name heuristic

---

## Notes (optional)
- the filter intentionally trusts lowercase embedded asset names such as `hotelwall` but rejects short uppercase byte noise like `HUD`
- current-output cleanup was limited to the shared `knackers.txd` name and affected DFF material references already present on disk
