from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from struct import pack, unpack, unpack_from

from .utils import find_sibling_case_insensitive


@dataclass(slots=True)
class ImgDirectoryEntry:
    offset_sectors: int
    size_sectors: int
    name: str


class ImgReader:
    def __init__(self, img_path: Path) -> None:
        self.img_path = img_path
        self.entries = self._read_directory()

    def _read_directory(self) -> list[ImgDirectoryEntry]:
        with self.img_path.open("rb") as handle:
            magic, count = unpack("4sI", handle.read(8))
            if magic == b"VER2":
                directory_blob = handle.read(count * 32)
            else:
                dir_path = find_sibling_case_insensitive(self.img_path, ".DIR")
                directory_blob = dir_path.read_bytes()
            entries: list[ImgDirectoryEntry] = []
            for offset in range(0, len(directory_blob), 32):
                sector_offset, size_sectors, raw_name = unpack_from("II24s", directory_blob, offset)
                name = raw_name.split(b"\0", 1)[0].decode("utf-8", "ignore")
                if not name:
                    continue
                entries.append(ImgDirectoryEntry(sector_offset, size_sectors, name))
            return entries

    def read_entry(self, entry: ImgDirectoryEntry) -> bytes:
        with self.img_path.open("rb") as handle:
            handle.seek(entry.offset_sectors * 2048)
            return handle.read(entry.size_sectors * 2048)


def write_ver2_img(output_path: Path, files: list[tuple[str, bytes]]) -> None:
    sector_size = 2048
    aligned_blobs: list[tuple[str, bytes, int]] = []
    directory_bytes = 8 + (len(files) * 32)
    current_sector = (directory_bytes + sector_size - 1) // sector_size
    for name, data in files:
        padding = (-len(data)) % sector_size
        aligned = data + (b"\0" * padding)
        sectors = len(aligned) // sector_size
        aligned_blobs.append((name, aligned, current_sector))
        current_sector += sectors

    directory = bytearray()
    for name, data, start_sector in aligned_blobs:
        entry_name = name.encode("utf-8", "ignore")[:24]
        entry_name = entry_name + (b"\0" * (24 - len(entry_name)))
        directory += pack("II24s", start_sector, len(data) // sector_size, entry_name)

    header = pack("4sI", b"VER2", len(aligned_blobs))
    body = bytearray(header)
    body.extend(directory)
    body.extend(b"\0" * ((-len(body)) % sector_size))
    for _, data, _ in aligned_blobs:
        body.extend(data)
    output_path.write_bytes(body)
