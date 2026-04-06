# T006 Tiny Indexed Streamed Gradient Decode

## Status
completed

## Type
bug

---

## Goal
Decode tiny paletted streamed textures with the correct PS2 swizzle choice so gradient ramps do not collapse into blocky mosaic patterns.

---

## Context
- the problem exists in streamed PS2 texture decode for small indexed textures like `tex_2_12d8`
- related modules/files:
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md)
- relevant system behavior:
  - decoder tries alternate swizzle variants because some streamed headers lie
  - current scoring prefers a swizzled `8x8` mosaic for `tex_2_12d8`
  - the correct decode is the smoother `swizzle=0` blue-to-red ramp

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic

---

## Plan
1. compare raw decode candidates for `tex_2_12d8` and confirm the current heuristic picks the wrong swizzle variant.
2. adjust tiny indexed texture scoring deterministically so correct small gradients beat blocky alternate swizzles.
3. add regression coverage for the `MAINLA` `0x12d8` case.
4. verify helper output and packed TXD output both use the corrected decode.

---

## Verification (MANDATORY)
- `txd_extract_test.py` for `MAINLA 4824` writes the smooth blue-to-red ramp
- the packed `knackers.txd` copy of `tex_2_12d8` matches the corrected raw decode
- `python -m unittest tests.test_streamed_backend` passes

---

## Files
- `/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`
- `/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_streamed_backend.py`
- `/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`

---

## Last Run Result
SUCCESS

Details:
- the root cause was the tiny-texture scoring heuristic in [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py), which preferred the wrong alternate `swizzle=1` decode for `MAINLA` `0x12d8`
- the decoder now adds a small diversity bonus for tiny indexed textures so the header-consistent `swizzle=0` blue-to-red ramp beats the blocky mosaic candidate
- verification passed on 2026-04-06:
  - `python -m unittest tests.test_streamed_backend`
  - `Ran 40 tests in 24.635s`
  - `OK`
  - helper output regenerated at [`/Users/nurupo/Desktop/dev/stories_py_conv/OUTPUT_DEBUG/tex_2_12d8_from_tool_after_fix.png`](/Users/nurupo/Desktop/dev/stories_py_conv/OUTPUT_DEBUG/tex_2_12d8_from_tool_after_fix.png)

---

## Next Action
- monitor for other tiny indexed textures that may still need a stronger discriminator than color diversity alone

---

## Notes (optional)
- the source blob is an `8x8`, `8bpp` PS2 texture with `swizzle_mask=0`
- this issue is in raw streamed decode, not in DXT packing
