# Glossary

Anchor matrix:
- World transform of the linked entity resolved from merged IPL/GAME.dat data.

AREA resource:
- Resource blob injected by an AREA chunk. Highest-priority streamed resource source.

Cluster:
- A grouped set of placements/fragments believed to represent one exported model variant.

Fragment:
- One decoded geometry piece contributing to a final streamed model.

Linked entity id:
- Entity id from `vcs_links.inc` used to locate the anchor transform.

Local matrix:
- `inverse(anchor_matrix) * placement_matrix`.

LVZ master resource:
- Shared resource blob referenced by the LVZ global resource table.

Placement matrix:
- Raw transform read from a streamed WRLD sector instance.

Resource variant:
- One candidate blob for a `res_id`, with source provenance such as AREA/master/overlay.

Sector overlay resource:
- Resource blob embedded in a sector body. Lowest-priority streamed resource source.

Standard archive:
- Conventional named IMG archive such as `GTA3PS2.IMG` or `MOCAPPS2.IMG`.

Streamed archive:
- LVZ+IMG pair such as `BEACH`, `MAINLA`, or `MALL`.
