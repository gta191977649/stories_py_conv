# T006 Conversion Pipeline Performance Breakdown

## Status
completed

## Type
research

---

## Goal
Measure end-to-end conversion time by pipeline step, identify the dominant bottlenecks, and implement behavior-preserving performance optimizations.

---

## Context
- the problem exists in the full converter run driven by [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/app.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/app.py)
- related modules/files:
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/app.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/app.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py)
  - [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/report.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/report.py)
- relevant system behavior:
  - standard archives and streamed archives must still export the same logical model set
  - streamed transform/resource invariants must remain unchanged
  - before/after validation must compare missing-model output and total runtime on the real game dump

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic
- must not alter original conversion semantics or drop models
- must keep deterministic resource-resolution behavior

---

## Plan
1. Record a real-data baseline run and capture total runtime plus missing-model output.
2. Add behavior-neutral timing instrumentation so each top-level pipeline step reports elapsed time.
3. Profile the hottest sections enough to identify which implementation paths dominate runtime.
4. Apply behavior-preserving optimizations to the dominant steps only.
5. Re-run the full conversion, compare total runtime before vs after, and verify missing-model lists stay identical.

---

## Verification (MANDATORY)
- full real-data export completes before and after optimization
- report contains comparable missing-model lists before and after
- missing-model lists are identical between baseline and optimized runs
- per-step timing data identifies the dominant pipeline phases
- total end-to-end runtime is materially lower after optimization

---

## Files
- /Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/app.py
- /Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/perf.py
- /Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/pure_backend.py
- /Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/streamed_backend.py
- /Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/report.py
- /Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/models.py
- /Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/T006 Conversion Pipeline Performance Breakdown.md

---

## Last Run Result
SUCCESS

Details:
- baseline timed full export: `/tmp/vcs_perf_timed_before`, `real 318.62s`, missing models=`363`
- optimized full export: `/tmp/vcs_perf_after3`, `real 263.62s`, missing models=`363`
- end-to-end wall clock improved by `55.00s` (`17.3%`)
- largest timed reductions:
  - `app.export_streamed_models`: `181.663s -> 153.117s`
  - `app.write_global_knackers_txd`: `82.134s -> 57.910s`
- missing-model list matched exactly before vs after
- targeted regression suite passed:
  - `./.venv/bin/python -m unittest tests.test_app tests.test_streamed_backend`

---

## Next Action
- If more performance is needed, focus next on reducing `knackers.txd` serialization cost and replacing thread-based archive overlap with a lower-overhead parallel strategy.
