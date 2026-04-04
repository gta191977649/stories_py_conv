# Naming And Links

Naming sources:
- IDE catalog provides canonical names and TXD names when available.
- `GAME.dat` provides model ids, hashed fallback names, and TXD slots.
- `load_vcs_name_table()` provides known hash-name mappings.

Resolver responsibilities:
- map streamed world id to linked entity id and model id
- choose canonical model name
- choose TXD name
- preserve `GAME.dat` linkage when IDE names are absent

Link chain:
- streamed instance `world_id`
- `vcs_links.inc` lookup
- linked IPL entity id + model id
- model metadata from IDE or `GAME.dat`

Rules:
- Prefer canonical model names over synthetic names when the resolver can prove the link.
- Keep hashed fallback names as stable identifiers when no canonical name exists.
- Format hashed fallbacks as lowercase `hash_<8hex>`.
- Do not invent new names for unresolved streamed models.
- Do not break `knackers` texture grouping by renaming streamed models inconsistently.

Interior fallback naming:
- When no linked world model is resolved, the planner first tries streamed resource/blob name recovery.
- If a recoverable semantic or hash name is found, the exporter uses that name instead of an `interior_*` synthetic fallback.
- If no recoverable name exists, the unresolved plan remains diagnostic-only and is not treated as semantic truth.
