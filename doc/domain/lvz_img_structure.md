# LVZ + IMG Structure

Streamed archives:
- `BEACH`, `MAINLA`, and `MALL` are LVZ+IMG pairs.
- LVZ stores level metadata and pointer tables.
- IMG stores sector bodies and area chunk payloads.

LVZ contains:
- global/master resource table
- sector header table
- level swap table
- interior swap table
- area table

IMG contains:
- WRLD sector bodies
- sector overlay resource tables
- pass-based geometry instance records
- AREA chunk bodies with area-local resource tables

Operational consequences:
- You cannot recover streamed models by filename.
- Sector traversal alone is incomplete when areas and interiors exist.
- AREA data patches the shared resource space and must be loaded.
- The same `res_id` can exist in multiple storage layers with multiple variants.

Current repository model:
- `LevelChunk.read_master_resource_pointers()` reads master pointers from LVZ.
- `parse_area_resource_table()` reads area-local resource blobs from IMG.
- `LevelChunk.parse_sector()` reads sector overlay resources and instance passes from IMG.

Do not assume:
- one resource blob per `res_id`
- one sector per model
- one archive record per final exported model
