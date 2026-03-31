from __future__ import annotations

import re
import zlib
from pathlib import Path


def normalize_input_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file():
        return candidate.parent
    return candidate


def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def find_sibling_case_insensitive(path: Path, suffix: str) -> Path:
    direct = path.with_suffix(suffix)
    if direct.exists():
        return direct
    target_name = f"{path.stem}{suffix}".lower()
    for candidate in path.parent.iterdir():
        if candidate.name.lower() == target_name:
            return candidate
    return direct


def uppercase_crc32(name: str) -> int:
    return zlib.crc32(name.upper().encode("ascii", "ignore")) & 0xFFFFFFFF


def is_zlib_blob(data: bytes) -> bool:
    return len(data) >= 2 and data[0] == 0x78 and data[1] in (0x01, 0x9C, 0xDA)


def maybe_decompress(data: bytes) -> bytes:
    if not data:
        return data
    if is_zlib_blob(data):
        try:
            return zlib.decompress(data)
        except zlib.error:
            pass
    for wbits in (16 + zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return zlib.decompress(data, wbits)
        except zlib.error:
            continue
    return data


def sanitize_filename(stem: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    return sanitized or "unnamed"
