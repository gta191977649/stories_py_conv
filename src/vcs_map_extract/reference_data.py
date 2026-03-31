from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .constants import DEFAULT_REFERENCE_ROOTS


HASH_ENTRY_RE = re.compile(r'\{\s*0x([0-9A-Fa-f]+)\s*,\s*"([^"]+)"\s*\}')
LINK_ENTRY_RE = re.compile(r"\{\s*0x([0-9A-Fa-f]+)\s*,\s*0x([0-9A-Fa-f]+)\s*,\s*(-?\d+)\s*\}")


def _load_text(path: Path) -> str:
    return path.read_text(errors="ignore")


@lru_cache(maxsize=1)
def load_vcs_name_table() -> dict[int, str]:
    root = DEFAULT_REFERENCE_ROOTS["g3dtz"] / "source" / "names"
    table: dict[int, str] = {}
    for name in ("vcsnames.inc", "bruteforcedvcsnames.inc"):
        path = root / name
        for key_hex, value in HASH_ENTRY_RE.findall(_load_text(path)):
            table[int(key_hex, 16)] = value
    return table


@lru_cache(maxsize=1)
def load_streamed_link_table() -> dict[int, int]:
    path = DEFAULT_REFERENCE_ROOTS["librwgta"] / "tools" / "storiesview" / "vcs_links.inc"
    mapping: dict[int, int] = {}
    for world_id_hex, _ipl_id_hex, model_id in LINK_ENTRY_RE.findall(_load_text(path)):
        mapping[int(world_id_hex, 16)] = int(model_id, 10)
    return mapping
