# Tasks

Purpose:
- keep active, backlog, and completed engineering work visible under `doc/`
- give every agent a deterministic place to record scope, constraints, verification, and current state

Rules:
- every non-trivial bug, feature, refactor, or research task must have a task file under `doc/tasks/`
- use the `TXXX Task Name` format exactly
- update the corresponding task file in the same change that implements or advances the work
- keep `Status`, `Last Run Result`, and `Next Action` current
- do not leave stale plans after the task direction changes
- if a task is completed, move its `Status` to `completed` and record the final verification result

When to create a new task:
- the work spans multiple files
- the work has non-obvious constraints or verification
- the work may continue across multiple turns or agents

When a task can be skipped:
- a one-line doc typo
- a trivial rename with no behavioral change

Task file format:
- [`/doc/tasks/T000_task_template.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/T000_task_template.md)

Current tasks:
- [`/doc/tasks/T001 TXD Packing Opaque DXT Policy.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/T001%20TXD%20Packing%20Opaque%20DXT%20Policy.md)
- [`/doc/tasks/T002 TXD Packing Quality Gate.md`](/Users/nurupo/Desktop/dev/stories_py_conv/doc/tasks/T002%20TXD%20Packing%20Quality%20Gate.md)
