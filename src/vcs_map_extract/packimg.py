from __future__ import annotations

from pathlib import Path
from typing import Callable

from .constants import ARCHIVE_ORDER
from .img_io import write_ver2_img


_LOG_SINK: Callable[[str], None] | None = None


def set_log_sink(sink: Callable[[str], None] | None) -> Callable[[str], None] | None:
    global _LOG_SINK
    previous = _LOG_SINK
    _LOG_SINK = sink
    return previous


def _log(message: str) -> None:
    if _LOG_SINK is not None:
        _LOG_SINK(message)
        return
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
            conflict = f"{key}: replaced earlier archive version with root knackers.txd"
            conflicts.append(conflict)
            _log(f"[buildimg] conflict {conflict}")
        packed[key] = data
        _log("[buildimg] added root knackers.txd")
    return sorted(packed.items()), conflicts


def collect_archive_pack_files(output_root: Path, archive_name: str) -> list[tuple[str, bytes]]:
    archive_dir = output_root / archive_name
    packed: list[tuple[str, bytes]] = []
    if not archive_dir.exists():
        return packed
    for path in sorted(archive_dir.glob("*")):
        if path.suffix.lower() not in {".dff", ".txd", ".col"}:
            continue
        packed.append((path.name.lower(), path.read_bytes()))
    _log(f"[buildimg] scan {archive_name}: scanned={len(packed)}, added={len(packed)}, total={len(packed)}")
    return packed


def write_packed_img(output_root: Path) -> tuple[list[Path], list[str]]:
    files, conflicts = collect_pack_files(output_root)
    output_paths: list[Path] = []

    output_path = output_root / "vcs_map.img"
    _log(f"[buildimg] writing {len(files)} files to {output_path}")
    write_ver2_img(output_path, files)
    _log(f"[buildimg] wrote {output_path} ({len(files)} entries, conflicts={len(conflicts)})")
    output_paths.append(output_path)

    gta3ps2_files = collect_archive_pack_files(output_root, "GTA3PS2")
    gta3ps2_output_path = output_root / "GTA3PS2.img"
    _log(f"[buildimg] writing {len(gta3ps2_files)} files to {gta3ps2_output_path}")
    write_ver2_img(gta3ps2_output_path, gta3ps2_files)
    _log(f"[buildimg] wrote {gta3ps2_output_path} ({len(gta3ps2_files)} entries)")
    output_paths.append(gta3ps2_output_path)

    return output_paths, conflicts
