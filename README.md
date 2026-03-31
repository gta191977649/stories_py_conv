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
│   │   └── shared TEX_REF texture entries
│   ├── sector header table
│   │   └── WRLD chunk headers
│   │       └── each header points into *.IMG via globalTab
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
```

### Where models are

For streamed archives, most model geometry is reached through sector data in `*.IMG`:

```text
LVZ sector header
-> IMG WRLD chunk body
-> sector passes[]
-> sGeomInstance.resId
-> sector overlay resource blob
-> streamed MDL-style geometry
```

That is why these archives cannot be treated like `GTA3PS2.IMG`.

### Where textures are

Streamed textures are usually in one of two places:

- shared/global texture resources in the `*.LVZ` global resource table
- sector-local resource blobs in `*.IMG`

In practice:

```text
shared texture path:
LVZ global resource table
-> TEX_REF entry
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

### Export flow

The current streamed exporter follows this runtime path:

```text
LVZ sector header
-> IMG WRLD chunk
-> sGeomInstance
   ├── id    -> vcs_links.inc -> IDE name / TXD name
   └── resId -> sector overlay resource
               ├── streamed geometry blob -> .dff / .col
               └── streamed texture blob  -> .txd / knackers.txd
```

## Notes

- The `.DIR` path is treated as the directory file for an IMG, not as a folder path.
- `report.txt` lists summary counts, missing IDE models, unresolved streamed IDs, and pack conflicts.
- Standard `MDL` / `TEX` / `COL2` conversion now runs in pure Python using BLeeds parsing logic plus DragonFF RW writers.
- `BEACH`, `MAINLA`, and `MALL` now use a pure-Python streamed exporter. Coverage is still best-effort: some streamed resources are malformed or unresolved and will be skipped into `report.txt` instead of aborting the run.
