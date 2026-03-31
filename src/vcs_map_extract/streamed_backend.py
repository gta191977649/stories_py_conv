from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import ReportData, StreamedArchivePlan
from .pure_backend import DecodedTexture, MeshData, write_col_from_mesh, write_dff_from_mesh, write_txd_from_decoded_textures
from .utils import sanitize_filename


UNPACK = 0x6C018000
STMASK = 0x20000000
STROW = 0x30000000
MSCAL = 0x14000006
WRLD_IDENT = 0x57524C44
LEVEL_BODY_OFFSET = 0x20
SECLIST_END_INDEX = 8
SECTOR_HEADER_PTR_OFFSET = LEVEL_BODY_OFFSET + 0x04
SECTOR_HEADER_STRUCT = struct.Struct("<IIIIIIIHH")


def _log(message: str) -> None:
    print(message, flush=True)


def read_u32(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def read_u16(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<H", blob, offset)[0]


def read_i16(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<h", blob, offset)[0]


def align_up4(offset: int) -> int:
    return (offset + 3) & ~3


def align_down4(offset: int) -> int:
    return offset & ~3


def half_to_float(value: int) -> float:
    return float(np.frombuffer(struct.pack("<H", value), dtype=np.float16)[0])


def maybe_decompress(blob: bytes) -> bytes:
    if not blob:
        return blob
    if len(blob) >= 2 and blob[0] == 0x78 and blob[1] in (0x01, 0x9C, 0xDA):
        try:
            return zlib.decompress(blob)
        except zlib.error:
            pass
    for wbits in (16 + zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return zlib.decompress(blob, wbits)
        except zlib.error:
            continue
    return blob


@dataclass(slots=True)
class MDLMaterial:
    texture_id: int
    tri_strip_size: int
    u_scale: float
    v_scale: float


@dataclass(slots=True)
class TriStrip:
    count: int
    verts: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    material_res_index: int = -1


@dataclass(slots=True)
class StripGroup:
    strips: list[TriStrip]
    start_off: int
    end_off: int


@dataclass(slots=True)
class ParsedStreamedGeometry:
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]
    uvs: list[tuple[float, float]]
    face_texture_res_ids: list[int]


class LVZArchive:
    def __init__(self, archive_name: str, lvz_path: Path, img_path: Path) -> None:
        self.archive_name = archive_name
        self.lvz_path = lvz_path
        self.img_path = img_path
        self.data = maybe_decompress(lvz_path.read_bytes())
        self.img_bytes = img_path.read_bytes()
        self.rows = self._walk_master_resource_table()
        self.row_by_index = {row["index"]: row for row in self.rows}
        self.overlay_raw_by_res_id = self._load_sector_overlay_resources()
        self.texture_by_res_id = self._decode_textures()
        self.overlay_texture_by_res_id: dict[int, DecodedTexture] = {}
        self.geometry_cache: dict[int, ParsedStreamedGeometry | None] = {}

    def _ptr_to_body_offset(self, raw_ptr: int) -> int:
        return raw_ptr - LEVEL_BODY_OFFSET

    def _iter_sector_headers(self):
        header_table = read_u32(self.data, SECTOR_HEADER_PTR_OFFSET)
        for index in range(4096):
            off = header_table + (index * SECTOR_HEADER_STRUCT.size)
            if off + SECTOR_HEADER_STRUCT.size > len(self.data):
                break
            ident, shrink, file_size, data_size, reloc_tab, num_relocs, global_tab, num_classes, num_funcs = SECTOR_HEADER_STRUCT.unpack_from(self.data, off)
            if ident != WRLD_IDENT:
                break
            yield {
                "index": index,
                "file_size": file_size,
                "global_tab": global_tab,
            }

    def _resource_slices(self, body: bytes, pointer_offsets: list[int]) -> dict[int, bytes]:
        if len(body) < 8:
            return {}
        resources_ptr = self._ptr_to_body_offset(read_u32(body, 0))
        num_resources = read_u16(body, 4)
        if not (0 <= resources_ptr < len(body)):
            return {}
        count = min(num_resources, max(0, (len(body) - resources_ptr) // 8))
        entries: list[tuple[int, int]] = []
        for index in range(count):
            off = resources_ptr + (index * 8)
            if off + 8 > len(body):
                break
            res_id = read_u32(body, off)
            data_ptr = self._ptr_to_body_offset(read_u32(body, off + 4))
            if 0 <= data_ptr < len(body):
                entries.append((res_id, data_ptr))
        boundaries = sorted({len(body), resources_ptr, *[ptr for ptr in pointer_offsets if 0 <= ptr <= len(body)], *[data_ptr for _, data_ptr in entries]})
        slices: dict[int, bytes] = {}
        for res_id, data_ptr in entries:
            next_boundary = next((value for value in boundaries if value > data_ptr), len(body))
            if next_boundary > data_ptr:
                slices.setdefault(res_id, body[data_ptr:next_boundary])
        return slices

    def _load_sector_overlay_resources(self) -> dict[int, bytes]:
        resources: dict[int, bytes] = {}
        for sector in self._iter_sector_headers():
            start = sector["global_tab"]
            end = start + max(0, sector["file_size"] - LEVEL_BODY_OFFSET)
            body = self.img_bytes[start:end]
            if len(body) < 48:
                continue
            passes_base = 0x08
            pointer_offsets = [
                self._ptr_to_body_offset(read_u32(body, 0)),
                *(
                    self._ptr_to_body_offset(read_u32(body, passes_base + (index * 4)))
                    for index in range(SECLIST_END_INDEX + 1)
                ),
                self._ptr_to_body_offset(read_u32(body, 48)),
            ]
            for res_id, blob in self._resource_slices(body, pointer_offsets).items():
                resources.setdefault(res_id, blob)
        return resources

    def _parse_master_header(self) -> tuple[int, int]:
        if len(self.data) < 0x24:
            raise ValueError(f"{self.lvz_path} is too small to be a valid LVZ")
        return read_u32(self.data, 0x20), self._parse_resource_count()

    def _parse_resource_count(self) -> int:
        cursor = 0x24
        n = len(self.data)
        while cursor + 8 <= n:
            addr = read_u32(self.data, cursor)
            reserved = read_u32(self.data, cursor + 4)
            plausible = 0 < addr < n and (addr & 0x3) == 0 and (reserved == 0 or (reserved & 0xFFFF) == 0)
            if not plausible:
                break
            cursor += 8
        return read_u32(self.data, cursor) & 0xFFFF if cursor + 4 <= n else 0

    def _classify_entry_peek(self, res_addr: int) -> tuple[str, dict[str, int]]:
        if res_addr <= 0 or res_addr + 8 > len(self.data):
            return "INVALID", {}
        a16 = read_u16(self.data, res_addr)
        a32 = read_u32(self.data, res_addr)
        b32 = read_u32(self.data, res_addr + 4)
        if a16 == 0:
            return "UNK_FAC0", {}
        if a16 <= 100:
            return "MDL", {}
        return "TEX_REF", {"ref_addr": a32, "embedded_res_id": b32}

    def _walk_master_resource_table(self) -> list[dict[str, int]]:
        res_table_addr, res_count = self._parse_master_header()
        rows: list[dict[str, int]] = []
        for index in range(res_count):
            off = res_table_addr + (index * 8)
            if off + 8 > len(self.data):
                break
            res_addr = read_u32(self.data, off)
            kind, info = self._classify_entry_peek(res_addr)
            rows.append(
                {
                    "index": index,
                    "res_addr": res_addr,
                    "kind": kind,
                    "ref_addr": info.get("ref_addr", 0),
                    "embedded_res_id": info.get("embedded_res_id", 0),
                }
            )
        return rows

    def _decode_textures(self) -> dict[int, DecodedTexture]:
        tex_rows = [row for row in self.rows if row["kind"] == "TEX_REF" and row.get("ref_addr", 0) > 0]
        tex_rows.sort(key=lambda row: row["ref_addr"])
        decoded: dict[int, DecodedTexture] = {}
        last_ref = None
        unique_rows = []
        for row in tex_rows:
            if row["ref_addr"] != last_ref:
                unique_rows.append(row)
                last_ref = row["ref_addr"]
        for index, row in enumerate(unique_rows):
            start = row["ref_addr"]
            if start <= 0 or start >= len(self.data):
                continue
            end = unique_rows[index + 1]["ref_addr"] if index + 1 < len(unique_rows) else len(self.data)
            end = min(end, len(self.data))
            if end <= start or end - start < 64:
                continue
            texture = self._decode_texture_blob(self.data[start:end], row["index"])
            if texture is not None:
                decoded[row["index"]] = texture
        return decoded

    def _decode_texture_blob(self, blob: bytes, res_id: int) -> DecodedTexture | None:
        if len(blob) < 64:
            return None
        try:
            palette = np.frombuffer(blob[-64:], dtype=np.uint8).reshape(16, 4).copy()
        except ValueError:
            return None
        alpha = (palette[:, 3].astype(np.uint16) * 255 + 64) // 128
        palette[:, 3] = np.clip(alpha, 0, 255).astype(np.uint8)
        index_blob = blob[:-64]
        if not index_blob:
            return None
        pixels = np.empty(len(index_blob) * 2, dtype=np.uint8)
        raw = np.frombuffer(index_blob, dtype=np.uint8)
        pixels[0::2] = raw & 0x0F
        pixels[1::2] = raw >> 4
        total_pixels = len(pixels)
        width = int(2 ** round(math.log2(max(16, int(total_pixels ** 0.5)))))
        while total_pixels % width != 0 and width > 16:
            width //= 2
        height = max(1, total_pixels // width)
        if width * height != total_pixels:
            return None
        rgba = palette[np.clip(pixels[: total_pixels], 0, 15)].reshape(height, width, 4)
        return DecodedTexture(
            name=f"{self.archive_name.lower()}_{res_id}",
            rgba=rgba.astype(np.uint8).tobytes(),
            width=width,
            height=height,
        )

    def _find_unpack_near(self, blob: bytes, offset: int, window: int = 8) -> int:
        start = max(0, offset - window)
        end = min(len(blob), offset + window + 4)
        for pos in range(start, end):
            if (pos & 3) == 0 and pos + 4 <= len(blob) and read_u32(blob, pos) == UNPACK:
                return pos
        raise ValueError(f"UNPACK header not found near 0x{offset:08X}")

    def _parse_mdl_material_list(self, blob: bytes, base: int) -> tuple[list[MDLMaterial], int]:
        count = read_u16(blob, base)
        size_bytes = read_u16(blob, base + 2)
        off = base + 4
        materials: list[MDLMaterial] = []
        limit = min(len(blob), base + 4 + size_bytes)
        for _ in range(count):
            if off + 22 > limit:
                break
            texture_id = read_u16(blob, off)
            tri_raw = read_u16(blob, off + 2)
            u_scale = half_to_float(read_u16(blob, off + 4)) or 1.0
            v_scale = half_to_float(read_u16(blob, off + 6)) or 1.0
            materials.append(MDLMaterial(texture_id, tri_raw & 0x7FFF, u_scale, v_scale))
            off += 22
        while off < len(blob) and blob[off] == 0xAA:
            off += 1
        return materials, align_down4(off)

    def _read_vec3_i16_norm(self, blob: bytes, offset: int) -> tuple[float, float, float]:
        return (
            read_i16(blob, offset + 0) / 32767.5,
            read_i16(blob, offset + 2) / 32767.5,
            read_i16(blob, offset + 4) / 32767.5,
        )

    def _read_uv(self, blob: bytes, offset: int) -> tuple[float, float]:
        return (blob[offset + 0] / 128.0, blob[offset + 1] / 128.0)

    def _parse_one_batch(self, blob: bytes, pos: int, *, first_batch: bool) -> tuple[TriStrip, int]:
        pos = self._find_unpack_near(blob, align_down4(pos))
        if pos + 20 > len(blob):
            raise ValueError("Batch header truncated")
        count_all = read_u32(blob, pos + 16) & 0x7FFF
        skip = 0 if first_batch else 2
        count = max(0, count_all - skip)
        w = pos + 20
        if read_u32(blob, w) != STMASK:
            raise ValueError("Missing STMASK before positions")
        w += 8
        if read_u32(blob, w) != STROW:
            raise ValueError("Missing STROW before positions")
        w += 20
        if (read_u32(blob, w) & 0xFF004000) != 0x79000000:
            raise ValueError("Unexpected position header")
        w += 4
        if w + (count_all * 6) > len(blob):
            raise ValueError("Position payload truncated")
        verts = [
            self._read_vec3_i16_norm(blob, w + ((index + skip) * 6))
            for index in range(count)
        ]
        w = align_up4(w + (count_all * 6))
        if read_u32(blob, w) != STMASK:
            raise ValueError("Missing STMASK before UVs")
        w += 8
        if read_u32(blob, w) != STROW:
            raise ValueError("Missing STROW before UVs")
        w += 20
        if (read_u32(blob, w) & 0xFF004000) != 0x76004000:
            raise ValueError("Unexpected UV header")
        w += 4
        if w + (count_all * 2) > len(blob):
            raise ValueError("UV payload truncated")
        uvs = [
            self._read_uv(blob, w + ((index + skip) * 2))
            for index in range(count)
        ]
        w = align_up4(w + (count_all * 2))
        if (read_u32(blob, w) & 0xFF004000) != 0x6F000000:
            raise ValueError("Unexpected prelight header")
        if w + 4 + (count_all * 2) > len(blob):
            raise ValueError("Prelight payload truncated")
        w = align_up4(w + 4 + (count_all * 2))
        if read_u32(blob, w) != MSCAL:
            if w + 4 <= len(blob) and read_u32(blob, w + 4) == MSCAL:
                w += 4
            else:
                raise ValueError("Missing MSCAL terminator")
        w += 4
        while w + 4 <= len(blob) and read_u32(blob, w) == 0:
            w += 4
        return TriStrip(count=count, verts=verts, uvs=uvs), w

    def _parse_groups(self, blob: bytes, start_off: int) -> list[StripGroup]:
        groups: list[StripGroup] = []
        try:
            off = self._find_unpack_near(blob, start_off)
        except ValueError:
            return groups
        first_batch = True
        for _ in range(4096):
            if off >= len(blob):
                break
            try:
                strip, next_off = self._parse_one_batch(blob, off, first_batch=first_batch)
            except ValueError:
                break
            groups.append(StripGroup(strips=[strip], start_off=off, end_off=next_off))
            first_batch = False
            try:
                off = self._find_unpack_near(blob, next_off)
            except ValueError:
                break
        return groups

    def _assign_materials(self, materials: list[MDLMaterial], groups: list[StripGroup]) -> None:
        if not materials or not groups:
            return
        flat = []
        start = min(group.start_off for group in groups)
        for group in groups:
            for strip in group.strips:
                flat.append((strip, group.start_off - start, group.end_off - start))
        flat.sort(key=lambda item: item[1])
        material_index = 0
        current = materials[material_index]
        window = current.tri_strip_size
        acc = 0
        for strip, rel_start, rel_end in flat:
            length = rel_end - rel_start
            while material_index < len(materials) and acc >= window and window > 0:
                material_index += 1
                if material_index < len(materials):
                    current = materials[material_index]
                    window = current.tri_strip_size
                    acc = 0
            if material_index >= len(materials):
                break
            strip.material_res_index = current.texture_id
            strip.uvs = [(u * current.u_scale, v * current.v_scale) for u, v in strip.uvs]
            acc += length

    def texture_for_res_id(self, res_id: int) -> DecodedTexture | None:
        texture = self.texture_by_res_id.get(res_id)
        if texture is not None:
            return texture
        if res_id not in self.overlay_texture_by_res_id:
            blob = self.overlay_raw_by_res_id.get(res_id)
            self.overlay_texture_by_res_id[res_id] = self._decode_texture_blob(blob, res_id) if blob else None
        return self.overlay_texture_by_res_id.get(res_id)

    def geometry_for_res_id(self, res_id: int) -> ParsedStreamedGeometry | None:
        if res_id in self.geometry_cache:
            return self.geometry_cache[res_id]
        row = self.row_by_index.get(res_id)
        if row is not None and row["kind"] == "MDL" and row["res_addr"] > 0:
            blob = self.data
            base = row["res_addr"]
        else:
            blob = self.overlay_raw_by_res_id.get(res_id)
            base = 0
        if blob is None or base >= len(blob):
            self.geometry_cache[res_id] = None
            return None
        materials, next_off = self._parse_mdl_material_list(blob, base)
        groups = self._parse_groups(blob, next_off)
        self._assign_materials(materials, groups)
        vertices: list[tuple[float, float, float]] = []
        faces: list[tuple[int, int, int]] = []
        uvs: list[tuple[float, float]] = []
        face_texture_res_ids: list[int] = []
        for group in groups:
            for strip in group.strips:
                count = min(strip.count, len(strip.verts), len(strip.uvs))
                if count < 3:
                    continue
                base = len(vertices)
                vertices.extend(strip.verts[:count])
                uvs.extend(strip.uvs[:count])
                for index in range(count - 2):
                    if index & 1:
                        face = (base + index + 1, base + index, base + index + 2)
                    else:
                        face = (base + index, base + index + 1, base + index + 2)
                    faces.append(face)
                    face_texture_res_ids.append(strip.material_res_index)
        geometry = ParsedStreamedGeometry(vertices, faces, uvs, face_texture_res_ids) if faces else None
        self.geometry_cache[res_id] = geometry
        return geometry


def matrix_inverse(values: tuple[float, ...]) -> np.ndarray:
    return np.linalg.inv(np.asarray(values, dtype=np.float64).reshape(4, 4))


def transform_point(matrix: np.ndarray, point: tuple[float, float, float]) -> tuple[float, float, float]:
    vector = np.array([point[0], point[1], point[2], 1.0], dtype=np.float64)
    out = matrix @ vector
    return (float(out[0]), float(out[1]), float(out[2]))


def merge_texture(existing: DecodedTexture, incoming: DecodedTexture) -> DecodedTexture:
    if incoming.width * incoming.height > existing.width * existing.height:
        return incoming
    return existing


def export_streamed_archive(
    archive_name: str,
    root: Path,
    output_root: Path,
    plan: StreamedArchivePlan,
    report: ReportData,
) -> dict[str, int]:
    _log(f"[streamed] load {archive_name}.LVZ")
    archive = LVZArchive(archive_name, root / f"{archive_name}.LVZ", root / f"{archive_name}.IMG")
    archive_dir = output_root / archive_name
    archive_dir.mkdir(parents=True, exist_ok=True)

    textures_by_txd: dict[str, dict[str, DecodedTexture]] = {}
    knackers_textures: dict[str, DecodedTexture] = {}
    exported_models = 0
    missing_res_ids = 0

    for model in plan.model_exports:
        if not model.placements:
            continue
        _log(
            f"[streamed] {archive_name} model {model.model_name}: "
            f"{len(model.placements)} placements"
        )
        base_inverse = matrix_inverse(model.placements[0].matrix)
        vertices: list[tuple[float, float, float]] = []
        faces: list[tuple[int, int, int]] = []
        uvs: list[tuple[float, float]] = []
        face_materials: list[str] = []

        for placement in model.placements:
            try:
                geometry = archive.geometry_for_res_id(placement.res_id)
            except Exception:
                geometry = None
            if geometry is None:
                missing_res_ids += 1
                continue
            local_matrix = base_inverse @ np.asarray(placement.matrix, dtype=np.float64).reshape(4, 4)
            base_index = len(vertices)
            vertices.extend(transform_point(local_matrix, vertex) for vertex in geometry.vertices)
            uvs.extend(geometry.uvs)
            for face_index, (a, b, c) in enumerate(geometry.faces):
                faces.append((base_index + a, base_index + b, base_index + c))
                texture_res_id = geometry.face_texture_res_ids[face_index]
                texture = archive.texture_for_res_id(texture_res_id)
                texture_name = texture.name if texture is not None else f"{archive_name.lower()}_{texture_res_id}"
                face_materials.append(texture_name)
                if texture is not None and model.txd_name:
                    txd_bucket = textures_by_txd.setdefault(model.txd_name, {})
                    existing = txd_bucket.get(texture_name)
                    txd_bucket[texture_name] = merge_texture(existing, texture) if existing else texture
                    if model.txd_name.lower() == "knackers":
                        existing_knackers = knackers_textures.get(texture_name)
                        if existing_knackers is not None and existing_knackers.rgba != texture.rgba:
                            report.knackers_texture_conflicts.append(
                                f"{archive_name}: texture '{texture_name}' had conflicting data; kept higher-resolution copy"
                            )
                        knackers_textures[texture_name] = merge_texture(existing_knackers, texture) if existing_knackers else texture

        if faces:
            mesh = MeshData(vertices=vertices, faces=faces, uvs=uvs, face_materials=face_materials)
            stem = sanitize_filename(model.model_name)
            write_dff_from_mesh(mesh, archive_dir / f"{stem}.dff", stem)
            write_col_from_mesh(mesh, archive_dir / f"{stem}.col", model_id=model.placements[0].res_id)
            exported_models += 1
            _log(f"[streamed] wrote {archive_name}/{stem}.dff + .col")

    for txd_name, textures in textures_by_txd.items():
        if textures:
            write_txd_from_decoded_textures(archive_dir / f"{sanitize_filename(txd_name)}.txd", list(textures.values()))
            _log(f"[streamed] wrote {archive_name}/{sanitize_filename(txd_name)}.txd")

    if knackers_textures:
        write_txd_from_decoded_textures(output_root / "knackers.txd", list(knackers_textures.values()))
        _log("[streamed] updated knackers.txd")

    return {
        "exported_models": exported_models,
        "missing_res_ids": missing_res_ids,
        "exported_txds": sum(1 for textures in textures_by_txd.values() if textures),
        "decoded_textures": len(archive.texture_by_res_id),
    }
