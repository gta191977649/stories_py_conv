# Fix Transform Bug

Goal:
- Correct a streamed object whose exported DFF pivot/orientation/offset does not match its linked IPL placement.

Procedure:
1. Identify archive, model name, and affected placements.
2. Resolve the streamed `world_id` through `vcs_links.inc`.
3. Resolve the linked entity id in the merged IPL summary.
4. Compute `local_matrix = inverse(anchor_matrix) * placement_matrix`.
   - if the active placement set resolves to one linked entity, use that exact linked transform
5. Verify the localized transform is stable across equivalent visible clusters.
6. Inspect whether the wrong result comes from:
   - bad anchor resolution
   - wrong multiplication order
   - wrong placement chosen as base
   - wrong fragment variant being selected
7. If the transform math is correct, inspect resource selection instead of changing anchor logic.
8. Add or update regression tests before finalizing.

Validation:
- localized pivot aligns with linked IPL/GAME.dat transform
- equivalent visible clusters reduce to the same localized transform signature
- primary export still survives even if alternates disagree, unless the primary path itself is broken
- exported mesh is finite and correctly oriented
- no unrelated models regress in streamed tests

Do not:
- “fix” the issue by using raw WRLD placement
- change world-named and interior-named anchor rules without proving necessity
