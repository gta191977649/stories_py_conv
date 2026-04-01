from __future__ import annotations

from pathlib import Path

from .constants import ARCHIVE_ORDER
from .img_io import write_ver2_img


def _log(message: str) -> None:
    print(message, flush=True)


def collect_pack_files(output_root: Path) -> tuple[list[tuple[str, bytes]], list[str]]:
    packed: dict[str, bytes] = {}
    conflicts: list[str] = []
    for archive_name in ARCHIVE_ORDER:
        archive_dir = output_root / archive_name
        if not archive_dir.exists():
            continue
        scanned = 0
        added = 0
        for path in sorted(archive_dir.glob("*")):
            if path.suffix.lower() not in {".dff", ".txd", ".col"}:
                continue
            scanned += 1
            key = path.name.lower()
            data = path.read_bytes()
            if key in packed:
                if packed[key] != data:
                    conflict = f"{key}: kept earlier archive version, skipped {archive_name}"
                    conflicts.append(conflict)
                    _log(f"[buildimg] conflict {conflict}")
                continue
            packed[key] = data
            added += 1
        _log(
            f"[buildimg] scan {archive_name}: "
            f"scanned={scanned}, added={added}, total={len(packed)}"
        )

    knackers = output_root / "knackers.txd"
    if knackers.exists():
        key = knackers.name.lower()
        data = knackers.read_bytes()
        if key in packed and packed[key] != data:
            conflict = f"{key}: kept earlier archive version, skipped root knackers.txd"
            conflicts.append(conflict)
            _log(f"[buildimg] conflict {conflict}")
        else:
            packed[key] = data
            _log("[buildimg] added root knackers.txd")
    return sorted(packed.items()), conflicts


def write_packed_img(output_root: Path) -> tuple[Path, list[str]]:
    files, conflicts = collect_pack_files(output_root)
    output_path = output_root / "vcs_map.img"
    _log(f"[buildimg] writing {len(files)} files to {output_path}")
    write_ver2_img(output_path, files)
    _log(f"[buildimg] wrote {output_path} ({len(files)} entries, conflicts={len(conflicts)})")
    return output_path, conflicts
