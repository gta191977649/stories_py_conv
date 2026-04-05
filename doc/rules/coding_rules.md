# Coding Rules

Do:
- preserve current extraction semantics unless a test-backed bug fix requires change
- keep streamed and standard archive logic separate
- add tests for every rule change in transforms, resource resolution, or geometry decode
- keep diagnostics explicit when behavior is ambiguous
- prefer deterministic ordering for candidates, clusters, and exports

Do not:
- “simplify” reverse-engineered logic without a reference and regression proof
- collapse multi-stage selection into first-match logic
- introduce behavior that depends on dict insertion order when output stability matters
- reuse standard-archive assumptions in streamed code paths

When editing:
- update docs if behavior changes
- update invariants/tests together with code
- keep naming stable unless there is a correctness bug
