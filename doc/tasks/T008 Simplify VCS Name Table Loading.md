# T008 Simplify VCS Name Table Loading

## Status
completed

## Type
refactor

---

## Goal
Make `load_vcs_name_table()` load names only from `vcsnames.ini` and remove dead fallback parsing logic.

---

## Context
- the relevant code is in [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/reference_data.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/reference_data.py)
- `load_vcs_name_table()` still contains fallback parsing for `.inc` sources that are no longer present in [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/data/reference`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/data/reference)
- related test coverage lives in [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_reference_data.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_reference_data.py)

---

## Constraints (CRITICAL)
- must follow [`/doc/rules/transform_rules.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/rules/transform_rules.md)
- must not break existing export pipeline
- must preserve fragment merging logic

---

## Plan
1. remove `.inc` fallback parsing from `load_vcs_name_table()`.
2. tighten the reference-data test so it asserts `vcsnames.ini` is the only active source.
3. run the focused reference-data test module.

---

## Verification (MANDATORY)
- `load_vcs_name_table()` reads only `vcsnames.ini`
- `python -m unittest tests.test_reference_data` passes

---

## Files
- `/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/reference_data.py`
- `/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_reference_data.py`
- `/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/README.md`

---

## Last Run Result
SUCCESS

Details:
- removed dead `.inc` fallback parsing from [`/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/reference_data.py`](/Users/nurupo/Desktop/dev/stories_py_conv/src/vcs_map_extract/reference_data.py)
- added a focused test in [`/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_reference_data.py`](/Users/nurupo/Desktop/dev/stories_py_conv/tests/test_reference_data.py) that asserts `load_vcs_name_table()` reads only `vcsnames.ini`
- verification on 2026-04-06:
  - `python -m unittest tests.test_reference_data`
  - `Ran 2 tests in 0.005s`
  - `OK`

---

## Next Action
- no further action unless you also want the same single-source policy documented in README or overview docs

---

## Notes (optional)
- the reference directory currently contains `vcsnames.ini` and `vcs_links.inc`, but no `vcsnames.inc` or `bruteforcedvcsnames.inc`
