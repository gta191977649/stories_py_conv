# Add CLI Flag

Goal:
- Add a new CLI option without breaking the command contract or orchestration flow.

Procedure:
1. Update parser definitions in `cli.py`.
2. Enforce validation rules in `main()`.
3. Thread the new option into `app.run()`.
4. Keep phase ordering and output behavior explicit.
5. Update README and CLI tests.
6. If the flag changes export semantics, update affected docs under `/doc`.

Validation:
- invalid combinations fail with parser errors
- valid combinations preserve existing behavior
- tests cover parser and runtime behavior

Do not:
- add hidden side effects to existing flags
- overload `--export` semantics without documenting the change
