# T004 Opaque DXT1 Transparent Block Fix

## Status
completed

## Type
bug

---

## Goal
Stop opaque packed TXD textures from emitting DXT1 transparent-mode blocks that make building walls render with holes.

---

## Context
- the problem exists in the TXD packing path for opaque streamed textures
- `doontoon26` is a concrete repro where side wall textures render as transparent in the viewer even though the source textures are opaque
- related modules/files:
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md)
- relevant system behavior:
  - the raw helper output is correct, so the issue is not in streamed texture decode
  - current packed `knackers.txd` contains opaque DXT1 textures whose blocks use `color0 <= color1`, which activates DXT1 transparent mode in downstream viewers
  - flat or near-flat dark facade blocks are the main trigger

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic

---

## Plan
1. confirm the affected model textures are opaque in the raw helper output and in DFF material state
2. verify the packed TXD contains opaque DXT1 blocks with `color0 <= color1`
3. update the opaque DXT1 endpoint selection so solid-color blocks always stay in four-color mode
4. add regression coverage for a flat opaque block and for the resulting packed block ordering
5. rewrite the current `knackers.txd` with the fixed packer and verify the affected `doontoon26` textures no longer contain transparent-mode DXT1 blocks

---

## Verification (MANDATORY)
- opaque `DXT1` blocks written by the packer use `color0 > color1`
- decoded alpha remains fully opaque for flat opaque textures
- `doontoon26` packed wall textures no longer contain transparent-mode blocks after rewrite
- `python -m unittest tests.test_pure_backend` passes

---

## Files
- [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_pure_backend.py)
- [`/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md)

---

## Last Run Result
SUCCESS

Details:
- raw-vs-packed RGB error for `doontoon26` textures is already low, so the issue is not general compression quality loss
- the root cause was opaque single-color `DXT1` blocks returning `color0 == color1`, which activates transparent mode in downstream decoders
- `python -m unittest tests.test_pure_backend` passed with `21` tests
- the current [`/Users/nurupo/Desktop/ps2/vcs_gta3_img/knackers.txd`](/Users/nurupo/Desktop/ps2/vcs_gta3_img/knackers.txd) was surgically rewritten for the `doontoon26` texture set
- the affected textures `tex_2_0370`, `tex_2_0361`, `tex_2_0385`, `tex_2_0362`, `tex_2_00e0`, `tex_2_027f`, `tex_2_029a`, `tex_2_0098`, `tex_2_0224`, and `tex_2_0239` now each have `0` packed blocks with `color0 <= color1`
- spot checks on `tex_2_0385` and `tex_2_0239` still decode with alpha `255` for every pixel

---

## Next Action
- apply the same fixed packer on the next full export so the rest of the shared TXD archive is regenerated under the corrected rule

---

## Notes (optional)
- this is a separate issue from the broader DXT quality gate in `T002`
- the safest fix is deterministic endpoint ordering for opaque blocks, not filename or texture-specific exceptions
