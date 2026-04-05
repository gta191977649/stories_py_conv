# Export Rules

DO:
- export per-archive outputs into archive-specific directories
- keep `knackers.txd` handling consistent with the current shared-root behavior
- write `report.txt` diagnostics for ambiguity, conflicts, and skips
- preserve deterministic export selection for repeated runs

DO NOT:
- skip the primary streamed export only because alternate clusters disagree materially after localization
- silently drop ambiguity in resource variants or cluster selection
- hide collision/DFF failures without reporting them

Stability rules:
- same input should produce the same chosen candidate ordering
- same resolved model name should map to the same output stem
- multi-variant `res_id` handling must be deterministic
- alternate-cluster disagreement should be reported while preserving primary-cluster export

Required outputs:
- `.dff`
- `.col`
- `.txd` when textures exist
- root `knackers.txd` aggregation when used
