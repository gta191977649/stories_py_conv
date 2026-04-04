# Geometry Rules

DO:
- treat a final streamed model as a set of fragments
- keep multiple resource candidates for the same `res_id`
- validate transformed fragment vertices before merge
- reject corrupt fragments instead of poisoning the full model
- preserve per-face material/texture assignment through merge

DO NOT:
- collapse a model to a single resource blob by assumption
- merge fragments before localization
- skip fragment validation because a decode succeeded
- cache decoded geometry by `res_id` alone

Fragment validation minimums:
- vertices must be finite
- vertices must stay within sane bounds
- merged mesh must remain finite before DFF/COL export

Decoder parity rules:
- streamed-building packet path uses its own numeric decode rules
- MDL-style fallback uses its own numeric decode rules
- do not unify those paths unless reference parity and regression tests stay green
