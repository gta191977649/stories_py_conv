# Invariants

Machine-checkable invariants:
- no exported mesh contains NaN or Inf vertex coordinates
- no exported mesh contains extreme out-of-bounds coordinates beyond current fragment validation thresholds
- streamed local transform uses `inverse(anchor_matrix) * placement_matrix`
- exact linked-entity localization is used when the active placement set resolves to one linked entity
- streamed resource resolution order is deterministic
- geometry cache identity includes resource variant identity, not only `res_id`
- alternate placement cluster disagreement is reported without silently replacing the primary export
- repeated runs on the same input choose the same candidate ordering

Streamed-specific invariants:
- multiple resource candidates per `res_id` remain representable
- AREA resources are not overwritten by later AREA blobs
- fragment validation happens after localization
- a model can export multiple merged fragments

CLI invariants:
- `--buildimg` requires `--export`
- at least one action flag is required

When adding tests:
- prefer direct invariant assertions over screenshot-only checks
- use real-data regression tests only when synthetic coverage is insufficient
