# vcs-map-extract

Python CLI for extracting GTA VCS PS2 static/map assets into GTA RW-compatible outputs.

## Install

```bash
cd /Users/nurupo/Desktop/dev/stories_py_conv
python3 -m pip install -e .
```

## Run

Input can be either:

- the game root directory
- an IMG sidecar `.DIR` file such as `MOCAPPS2.DIR`

Examples:

```bash
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out
```

```bash
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME/MOCAPPS2.DIR /tmp/vcs_out
```

```bash
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --export --buildimg
```

If you do not want to install the console script first:

```bash
cd /Users/nurupo/Desktop/dev/stories_py_conv
PYTHONPATH=src python3 -m vcs_map_extract.cli /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --export --buildimg
```

Or run directly from the checkout root:

```bash
cd /Users/nurupo/Desktop/dev/stories_py_conv
./.venv/bin/python main.py /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --export --buildimg
```

## Arguments

CLI shape:

```text
vcs-map-extract INPUT OUTPUT [--clean] [--export] [--buildimg] [--decode-dat] [--dxt-level {1,2,3,4,5}]
```

Positional arguments:

- `INPUT`
  - game root directory such as `/Users/nurupo/Desktop/ps2/GAME`
  - or an IMG sidecar `.DIR` file such as `MOCAPPS2.DIR`
- `OUTPUT`
  - destination directory for generated files

Options:

- `--clean`
  - remove everything under `OUTPUT` before running any other action
- `--export`
  - export models, textures, and collisions into `OUTPUT`
  - this is the flag that actually performs asset extraction
- `--buildimg`
  - pack generated `.dff/.txd/.col` outputs into `OUTPUT/vcs_map.img`
  - also rebuild `OUTPUT/GTA3PS2.img` from the exported `OUTPUT/GTA3PS2/` files
  - requires `--export`
- `--decode-dat`
  - decode `GAME.dat` into generated IDE/IPL text files under `OUTPUT/data/`
  - can be used by itself or together with `--export`
- `--dxt-level {1,2,3,4,5}`
  - write exported `.txd` files using DXT compression instead of uncompressed rasters
  - applies to standard archive TXDs, streamed archive TXDs, and the final root `knackers.txd`
  - when omitted, TXDs are written uncompressed as before
  - mapping:
    - `1` -> DXT1
    - `2` -> DXT2
    - `3` -> DXT3
    - `4` -> DXT4
    - `5` -> DXT5
  - example: `--dxt-level 3` writes DXT3-compressed TXDs

Common command lines:

```bash
# clean the output directory only
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --clean

# export models/textures/collisions
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --export

# export TXDs as DXT3
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --export --dxt-level 3

# clean, then export
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --clean --export

# export and build vcs_map.img plus GTA3PS2.img
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --export --buildimg

# decode GAME.dat only
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --decode-dat

# decode GAME.dat and export assets in one run
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --decode-dat --export

# export from a DIR sidecar path
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME/MOCAPPS2.DIR /tmp/vcs_out --export
```

Important CLI rules:

- At least one action must be selected: `--clean`, `--export`, or `--decode-dat`.
- `--buildimg` cannot be used alone; it must be combined with `--export`.
- `--dxt-level` is only meaningful when `--export` is also used, because TXDs are only written during export.

## Output

The tool writes:

- `OUTPUT/GTA3PS2/`
- `OUTPUT/MOCAPPS2/`
- `OUTPUT/BEACH/`
- `OUTPUT/MAINLA/`
- `OUTPUT/MALL/`
- `OUTPUT/knackers.txd`
- `OUTPUT/report.txt`
- `OUTPUT/vcs_map.img` when `--buildimg` is used
- `OUTPUT/GTA3PS2.img` when `--buildimg` is used

## Streamed Archive Structure

The standard archives are conventional GTA-style IMG archives:

```text
GTA3PS2.IMG
MOCAPPS2.IMG
└── named entries
    ├── *.mdl                  (model)
    ├── *.tex / *.xtx / *.chk  (texture)
    ├── *.anim                 (animation)
    ├── *.cut                  (cutscene definition file)
    ├── *.cam                  (camera motion file for cutscene)
    └── *.col2                 (collision file)
```

Important: `MOCAPPS2.IMG` is not a pure map archive. It contains non-static assets such as cutscene, animation, and related story resources. This tool does not try to convert those. For `MOCAPPS2.IMG`, the current scope is still only static/map-style resources that can be exported as:

```text
*.mdl  -> .dff
*.tex / *.xtx / *.chk -> .txd
*.col2 -> .col
```

Other entries in `MOCAPPS2.IMG`, including cutscene and animation files, are scanned but skipped.

The streamed map archives are different. `BEACH.IMG`, `MAINLA.IMG`, and `MALL.IMG` are not flat file archives with named model files. They work as an `LVZ + IMG` pair:

```text
BEACH.LVZ / MAINLA.LVZ / MALL.LVZ
├── DLRW relocatable level chunk
│   ├── level metadata
│   ├── global resource table
│   │   ├── shared model/resource entries
│   │   └── shared texture/resource entries
│   ├── sector header table
│   │   └── WRLD chunk headers
│   │       └── each header points into *.IMG via globalTab
│   ├── level swap table
│   │   └── sector visibility / timed swap definitions
│   ├── interior swap table
│   │   └── maps interior sectors to sector ids and swap states
│   └── area table
│       └── AREA chunk records
│           └── each area record points into *.IMG via fileOffset
│
BEACH.IMG / MAINLA.IMG / MALL.IMG
├── WRLD sector chunk bodies
│   ├── sector overlay resource table
│   │   └── raw resource blobs used by that sector
│   ├── passes[...]
│   │   └── sGeomInstance[0x50-byte records]
│   │       ├── id        -> building/world instance id
│   │       ├── resId     -> streamed resource id used by that instance
│   │       └── matrix    -> placement transform
│   └── swaps / other sector data
└── AREA chunk bodies
    └── area-local resource table
        └── patches additional resources into the shared level resource table
```

For the real VCS dump used during development, the streamed archives contain substantial non-world data:

```text
BEACH.LVZ
├── numWorldSectors = 623
├── numInteriors    = 59
└── numAreas        = 63

MAINLA.LVZ
├── numWorldSectors = 601
├── numInteriors    = 70
└── numAreas        = 75

MALL.LVZ
├── numWorldSectors = 1152
├── numInteriors    = 0
└── numAreas        = 7
```

That is why a sector-only pass misses large amounts of geometry in `BEACH` and `MAINLA`.

### Where models are

For streamed archives, model geometry can come from three different resource sources:

```text
1. sector overlay resources
LVZ sector header
-> IMG WRLD chunk body
-> sector passes[]
-> sGeomInstance.resId
-> sector overlay resource blob
-> streamed MDL-style geometry

2. shared LVZ resources
LVZ global resource table
-> resource id
-> shared geometry blob

3. area-patched resources
LVZ area table
-> IMG AREA chunk
-> area-local resource table
-> patched level resource id
-> streamed geometry blob
```

The current extractor resolves geometry in this order:

```text
area-patched resource table
-> LVZ master resource table
-> sector overlay resources
```

Important: sector overlay resources are not always one clean blob per `resId`. The same `resId` can appear in several sectors with different overlay blob variants. The extractor keeps multiple variants for the same `resId` and tries them in descending usefulness, because the first-seen overlay blob is often truncated for neon/light resources.

That is why these archives cannot be treated like `GTA3PS2.IMG`, and why loading only one sector overlay blob per resource id is incomplete.

### Where textures are

Streamed textures can also come from the same three resource sources:

- area-patched resources from `AREA` chunks
- shared/global resources in the `*.LVZ` master resource table
- sector-local resource blobs in `*.IMG`

In practice:

```text
area texture path:
LVZ area table
-> IMG AREA chunk
-> area resource table
-> patched resource id
-> texture blob

shared texture path:
LVZ master resource table
-> resource id
-> texture blob

sector-local texture path:
IMG WRLD chunk body
-> sector overlay resource
-> texture blob
```

As with geometry, a streamed texture resource can have more than one sector-local overlay variant for the same `resId`. The extractor tries all candidates until one decodes successfully.

For 4-bit streamed textures, decode scoring also tries both nibble orders and swapped width/height header variants when needed. Some streamed blobs are larger than the actual raster payload, so the decoder now uses the header's local raster offset plus the minimal raster block size instead of assuming the palette sits at the end of the whole resource blob. Some real assets also carry all-zero CLUT alpha bytes even though they are meant to be opaque, so the decoder treats that palette case as no-alpha instead of full transparency. This is required for assets such as `BEACH` `res_id=129`, `BEACH` `res_id=1882`, `MAINLA` `res_id=448`, and `MAINLA` `res_id=5291`.

### How naming works

Streamed archives do not store clean filenames for buildings. The tool resolves names like this:

```text
sGeomInstance.id & 0x7FFF
-> world/building id
-> vcs_links.inc
   ├── linked entity id   (building/treadable/dummy pool slot)
   └── IDE model id
-> inputdir/ide/*.ide
-> final model name / txd name
```

This is why the LVZ/IMG pair must be processed together with the IDEs.

Important: `vcs_links.inc` is not just a naming table. Its second field is the exact runtime entity id used by the original Leeds tools and engine-side linkage:

```text
world id
-> linked entity id
   ├── upper 16 bits: entity pool
   │   ├── 0 = building
   │   ├── 1 = treadable
   │   └── 2 = dummy
   └── lower 16 bits: slot inside that pool
-> model id
```

That linked entity id is the authoritative anchor for transform reconstruction. Treating it as "just another model reference" causes bad pivots, bad 2DFX placement, and wrong headings for some streamed models.

Important: the extracted `ipl/*.ipl` files in this dump do not expose streamed interiors. In the real test data:

```text
Buildings.ipl: Interior = 0 for every inst row
Dummys.ipl:    Interior = 0 for every inst row
```

So interior handling for this tool is driven primarily by LVZ `sInteriorSwap` and area records, not by the extracted IPLs.

### Export flow

The current streamed exporter follows this runtime path:

```text
LoadLevel
-> load all world sectors
-> load all interior sectors from sInteriorSwap.sectorId
-> load all AREA chunks
-> patch area resources into the shared level resource table
-> walk sGeomInstance rows across all passes
   ├── id    -> vcs_links.inc -> IDE name / TXD name
   └── resId -> resolve resource from:
               ├── area-patched table
               ├── LVZ master resource table
               └── sector overlay resources
-> merge all decoded fragments for one IDE model name
-> write .dff / .col / .txd
```

The exporter also keeps multiple resource ids per model name. It does not reduce a streamed model to one exemplar `resId`, because many VCS world models are split across several streamed fragments.

Interior and swap sectors are loaded as part of the same graph, but output filenames stay stable:

```text
IDE model name
-> merged world fragments
-> merged interior fragments
-> merged area-backed fragments
-> one output model file under the archive folder
```

### Transform model

This is the most important reverse-engineering detail in the repo.

`WRLD` instance records are not equivalent to final GTA-style `inst` rows. They are streamed fragment placements. The canonical object transform still lives in the linked entity pools from `GAME.dat`.

#### Matrix layout

`sGeomInstance.matrix` is read as 16 floats with RenderWare-style basis vectors:

```text
right.x right.y right.z rightw
up.x    up.y    up.z    upw
at.x    at.y    at.z    atw
pos.x   pos.y   pos.z   posw
```

For valid visible world fragments, the useful parts are:

- `right/up/at` = local basis vectors, often including baked scale
- `pos` = translation
- `rightw/upw/atw` are expected to be `0`
- `posw` is expected to be `1`

The streamed geometry decoder keeps vertices in their decoded local blob space. Placement and localization happen later via these matrices.

#### Canonical anchor

When a streamed model has a `vcs_links.inc` entry, the correct anchor is:

```text
world id
-> vcs_links.inc
-> linked entity id
-> GAME.dat building/treadable/dummy pool matrix
```

That pool/entity matrix is the same anchor used by the original `storiesview` / `storiesconv` tools. It is more accurate than:

- nearest-by-name IPL matching
- first visible streamed placement
- sector origin plus WRLD matrix alone

#### DFF localization rule

For streamed DFF export, model-local coordinates should be built like this:

```text
local fragment matrix
= inverse(linked entity matrix) * streamed placement matrix
```

In words:

- the linked entity matrix defines the canonical GTA object transform
- the WRLD matrix defines where one streamed fragment sits relative to that object
- the exported DFF should keep only the residual local transform

If the exact linked entity transform is missing, the current fallback is:

```text
nearest exact IPL/model transform from GAME.dat/IPL summary
-> else best non-degenerate streamed placement
-> else identity
```

This exact-anchor rule fixed the pivot/2DFX regression where interior lights were ending up outside the room after export.

#### IPL generation rule

Generated `wrld_stream.ipl` rows should use the same canonical anchor as DFF export:

```text
linked entity transform from GAME.dat
-> write IPL inst row
```

Do not write those rows directly from the raw WRLD absolute matrix when an exact linked entity transform exists. Doing that makes the placement file disagree with the localized DFF, so models appear translated even if the mesh itself was exported correctly.

#### Quaternion convention

The IPL quaternion convention in this repo must match the matrix builder used later by the streamed exporter.

The practical rule is:

```text
matrix -> quaternion
use (x, y, z, w)
do not negate xyz
```

Negating `x/y/z` makes some objects appear to have the correct position but the wrong heading. For instance `JM_marinex` was the sanity-check model that exposed this issue: the old quaternion sign convention reconstructed a rotated anchor, while the corrected convention reduced the residual local transform to scale-only as expected.

#### Sanity checks for developers

If a streamed model still looks wrong, check these in order:

1. Is the model linked through `vcs_links.inc`?
2. Does the linked entity id resolve to a real `GAME.dat` pool transform?
3. Does `inverse(anchor) * placement` leave a plausible local matrix?
4. Does the generated IPL row use the same anchor transform as the DFF export?
5. Do quaternion -> matrix -> localization paths all agree on handedness/sign?

For a healthy export, the residual local matrix for a correctly linked fragment should usually look like:

- mostly diagonal scale, or
- a small fixed local offset/rotation shared across every fragment of that model

It should not look like a second large world rotation layered on top of the anchor.

### Standard vs streamed duplicates

Some IDE model names exist in both:

- the normal `GAME.dat` entity pools / base IPLs
- streamed `WRLD` resources

That does not always mean the streamed copy is the canonical world placement. In practice:

- base pool/entity transforms are the authoritative placement anchor
- streamed resources often provide fragment geometry for that same object
- interior/swap-sector traversals can surface additional copies of the same model name

So developers should avoid assuming:

```text
same model name
-> same placement source
-> safe to merge blindly
```

Name equality is not enough. Always check the linked entity id and source kind.

### Streamed decoder behavior

The streamed decoder does not rely on one resource layout. It currently tries these geometry paths:

```text
1. streamed building-geometry header
   -> explicit mesh descriptors
   -> per-mesh DMA/VIF packets

2. flat MDL-style geometry blob
   -> shared material list
   -> mesh groups

3. wrapped embedded UNPACK stream
   -> find a VIF payload inside a larger resource blob

4. position-only transparent/light fallback
   -> build triangle strips from position data only
   -> used for simple neon/light resources with no full material table
```

That last fallback is important for VCS signage, neon, and light-strip resources. Many of those blobs are tiny transparent-pass resources rather than full building meshes.

### Fragment filtering during export

Some streamed models contain one bad fragment mixed into otherwise valid geometry. The exporter now validates each transformed fragment before merge:

```text
decoded fragment
-> apply placement matrix
-> reject NaN / Inf / extreme coordinate fragments
-> keep valid fragments
-> export salvaged model if enough geometry remains
```

This prevents one corrupt streamed fragment from causing the whole model export to fail.

## Notes

- The `.DIR` path is treated as the directory file for an IMG, not as a folder path.
- `report.txt` now includes streamed-source diagnostics:
  - world/interior/area load counts
  - area/master/overlay resource counts
  - no-link / no-resource / decode-failure counts
  - models recovered only from area or interior data
  - `skipped_bad_fragments` and `salvaged_models`
  - swap/default-visible conflict notes
  - unresolved `hash_*` naming fallbacks
  - missing-model breakdown by IDE file
- Standard `MDL` / `TEX` / `COL2` conversion now runs in pure Python using BLeeds parsing logic plus DragonFF RW writers.
- The required reference data is bundled in this repo:
  - `vcs_links.inc`
  - `vcsnames.inc`
  - `bruteforcedvcsnames.inc`
- The required pure-Python helper code is also vendored in this repo, so the tool no longer depends on sibling checkouts of `g3DTZ`, `librwgta`, `BLeeds`, or `DragonFF`.
- `BEACH`, `MAINLA`, and `MALL` now use a pure-Python streamed exporter with world, interior, and area-resource loading.
- Streamed neon/light resources are now handled as best-effort geometry too, including small wrapped transparent-pass blobs that only expose position strips.
- Streamed 4-bit texture decode now handles low/high nibble variants, swapped header dimensions, local raster offsets inside oversized resource blobs, minimal raster block sizing, and all-zero palette-alpha no-alpha cases, which fixes previously empty or corrupted exports such as `beach_129`, `beach_1882`, `mainla_448`, and `mainla_5291`.
- Opaque exported TXD textures are written as `565` instead of always `8888`, so exported raster metadata matches the decoded texture content more closely.
- Streamed transform reconstruction now uses exact linked entity anchors from `GAME.dat` when available, both for DFF localization and generated streamed IPL rows.
- The IPL quaternion conversion now matches the streamed exporter matrix convention; this fixed wrong headings for some anchored models such as `JM_marinex`.
- Coverage is still best-effort: some streamed resources are malformed, unresolved, or too extreme for DragonFF mesh/collision writers, and those cases are recorded in `report.txt` instead of aborting the run.


# Issues (TODO)

* No Vertex color is ripped on model.
* Incorrect TOBJ
* Some interior/swap resources still need better classification and dedupe rules.
* More documentation is still needed for malformed swap-sector placements and non-geometry resource classes.
