from __future__ import annotations

from pathlib import Path


ARCHIVE_ORDER = ("GTA3PS2", "BEACH", "MAINLA", "MALL", "MOCAPPS2")
STREAMED_ARCHIVES = ("BEACH", "MAINLA", "MALL")
STANDARD_ARCHIVES = ("GTA3PS2", "MOCAPPS2")

EXPECTED_FILES = {
    "GTA3PS2": ("GTA3PS2.IMG", "GTA3PS2.DIR"),
    "MOCAPPS2": ("MOCAPPS2.IMG", "MOCAPPS2.DIR"),
    "BEACH": ("BEACH.IMG", "BEACH.LVZ"),
    "MAINLA": ("MAINLA.IMG", "MAINLA.LVZ"),
    "MALL": ("MALL.IMG", "MALL.LVZ"),
}

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REFERENCE_ROOTS = {
    "g3dtz": REPO_ROOT.parent / "g3DTZ-master",
    "librwgta": REPO_ROOT.parent / "librwgta-master",
    "dragonff": REPO_ROOT.parent / "DragonFF-master",
    "bleeds": REPO_ROOT.parent / "BLeeds-master",
}
