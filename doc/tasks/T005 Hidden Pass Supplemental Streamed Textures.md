# T005 Hidden Pass Supplemental Streamed Textures

## Status
completed

## Type
bug

---

## Goal
Preserve valid hidden supplemental streamed pass geometry when it contributes texture sets that are missing from the exported primary hidden pass.

---

## Context
- the problem exists in hidden-only streamed interior export for models like `dr_stallionz_int`
- related modules/files:
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md)
- relevant system behavior:
  - hidden-only clusters are split by `pass_index`
  - the exporter currently stops after the first hidden pass that has any textures
  - `dr_stallionz_int` keeps `res_id=5514` and drops `res_id=5568`, which contains `tex_2_082d`

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic

---

## Plan
1. Trace the hidden-only cluster export path for `dr_stallionz_int` and confirm which pass contains the missing streamed texture.
2. Change hidden-pass merge behavior deterministically so later hidden pass groups can merge only when they add disjoint texture IDs.
3. Add regression coverage for textured hidden supplemental passes with disjoint texture sets.
4. Verify real-data export for `dr_stallionz_int` includes `tex_2_082d` without removing the existing hidden-pass split safeguard.

---

## Verification (MANDATORY)
- unit test proves a hidden supplemental pass with disjoint texture IDs is merged
- existing hidden-pass split regression still passes
- real-data `dr_stallionz_int` export includes `tex_2_082d`
- exported DFF still writes deterministic material assignments

---

## Files
- `/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`
- `/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py`
- `/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`

---

## Last Run Result
SUCCESS

Details:
- `python -m unittest tests.test_streamed_backend` passed with `39` tests
- targeted real-data export to `/tmp/dr_stallionz_pass_fix/MAINLA/dr_stallionz_int.dff` wrote `74` materials
- the written DFF now includes `tex_2_082d`, which comes from `MAINLA` sector `605` overlay resources
- the root cause was hidden-only export stopping after the first textured pass and never evaluating the disjoint supplemental pass `5568`

---

## Next Action
- monitor future streamed interiors for hidden supplemental passes that should merge despite pass separation

---

## Notes (optional)
- `tex_2_062a` is a real but empty master streamed texture blob and is not the missing lattice texture
- `tex_2_082d` exists in `MAINLA` sector `605` overlay resources and visually matches the expected lattice/net texture
