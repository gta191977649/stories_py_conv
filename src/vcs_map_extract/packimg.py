from __future__ import annotations

from pathlib import Path

from .constants import ARCHIVE_ORDER
from .img_io import write_ver2_img


def collect_pack_files(output_root: Path) -> tuple[list[tuple[str, bytes]], list[str]]:
    packed: dict[str, bytes] = {}
    conflicts: list[str] = []
    for archive_name in ARCHIVE_ORDER:
        archive_dir = output_root / archive_name
        if not archive_dir.exists():
            continue
        for path in sorted(archive_dir.glob("*")):
            if path.suffix.lower() not in {".dff", ".txd", ".col"}:
                continue
            key = path.name.lower()
            data = path.read_bytes()
            if key in packed:
                if packed[key] != data:
                    conflicts.append(f"{key}: kept earlier archive version, skipped {archive_name}")
                continue
            packed[key] = data

    knackers = output_root / "knackers.txd"
    if knackers.exists():
        key = knackers.name.lower()
        data = knackers.read_bytes()
        if key in packed and packed[key] != data:
            conflicts.append(f"{key}: kept earlier archive version, skipped root knackers.txd")
        else:
            packed[key] = data
    return sorted(packed.items()), conflicts


def write_packed_img(output_root: Path) -> tuple[Path, list[str]]:
    files, conflicts = collect_pack_files(output_root)
    output_path = output_root / "vcs_map.img"
    write_ver2_img(output_path, files)
    return output_path, conflicts
