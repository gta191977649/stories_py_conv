# T007 Streamed Neon Material Texture Recovery

## Status
completed

## Type
bug

---

## Goal
Restore missing streamed neon texture mappings for models like `jm_dt22_neon` without regressing existing fragment export behavior.

---

## Context
- the problem exists in streamed model export where neon geometry is present but some faces lose their intended texture mapping
- related modules/files:
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py)
- relevant system behavior:
  - streamed models are fragment assemblies and must preserve per-face material assignments through merge
  - `jm_dt22_neon` currently exports with incomplete texture references in the generated DFF
  - the failure may be in streamed texture-id decode, material naming, or export-side face-material preservation

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic

---

## Plan
1. inspect the current `jm_dt22_neon` DFF and streamed source plan to determine which texture ids are expected and which are missing.
2. trace the streamed geometry path from decoded face texture ids to exported DFF material slots.
3. patch the deterministic failure point, add regression coverage, and verify `jm_dt22_neon` exports with the expected neon texture references.

---

## Verification (MANDATORY)
- define the exported material names for `jm_dt22_neon` after the fix
- confirm the expected streamed texture ids are present in `knackers.txd`
- `python -m unittest tests.test_streamed_backend` passes

---

## Files
- `/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`
- `/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`
- `/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py`

---

## Last Run Result
SUCCESS

Details:
- root cause 1: truncated streamed descriptor blobs for supplemental overlay fragments were rejected by `_parse_streamed_mesh_descriptors`, then mis-read by the MDL fallback path
- root cause 2: export selection kept only the single best same-priority variant for a `res_id`, even when another same-bounds overlay variant contributed non-duplicate geometry
- fixes applied in [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py):
  - accept truncated streamed packet tails and let packet parsing clamp to available bytes
  - merge same-priority same-bounds localized variants when they contribute distinct geometry
- verification on 2026-04-06:
  - `python -m unittest tests.test_streamed_backend`
  - `Ran 42 tests in 24.053s`
  - `OK`
  - targeted export rewrote [`/Users/nurupo/Desktop/ps2/vcs_gta3_img/MAINLA/jm_dt22_neon.dff`](/Users/nurupo/Desktop/ps2/vcs_gta3_img/MAINLA/jm_dt22_neon.dff)
  - `jm_dt22_neon.dff` now has `80` faces instead of `48`
  - exported material list remains `tex_2_01f6`, which is the expected neon texture for the merged geometry

---

## Next Action
- monitor other streamed neon/sign models for the same truncated-overlay supplemental-geometry pattern

---

## Notes (optional)
- `jm_dt22_neon` specifically had two overlay variants for `res_id=1047`; the second fragment was real geometry, not an alternate to discard
