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
vcs-map-extract /Users/nurupo/Desktop/ps2/GAME/MOCAPPS2.DIR /tmp/vcs_out --packimg
```

If you do not want to install the console script first:

```bash
cd /Users/nurupo/Desktop/dev/stories_py_conv
PYTHONPATH=src python3 -m vcs_map_extract.cli /Users/nurupo/Desktop/ps2/GAME/MOCAPPS2.DIR /tmp/vcs_out --packimg
```

Or run directly from the checkout root:

```bash
cd /Users/nurupo/Desktop/dev/stories_py_conv
./.venv/bin/python main.py /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --packimg
```

## Output

The tool writes:

- `OUTPUT/GTA3PS2/`
- `OUTPUT/MOCAPPS2/`
- `OUTPUT/BEACH/`
- `OUTPUT/MAINLA/`
- `OUTPUT/MALL/`
- `OUTPUT/knackers.txd`
- `OUTPUT/report.txt`
- `OUTPUT/vcs_map.img` when `--packimg` is used

## Streamed Archive Structure

The standard archives are conventional GTA-style IMG archives:

```text
GTA3PS2.IMG
MOCAPPS2.IMG
└── named entries
    ├── *.mdl
    ├── *.tex / *.xtx / *.chk
    └── *.col2
```

Important: `MOCAPPS2.IMG` is not map archive. It contains non-static assets such as cutscene, animation, and related story resources. This tool does not try to convert those. For `MOCAPPS2.IMG`, the current scope is still only static/map-style resources that can be exported as:

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

That is why these archives cannot be treated like `GTA3PS2.IMG`, and why loading only sector overlay resources is incomplete.

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

### How naming works

Streamed archives do not store clean filenames for buildings. The tool resolves names like this:

```text
sGeomInstance.id & 0x7FFF
-> world/building id
-> vcs_links.inc
-> IDE model id
-> inputdir/ide/*.ide
-> final model name / txd name
```

This is why the LVZ/IMG pair must be processed together with the IDEs.

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

## Notes

- The `.DIR` path is treated as the directory file for an IMG, not as a folder path.
- `report.txt` now includes streamed-source diagnostics:
  - world/interior/area load counts
  - area/master/overlay resource counts
  - no-link / no-resource / decode-failure counts
  - models recovered only from area or interior data
  - swap/default-visible conflict notes
  - missing-model breakdown by IDE file
- Standard `MDL` / `TEX` / `COL2` conversion now runs in pure Python using BLeeds parsing logic plus DragonFF RW writers.
- The required reference data is bundled in this repo:
  - `vcs_links.inc`
  - `vcsnames.inc`
  - `bruteforcedvcsnames.inc`
- The required pure-Python helper code is also vendored in this repo, so the tool no longer depends on sibling checkouts of `g3DTZ`, `librwgta`, `BLeeds`, or `DragonFF`.
- `BEACH`, `MAINLA`, and `MALL` now use a pure-Python streamed exporter with world, interior, and area-resource loading.
- Coverage is still best-effort: some streamed resources are malformed, unresolved, or too extreme for DragonFF mesh/collision writers, and those cases are recorded in `report.txt` instead of aborting the run.
