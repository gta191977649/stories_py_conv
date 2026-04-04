# AGENTS.md

Read in this order:
- [`/doc/overview.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/overview.md)
- [`/doc/architecture/pipeline.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/architecture/pipeline.md)
- [`/doc/domain/transform_model.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/domain/transform_model.md)
- [`/doc/domain/resource_resolution.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/domain/resource_resolution.md)
- [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- [`/doc/rules/geometry_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/geometry_rules.md)
- [`/doc/tests/invariants.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tests/invariants.md)

Core invariants:
- Streamed placement export uses `local_matrix = inverse(anchor_matrix) * placement_matrix`.
- Anchor resolution comes from `vcs_links.inc -> linked entity id -> GAME.dat` or merged IPL summary, not from the raw streamed WRLD matrix alone.
- When the active placement set resolves to one linked entity, the exporter uses that exact linked anchor for streamed localization.
- Streamed resource lookup order is `AREA patched -> LVZ master -> sector overlay`.
- A streamed `res_id` can have multiple candidate blobs. Try candidates; do not assume one blob per `res_id`.
- Streamed texture blobs may be larger than the actual raster payload. Preserve the raw resource address, derive the local raster offset from the PS2 header, and size palette reads from the minimal raster block instead of the whole blob tail.
- Streamed models are fragment assemblies. Do not collapse a model to one resource blob or one fragment.

Critical constraints:
- Never treat `BEACH/MAINLA/MALL` like standard named IMG archives.
- Never export streamed geometry in raw world space when an anchor exists.
- Never replace fragment validation with blind merge logic.
- Never “simplify” resource resolution order.
- Do not change alternate-cluster handling to skip primary export unless you have a proven regression and updated tests.

Common failure modes:
- Transform misuse: using the placement matrix directly, or anchoring against the wrong linked entity.
- Incorrect resource resolution: using only the first blob for a `res_id`, or ignoring AREA-patched variants.
- Incorrect streamed texture slicing: assuming the palette sits at the end of the whole resource blob, or dropping the raw resource pointer needed to recover the local raster offset.
- Standard-IMG assumptions on streamed archives: expecting one named model file per model.

Use these docs by task:
- Transform bug: [`/doc/playbooks/fix_transform_bug.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/playbooks/fix_transform_bug.md)
- Decoder work: [`/doc/playbooks/add_decoder.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/playbooks/add_decoder.md)
- Missing model: [`/doc/playbooks/debug_missing_model.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/playbooks/debug_missing_model.md)
- CLI changes: [`/doc/playbooks/add_cli_flag.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/playbooks/add_cli_flag.md)

* Pleases update the cosponed task document when fixed.


Toools:
* python local env : project_dir/.venv
* librw (renderware source if you need reference to rw implmentation): /Users/nurupo/Desktop/dev/librwgta-master
