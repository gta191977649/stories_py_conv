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
- Do not invent new names for unresolved streamed models.
- Do not break `knackers` texture grouping by renaming streamed models inconsistently.

Interior fallback naming:
- When no linked world model is resolved, current code uses archive/sector/res-id based fallback names.
- Those fallback names are operational, not semantic truth.
