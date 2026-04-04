# Transform Rules

DO:
- compute `local_matrix = inverse(anchor_matrix) * placement_matrix`
- resolve anchors through the merged IPL summary
- validate that basis lengths are finite before choosing a base transform
- keep anchor and placement spaces explicit in variable names
- use exact linked-entity transforms where the current exporter already relies on them
- use the exact linked-entity transform for world-named streamed exports too when the active placement set has one linked entity

DO NOT:
- export streamed geometry in raw placement/world space
- use sector origin as a substitute for anchor space
- invert `placement_matrix` and multiply by `anchor_matrix`
- treat two placements as equivalent without checking localized transform signatures
- patch transform bugs by changing naming or resource logic first

Required checks:
- no NaN/Inf in localized matrices
- no degenerate basis used as primary anchor
- localized transforms across equivalent clusters must match before collapsing
