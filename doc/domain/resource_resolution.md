# Resource Resolution

Rule:
1. AREA patched
2. LVZ master
3. sector overlay

Meaning:
- AREA variants are highest priority because they patch the shared resource space.
- LVZ master variants are the base shared resources.
- Sector overlay variants are fallback candidates, not universal truth.

Required behavior:
- Collect all distinct blobs for a `res_id`.
- Deduplicate by blob content, not by `res_id` alone.
- Keep source provenance for each candidate.
- Decode candidates independently.
- Cache geometry per variant identity, not per `res_id`.

Selection rules during export:
- Try multiple decoded candidates for the same `res_id`.
- Reject candidates whose localized fragment geometry is invalid.
- Prefer candidates that match the dominant localized bounds signature already accepted for the model.
- If signatures tie, prefer by origin priority:
  - AREA
  - master
  - overlay

Do:
- record diagnostics when one `res_id` yields multiple decoded variants
- keep deterministic candidate ordering

Do not:
- overwrite AREA blobs with the last one seen
- choose one blob for a `res_id` and reuse it blindly everywhere
- treat overlay blobs as globally authoritative
