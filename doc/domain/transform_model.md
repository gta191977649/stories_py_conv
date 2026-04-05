# Transform Model

Primary formula:
- `local_matrix = inverse(anchor_matrix) * placement_matrix`

Anchor source:
1. `vcs_links.inc` maps streamed world id to linked entity id.
2. linked entity id resolves into the merged IPL summary.
3. merged IPL summary is built from:
   - `ipl/*.ipl`
   - `GAME.dat` generated streamed transforms

Placement source:
- streamed placement matrices come from WRLD sector instance records

Rules:
- The placement matrix is not the final DFF local transform.
- The anchor matrix is the linked entity’s world transform.
- Exported vertices are localized against the anchor before fragment merge.

Exact constraints:
- Never use raw WRLD placement as exported local space when an anchor exists.
- Never swap the multiplication order.
- Never localize against an unrelated visible placement just because it is nearby.

Interior handling:
- Interior exports may use an exact linked entity transform from the merged IPL summary.
- World-named exports also use the exact linked entity transform when the active placement set resolves to one linked entity.
- If no single linked entity can be proven for the active placement set, the exporter falls back to the chosen base placement transform.

Validation target:
- DFF pivot and localized geometry should align with the linked IPL/GAME.dat instance, not with the raw streamed sector origin.
