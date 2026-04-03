from __future__ import annotations

import hashlib
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .models import ReportData, StreamedArchivePlan, StreamedPlacement
from .ipl_parser import IplSummary, IplTransform
from .pure_backend import DecodedTexture, MeshData, write_col_from_mesh, write_dff_from_mesh, write_txd_from_decoded_textures
from .reference_data import load_vcs_name_table
from .streamed_world import LevelChunk, parse_area_resource_table
from .utils import sanitize_filename
from .vendor.bleeds.tex import Ps2TexHeader, decode_ps2_texture, parse_ps2_header


UNPACK = 0x6C018000
STMASK = 0x20000000
STROW = 0x30000000
MSCAL = 0x14000006
TEXTURE_NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_.-]{2,31}")


def _log(message: str) -> None:
    if _LOG_SINK is not None:
        _LOG_SINK(message)
        return
    print(message, flush=True)


_LOG_SINK: Callable[[str], None] | None = None


def set_log_sink(sink: Callable[[str], None] | None) -> Callable[[str], None] | None:
    global _LOG_SINK
    previous = _LOG_SINK
    _LOG_SINK = sink
    return previous


def read_u32(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def read_i16(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<h", blob, offset)[0]


def align_up4(offset: int) -> int:
    return (offset + 3) & ~3


def align_down4(offset: int) -> int:
    return offset & ~3


def half_to_float(value: int) -> float:
    return float(np.frombuffer(struct.pack("<H", value), dtype=np.float16)[0])


def _ps2_exact_mip_texel_bytes(width: int, height: int, bpp: int, mip_count: int) -> int:
    total = 0
    mip_levels = max(1, int(mip_count))
    w = max(1, int(width))
    h = max(1, int(height))
    for _ in range(mip_levels):
        total += (w * h * int(bpp)) // 8
        w = max(1, w // 2)
        h = max(1, h // 2)
    return total


def _ps2_palette_bytes(bpp: int) -> int:
    if bpp == 4:
        return 16 * 4
    if bpp == 8:
        return 256 * 4
    return 0


@dataclass(slots=True)
class MDLMaterial:
    texture_id: int
    tri_strip_size: int
    u_scale: float
    v_scale: float


@dataclass(slots=True)
class StreamedMeshDesc:
    packet_size_bytes: int
    texture_id: int
    u_scale: float
    v_scale: float


@dataclass(slots=True)
class TriStrip:
    count: int
    verts: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    colors: list[tuple[int, int, int, int]] | None = None
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
    vertex_colors: list[tuple[int, int, int, int]]
    face_texture_res_ids: list[int]
    resource_origin: str


def _nearly_equal(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) <= eps


def _same_vertex(
    a_pos: tuple[float, float, float],
    b_pos: tuple[float, float, float],
    a_uv: tuple[float, float] | None = None,
    b_uv: tuple[float, float] | None = None,
) -> bool:
    if not (
        _nearly_equal(a_pos[0], b_pos[0])
        and _nearly_equal(a_pos[1], b_pos[1])
        and _nearly_equal(a_pos[2], b_pos[2])
    ):
        return False
    if a_uv is None or b_uv is None:
        return True
    return _nearly_equal(a_uv[0], b_uv[0]) and _nearly_equal(a_uv[1], b_uv[1])


def _triangulate_strip_faces(
    verts: list[tuple[float, float, float]],
    *,
    base_index: int = 0,
    uvs: list[tuple[float, float]] | None = None,
) -> list[tuple[int, int, int]]:
    faces: list[tuple[int, int, int]] = []
    strip_window: list[int] = []
    winding = 0
    for index, position in enumerate(verts):
        uv = uvs[index] if uvs is not None and index < len(uvs) else None
        if strip_window:
            prev_index = strip_window[-1]
            prev_uv = uvs[prev_index] if uvs is not None and prev_index < len(uvs) else None
            if _same_vertex(verts[prev_index], position, prev_uv, uv):
                strip_window = [index]
                winding = 0
                continue

        strip_window.append(index)
        if len(strip_window) < 3:
            continue

        a = strip_window[-3]
        b = strip_window[-2]
        c = strip_window[-1]
        a_uv = uvs[a] if uvs is not None and a < len(uvs) else None
        b_uv = uvs[b] if uvs is not None and b < len(uvs) else None
        c_uv = uvs[c] if uvs is not None and c < len(uvs) else None
        if _same_vertex(verts[a], verts[b], a_uv, b_uv) or _same_vertex(verts[b], verts[c], b_uv, c_uv) or _same_vertex(verts[a], verts[c], a_uv, c_uv):
            strip_window = [b, c]
            winding = 0
            continue

        if winding & 1:
            faces.append((base_index + b, base_index + a, base_index + c))
        else:
            faces.append((base_index + a, base_index + b, base_index + c))
        winding ^= 1
    return faces


def _iter_stitched_strip_runs(
    strips: list[TriStrip],
) -> list[tuple[list[tuple[float, float, float]], list[tuple[float, float]], list[tuple[int, int, int, int]], int]]:
    runs: list[tuple[list[tuple[float, float, float]], list[tuple[float, float]], list[tuple[int, int, int, int]], int]] = []
    current_verts: list[tuple[float, float, float]] = []
    current_uvs: list[tuple[float, float]] = []
    current_colors: list[tuple[int, int, int, int]] = []
    current_material = -1

    def flush() -> None:
        nonlocal current_verts, current_uvs, current_colors, current_material
        if len(current_verts) >= 3:
            runs.append((current_verts, current_uvs, current_colors, current_material))
        current_verts = []
        current_uvs = []
        current_colors = []
        current_material = -1

    for strip in strips:
        count = min(strip.count, len(strip.verts), len(strip.uvs))
        if count <= 0:
            continue
        strip_verts = strip.verts[:count]
        strip_uvs = strip.uvs[:count]
        strip_src_colors = strip.colors or []
        strip_colors = [
            strip_src_colors[index] if index < len(strip_src_colors) else (255, 255, 255, 255)
            for index in range(count)
        ]
        material = strip.material_res_index if strip.material_res_index >= 0 else -1
        if not current_verts:
            current_verts = list(strip_verts)
            current_uvs = list(strip_uvs)
            current_colors = list(strip_colors)
            current_material = material
            continue
        if material != current_material:
            flush()
            current_verts = list(strip_verts)
            current_uvs = list(strip_uvs)
            current_colors = list(strip_colors)
            current_material = material
            continue
        current_verts.extend(strip_verts)
        current_uvs.extend(strip_uvs)
        current_colors.extend(strip_colors)
    flush()
    return runs


class LVZArchive:
    def __init__(self, archive_name: str, lvz_path: Path, img_path: Path) -> None:
        self.archive_name = archive_name
        self.level = LevelChunk.from_archive(lvz_path.parent, archive_name)
        self.master_resource_ptr_by_res_id = self.level.read_master_resource_pointers()
        self.master_raw_by_res_id = self._load_master_resources()
        self.area_raw_by_res_id = self._load_area_resources()
        self.overlay_raw_variants_by_res_id = self._load_sector_overlay_resources()
        self.texture_cache: dict[int, tuple[DecodedTexture | None, str]] = {}
        self.geometry_cache: dict[int, ParsedStreamedGeometry | None] = {}
        self.known_hash_names = load_vcs_name_table()
        self.known_names = {name.lower(): name for name in self.known_hash_names.values()}

    def _load_master_resources(self) -> dict[int, bytes]:
        ordered = sorted((ptr, res_id) for res_id, ptr in self.master_resource_ptr_by_res_id.items())
        boundaries = [ptr for ptr, _res_id in ordered] + [len(self.level.data)]
        resources: dict[int, bytes] = {}
        for index, (ptr, res_id) in enumerate(ordered):
            end = boundaries[index + 1]
            if end > ptr:
                resources[res_id] = self.level.data[ptr:end]
        return resources

    def _load_area_resources(self) -> dict[int, bytes]:
        resources: dict[int, bytes] = {}
        for area_index, area in enumerate(self.level.areas):
            _log(f"[streamed] {self.archive_name} area {area_index + 1}/{len(self.level.areas)}")
            for res_id, blob in parse_area_resource_table(self.level, area).items():
                resources[res_id] = blob
        return resources

    def _load_sector_overlay_resources(self) -> dict[int, list[bytes]]:
        resources: dict[int, list[bytes]] = {}
        reachable = self.level.iter_reachable_sectors()
        for sector_id in sorted(reachable):
            sector = self.level.parse_sector(sector_id)
            for res_id, blob in sector.resources.items():
                variants = resources.setdefault(res_id, [])
                signature = (len(blob), blob[:32])
                if all((len(existing), existing[:32]) != signature for existing in variants):
                    variants.append(blob)
        for variants in resources.values():
            variants.sort(key=len, reverse=True)
        return resources

    def resource_blobs_for_res_id(self, res_id: int) -> list[tuple[bytes, str]]:
        candidates: list[tuple[bytes, str]] = []
        if res_id in self.area_raw_by_res_id:
            candidates.append((self.area_raw_by_res_id[res_id], "area"))
        if res_id in self.master_raw_by_res_id:
            candidates.append((self.master_raw_by_res_id[res_id], "master"))
        for blob in self.overlay_raw_variants_by_res_id.get(res_id, []):
            candidates.append((blob, "overlay"))
        return candidates

    def resource_blob_for_res_id(self, res_id: int) -> tuple[bytes | None, str | None]:
        candidates = self.resource_blobs_for_res_id(res_id)
        if candidates:
            return candidates[0]
        return None, None

    def _recover_texture_name(self, blob: bytes, res_id: int) -> tuple[str, str]:
        known_names = getattr(self, "known_names", {})
        known_hash_names = getattr(self, "known_hash_names", {})
        for match in TEXTURE_NAME_RE.finditer(blob):
            try:
                candidate = match.group(0).decode("ascii")
            except UnicodeDecodeError:
                continue
            lowered = candidate.lower()
            if lowered in known_names:
                return known_names[lowered], "embedded"
        scan_limit = min(len(blob), 0x80)
        for offset in range(0, max(0, scan_limit - 3), 4):
            hash_value = read_u32(blob, offset)
            if hash_value in known_hash_names:
                return known_hash_names[hash_value], "hash"
        return f"{self.archive_name.lower()}_{res_id}", "synthetic"

    def _decode_texture_blob(self, blob: bytes, res_id: int, origin: str) -> tuple[DecodedTexture | None, str]:
        if len(blob) < 16:
            return None, "synthetic"
        parsed = parse_ps2_header((b"\x00" * 16) + blob, 16)
        if parsed is None:
            return None, "synthetic"
        header = Ps2TexHeader(
            reserved0=parsed.reserved0,
            reserved1=parsed.reserved1,
            raster_offset=16,
            flags=parsed.flags,
        )
        block_size = max(0, len(blob) - 16)

        local_raster_offset: int | None = None
        if 0 < parsed.raster_offset < len(blob):
            local_raster_offset = parsed.raster_offset
        elif origin == "master":
            base_ptr = self.master_resource_ptr_by_res_id.get(res_id)
            if base_ptr is not None:
                candidate = parsed.raster_offset - base_ptr
                if 0 < candidate < len(blob):
                    local_raster_offset = candidate

        if local_raster_offset is not None:
            exact_block_size = _ps2_exact_mip_texel_bytes(parsed.width, parsed.height, parsed.bpp, parsed.mip_count)
            exact_block_size += _ps2_palette_bytes(parsed.bpp)
            if exact_block_size > 0 and local_raster_offset + exact_block_size <= len(blob):
                header = Ps2TexHeader(
                    reserved0=parsed.reserved0,
                    reserved1=parsed.reserved1,
                    raster_offset=local_raster_offset,
                    flags=parsed.flags,
                )
                block_size = exact_block_size

        rgba = decode_ps2_texture(blob, header, block_size)
        if rgba is None:
            return None, "synthetic"
        texture_name, naming_mode = self._recover_texture_name(blob, res_id)
        return DecodedTexture(
            name=texture_name,
            rgba=rgba.astype(np.uint8).tobytes(),
            width=int(rgba.shape[1]),
            height=int(rgba.shape[0]),
        ), naming_mode

    def texture_for_res_id(self, res_id: int) -> tuple[DecodedTexture | None, str]:
        if res_id not in self.texture_cache:
            decoded = None
            naming_mode = "synthetic"
            for blob, _origin in self.resource_blobs_for_res_id(res_id):
                decoded, naming_mode = self._decode_texture_blob(blob, res_id, _origin)
                if decoded is not None:
                    break
            self.texture_cache[res_id] = (decoded, naming_mode)
        return self.texture_cache[res_id]

    def _find_unpack_near(self, blob: bytes, offset: int, window: int = 8) -> int:
        start = max(0, offset - window)
        end = min(len(blob), offset + window + 4)
        for pos in range(start, end):
            if (pos & 3) == 0 and pos + 4 <= len(blob) and read_u32(blob, pos) == UNPACK:
                return pos
        raise ValueError(f"UNPACK header not found near 0x{offset:08X}")

    def _parse_mdl_material_list(self, blob: bytes, base: int) -> tuple[list[MDLMaterial], int]:
        if base + 4 > len(blob):
            return [], base
        count = struct.unpack_from("<H", blob, base)[0]
        size_bytes = struct.unpack_from("<H", blob, base + 2)[0]
        off = base + 4
        materials: list[MDLMaterial] = []
        limit = min(len(blob), base + 4 + size_bytes)
        for _ in range(count):
            if off + 22 > limit:
                break
            texture_id = struct.unpack_from("<H", blob, off)[0]
            tri_raw = struct.unpack_from("<H", blob, off + 2)[0]
            u_scale = half_to_float(struct.unpack_from("<H", blob, off + 4)[0]) or 1.0
            v_scale = half_to_float(struct.unpack_from("<H", blob, off + 6)[0]) or 1.0
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

    def _read_prelight(self, blob: bytes, offset: int) -> tuple[int, int, int, int]:
        color = struct.unpack_from("<H", blob, offset)[0]
        return (
            (color & 0x1F) * 255 // 0x1F,
            ((color >> 5) & 0x1F) * 255 // 0x1F,
            ((color >> 10) & 0x1F) * 255 // 0x1F,
            0xFF if (color & 0x8000) else 0,
        )

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
        verts = [self._read_vec3_i16_norm(blob, w + ((index + skip) * 6)) for index in range(count)]
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
        uvs = [self._read_uv(blob, w + ((index + skip) * 2)) for index in range(count)]
        w = align_up4(w + (count_all * 2))
        if (read_u32(blob, w) & 0xFF004000) != 0x6F000000:
            raise ValueError("Unexpected prelight header")
        if w + 4 + (count_all * 2) > len(blob):
            raise ValueError("Prelight payload truncated")
        colors = [self._read_prelight(blob, w + 4 + ((index + skip) * 2)) for index in range(count)]
        w = align_up4(w + 4 + (count_all * 2))
        if read_u32(blob, w) != MSCAL:
            if w + 4 <= len(blob) and read_u32(blob, w + 4) == MSCAL:
                w += 4
            else:
                raise ValueError("Missing MSCAL terminator")
        w += 4
        while w + 4 <= len(blob) and read_u32(blob, w) == 0:
            w += 4
        return TriStrip(count=count, verts=verts, uvs=uvs, colors=colors), w

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
            except (ValueError, struct.error):
                break
            groups.append(StripGroup(strips=[strip], start_off=off, end_off=next_off))
            first_batch = False
            try:
                off = self._find_unpack_near(blob, next_off)
            except ValueError:
                break
        return groups

    def _parse_streamed_mesh_descriptors(self, blob: bytes) -> tuple[list[StreamedMeshDesc], int] | None:
        if len(blob) < 8:
            return None
        num_meshes = struct.unpack_from("<H", blob, 0)[0]
        header_size = struct.unpack_from("<H", blob, 2)[0]
        # Valid streamed interiors can exceed 64 packet descriptors
        # (e.g. MAINLA/haitin_sh_int uses 66). Keep a sanity cap, but
        # don't reject larger real-world blobs up front.
        if not (0 < num_meshes <= 256):
            return None
        desc_size = 24
        desc_end = 4 + (num_meshes * desc_size)
        data_start = header_size + 4
        if desc_end > len(blob) or header_size < desc_end - 4 or data_start >= len(blob):
            return None

        meshes: list[StreamedMeshDesc] = []
        total_packets = 0
        for index in range(num_meshes):
            off = 4 + (index * desc_size)
            if off + desc_size > len(blob):
                return None
            packet_raw, texture_id, u_scale_raw, v_scale_raw = struct.unpack_from("<IHHH", blob, off)
            packet_size_bytes = packet_raw >> 1
            if packet_size_bytes <= 0:
                return None
            total_packets += packet_size_bytes
            meshes.append(
                StreamedMeshDesc(
                    packet_size_bytes=packet_size_bytes,
                    texture_id=texture_id,
                    u_scale=half_to_float(u_scale_raw) or 1.0,
                    v_scale=half_to_float(v_scale_raw) or 1.0,
                )
            )
        remaining = len(blob) - data_start
        if total_packets <= 0 or total_packets > remaining:
            return None
        return meshes, data_start

    def _parse_streamed_geometry_blob(self, blob: bytes, origin: str) -> ParsedStreamedGeometry | None:
        parsed = self._parse_streamed_mesh_descriptors(blob)
        if parsed is None:
            return None
        meshes, data_start = parsed

        vertices: list[tuple[float, float, float]] = []
        faces: list[tuple[int, int, int]] = []
        uvs: list[tuple[float, float]] = []
        vertex_colors: list[tuple[int, int, int, int]] = []
        face_texture_res_ids: list[int] = []

        cursor = data_start
        for mesh in meshes:
            packet_end = min(len(blob), cursor + mesh.packet_size_bytes)
            packet = blob[cursor:packet_end]
            cursor = packet_end
            if len(packet) < 16:
                continue
            groups = self._parse_groups(packet, 0)
            if not groups:
                continue
            packet_strips: list[TriStrip] = []
            for group in groups:
                for strip in group.strips:
                    strip.material_res_index = mesh.texture_id
                    strip.uvs = [(u * mesh.u_scale, v * mesh.v_scale) for u, v in strip.uvs]
                    packet_strips.append(strip)
            for strip_verts, strip_uvs, strip_colors, material_res_index in _iter_stitched_strip_runs(packet_strips):
                base = len(vertices)
                vertices.extend(strip_verts)
                uvs.extend(strip_uvs)
                vertex_colors.extend(strip_colors)
                for face in _triangulate_strip_faces(strip_verts, base_index=base, uvs=strip_uvs):
                    faces.append(face)
                    face_texture_res_ids.append(material_res_index)

        if not faces:
            return None
        return ParsedStreamedGeometry(vertices, faces, uvs, vertex_colors, face_texture_res_ids, origin)

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

    def _groups_to_geometry(self, groups: list[StripGroup], origin: str) -> ParsedStreamedGeometry | None:
        vertices: list[tuple[float, float, float]] = []
        faces: list[tuple[int, int, int]] = []
        uvs: list[tuple[float, float]] = []
        vertex_colors: list[tuple[int, int, int, int]] = []
        face_texture_res_ids: list[int] = []
        stitched_runs = _iter_stitched_strip_runs([strip for group in groups for strip in group.strips])
        for strip_verts, strip_uvs, strip_colors, material_res_index in stitched_runs:
            base = len(vertices)
            vertices.extend(strip_verts)
            uvs.extend(strip_uvs)
            vertex_colors.extend(strip_colors)
            for face in _triangulate_strip_faces(strip_verts, base_index=base, uvs=strip_uvs):
                faces.append(face)
                face_texture_res_ids.append(material_res_index)
        if not faces:
            return None
        return ParsedStreamedGeometry(vertices, faces, uvs, vertex_colors, face_texture_res_ids, origin)

    def _parse_embedded_unpack_geometry(self, blob: bytes, origin: str) -> ParsedStreamedGeometry | None:
        marker = struct.pack("<I", UNPACK)
        start = blob.find(marker)
        if start < 0:
            return None
        groups = self._parse_groups(blob, start)
        return self._groups_to_geometry(groups, origin)

    def _parse_position_only_geometry(self, blob: bytes, origin: str) -> ParsedStreamedGeometry | None:
        marker = struct.pack("<I", UNPACK)
        off = blob.find(marker)
        if off < 0:
            return None

        vertices: list[tuple[float, float, float]] = []
        faces: list[tuple[int, int, int]] = []
        uvs: list[tuple[float, float]] = []
        vertex_colors: list[tuple[int, int, int, int]] = []
        face_texture_res_ids: list[int] = []
        first_batch = True

        for _ in range(256):
            if off + 20 > len(blob):
                break
            count_all = read_u32(blob, off + 16) & 0x7FFF
            skip = 0 if first_batch else 2
            count = max(0, count_all - skip)
            w = off + 20
            if read_u32(blob, w) != STMASK:
                break
            w += 8
            if read_u32(blob, w) != STROW:
                break
            w += 20
            if (read_u32(blob, w) & 0xFF004000) != 0x79000000:
                break
            w += 4
            if w + (count_all * 6) > len(blob):
                break
            batch_vertices = [self._read_vec3_i16_norm(blob, w + ((index + skip) * 6)) for index in range(count)]
            w = align_up4(w + (count_all * 6))
            batch_uvs = [(0.0, 0.0)] * count
            if (read_u32(blob, w) & 0xFF004000) != 0x6F000000:
                break
            batch_colors = [self._read_prelight(blob, w + 4 + ((index + skip) * 2)) for index in range(count)]
            base = len(vertices)
            vertices.extend(batch_vertices)
            uvs.extend(batch_uvs)
            vertex_colors.extend(batch_colors)
            for face in _triangulate_strip_faces(batch_vertices, base_index=base, uvs=batch_uvs):
                faces.append(face)
                face_texture_res_ids.append(-1)

            next_search = align_up4(w + 4 + (count_all * 2))
            try:
                off = self._find_unpack_near(blob, next_search)
            except ValueError:
                break
            first_batch = False

        if not faces:
            return None
        return ParsedStreamedGeometry(vertices, faces, uvs, vertex_colors, face_texture_res_ids, origin)

    def geometry_for_res_id(self, res_id: int) -> ParsedStreamedGeometry | None:
        if res_id in self.geometry_cache:
            return self.geometry_cache[res_id]
        geometry = None
        for blob, origin in self.resource_blobs_for_res_id(res_id):
            geometry = self._parse_streamed_geometry_blob(blob, origin)
            if geometry is None:
                materials, next_off = self._parse_mdl_material_list(blob, 0)
                groups = self._parse_groups(blob, next_off)
                self._assign_materials(materials, groups)
                geometry = self._groups_to_geometry(groups, origin)
            if geometry is None:
                geometry = self._parse_embedded_unpack_geometry(blob, origin)
            if geometry is None:
                geometry = self._parse_position_only_geometry(blob, origin)
            if geometry is not None:
                break
        self.geometry_cache[res_id] = geometry
        return geometry


def rw_matrix(values: tuple[float, ...]) -> np.ndarray:
    raw = np.asarray(values, dtype=np.float64).reshape(4, 4)
    return raw.T


def basis_lengths(values: tuple[float, ...]) -> tuple[float, float, float]:
    matrix = rw_matrix(values)
    linear = matrix[:3, :3]
    lengths = np.linalg.norm(linear, axis=0)
    return (float(lengths[0]), float(lengths[1]), float(lengths[2]))


def matrix_inverse(values: tuple[float, ...]) -> np.ndarray:
    return np.linalg.inv(rw_matrix(values))


def transform_point(matrix: np.ndarray, point: tuple[float, float, float]) -> tuple[float, float, float]:
    vector = np.array([point[0], point[1], point[2], 1.0], dtype=np.float64)
    out = matrix @ vector
    return (float(out[0]), float(out[1]), float(out[2]))


def translation_inverse(values: tuple[float, ...]) -> np.ndarray:
    matrix = np.identity(4, dtype=np.float64)
    world = rw_matrix(values)
    matrix[0, 3] = -world[0, 3]
    matrix[1, 3] = -world[1, 3]
    matrix[2, 3] = -world[2, 3]
    return matrix


def _normalize_vector(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if not math.isfinite(length) or length <= 1e-8:
        raise ValueError("Degenerate basis vector")
    return vector / length


def rotation_translation_inverse(values: tuple[float, ...]) -> np.ndarray:
    world = rw_matrix(values)
    linear = world[:3, :3]

    right = _normalize_vector(linear[:, 0])
    up = linear[:, 1] - (np.dot(linear[:, 1], right) * right)
    up = _normalize_vector(up)
    at = np.cross(right, up)
    if np.dot(at, linear[:, 2]) < 0.0:
        at = -at
    at = _normalize_vector(at)

    rotation = np.column_stack((right, up, at))
    inverse = np.identity(4, dtype=np.float64)
    inverse[:3, :3] = rotation.T
    inverse[:3, 3] = -(rotation.T @ world[:3, 3])
    return inverse


def _quaternion_matrix(rotation: tuple[float, float, float, float]) -> np.ndarray:
    x, y, z, w = rotation
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if not math.isfinite(length) or length <= 1e-8:
        raise ValueError("Degenerate quaternion")
    x /= length
    y /= length
    z /= length
    w /= length
    matrix = np.identity(4, dtype=np.float64)
    matrix[0, 0] = 1.0 - 2.0 * (y * y + z * z)
    matrix[0, 1] = 2.0 * (x * y - z * w)
    matrix[0, 2] = 2.0 * (x * z + y * w)
    matrix[1, 0] = 2.0 * (x * y + z * w)
    matrix[1, 1] = 1.0 - 2.0 * (x * x + z * z)
    matrix[1, 2] = 2.0 * (y * z - x * w)
    matrix[2, 0] = 2.0 * (x * z - y * w)
    matrix[2, 1] = 2.0 * (y * z + x * w)
    matrix[2, 2] = 1.0 - 2.0 * (x * x + y * y)
    return matrix


def _conjugate_quaternion(rotation: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, z, w = rotation
    return (-x, -y, -z, w)


def _ipl_matrix(transform: IplTransform) -> np.ndarray:
    # GTA IPL rows store the conjugated quaternion. Convert back to the
    # world-space rotation matrix before using the transform as an anchor.
    matrix = _quaternion_matrix(_conjugate_quaternion(transform.rotation))
    matrix[0, 3] = transform.position[0]
    matrix[1, 3] = transform.position[1]
    matrix[2, 3] = transform.position[2]
    return matrix


def _placement_translation(values: tuple[float, ...]) -> tuple[float, float, float]:
    matrix = rw_matrix(values)
    return (float(matrix[0, 3]), float(matrix[1, 3]), float(matrix[2, 3]))


def _find_linked_ipl_transform(
    placements: list[StreamedPlacement],
    ipl_summary: IplSummary | None,
) -> IplTransform | None:
    if ipl_summary is None:
        return None
    linked_ids = {placement.linked_ipl_id for placement in placements if placement.linked_ipl_id is not None}
    if len(linked_ids) != 1:
        return None
    linked_id = next(iter(linked_ids))
    return ipl_summary.transforms_by_entity_id.get(linked_id)


def _find_nearest_ipl_transform(
    model_name: str,
    placements: list[StreamedPlacement],
    ipl_summary: IplSummary | None,
) -> IplTransform | None:
    if ipl_summary is None:
        return None
    candidates = ipl_summary.transforms_by_model.get(model_name.lower(), [])
    if not candidates:
        return None
    game_dat_candidates = [candidate for candidate in candidates if candidate.source_file.startswith("GAME.dat:")]
    if game_dat_candidates:
        candidates = game_dat_candidates
    anchor = _placement_translation(placements[0].matrix)
    best: IplTransform | None = None
    best_dist = float("inf")
    for candidate in candidates:
        dx = candidate.position[0] - anchor[0]
        dy = candidate.position[1] - anchor[1]
        dz = candidate.position[2] - anchor[2]
        dist = dx * dx + dy * dy + dz * dz
        if dist < best_dist:
            best_dist = dist
            best = candidate
    return best


def choose_base_transform(placements: list[StreamedPlacement]) -> tuple[np.ndarray, StreamedPlacement | None]:
    best_transform: np.ndarray | None = None
    best_placement: StreamedPlacement | None = None
    best_score = float("-inf")
    for placement in placements:
        lengths = basis_lengths(placement.matrix)
        if not all(math.isfinite(length) for length in lengths):
            continue
        score = max(lengths)
        if score <= 1e-6:
            continue
        if score > best_score:
            best_score = score
            try:
                best_transform = rotation_translation_inverse(placement.matrix)
                best_placement = placement
            except ValueError:
                continue
    if best_transform is not None:
        return best_transform, best_placement
    return np.identity(4, dtype=np.float64), None


def merge_texture(existing: DecodedTexture, incoming: DecodedTexture) -> DecodedTexture:
    if incoming.width * incoming.height > existing.width * existing.height:
        return incoming
    return existing


def _geometry_signature(fragment_keys: set[tuple[int, int, int]]) -> str:
    digest = hashlib.sha1()
    for fragment_key in sorted(fragment_keys):
        digest.update(repr(fragment_key).encode("utf-8"))
    return digest.hexdigest()


def _unique_stem(preferred: str, used_stems: set[str], fallback_suffix: str = "") -> str:
    stem = sanitize_filename(preferred)
    if stem not in used_stems:
        return stem
    if fallback_suffix:
        candidate = sanitize_filename(f"{preferred}{fallback_suffix}")
        if candidate not in used_stems:
            return candidate
    index = 1
    while True:
        candidate = sanitize_filename(f"{preferred}_{index}")
        if candidate not in used_stems:
            return candidate
        index += 1


def export_streamed_archive(
    archive_name: str,
    root: Path,
    output_root: Path,
    plan: StreamedArchivePlan,
    report: ReportData,
    global_knackers_textures: dict[str, DecodedTexture] | None = None,
    ipl_summary: IplSummary | None = None,
    on_model_done: Callable[[str, StreamedModelPlan, bool], None] | None = None,
) -> dict[str, int]:
    _log(f"[streamed] load {archive_name}.LVZ")
    archive = LVZArchive(archive_name, root / f"{archive_name}.LVZ", root / f"{archive_name}.IMG")
    archive_dir = output_root / archive_name
    archive_dir.mkdir(parents=True, exist_ok=True)

    textures_by_txd: dict[str, dict[str, DecodedTexture]] = {}
    knackers_textures = global_knackers_textures if global_knackers_textures is not None else {}
    exported_models = 0
    missing_res_ids = 0
    no_resource_res_ids = 0
    decode_failed_res_ids = 0
    models_recovered_only_area = 0
    models_recovered_only_interior = 0
    models_recovered_only_swap = 0
    models_recovered_via_area = 0
    models_recovered_via_interior = 0
    models_recovered_via_swap = 0
    models_with_hidden_conflicts = 0
    dff_failed_models = 0
    skipped_bad_fragments = 0
    salvaged_models = 0
    interior_models_exported = 0
    interior_named_exports = 0
    interior_unresolved_skips = 0
    interior_conflict_skips = 0
    interior_texture_names_recovered_ids: set[int] = set()
    interior_texture_names_fallback_ids: set[int] = set()
    exported_signatures_by_name: dict[str, set[str]] = {}
    used_output_stems: set[str] = set()

    for model in plan.model_exports:
        model_exported = False
        if not model.placements:
            if on_model_done is not None:
                on_model_done(archive_name, model, False)
            continue
        try:
            diagnostics = report.interior_diagnostics if model.export_kind.startswith("interior") else report.streamed_diagnostics
            _log(
                f"[streamed] {archive_name} model {model.output_name}: "
                f"{len(model.placements)} resource candidates"
            )
            visible_placements = [placement for placement in model.placements if placement.visible]
            hidden_placements = [placement for placement in model.placements if not placement.visible]
            placement_sets = [visible_placements or model.placements]
            if visible_placements and hidden_placements:
                placement_sets.append(hidden_placements)

            vertices: list[tuple[float, float, float]] = []
            faces: list[tuple[int, int, int]] = []
            uvs: list[tuple[float, float]] = []
            vertex_colors: list[tuple[int, int, int, int]] = []
            face_materials: list[str] = []
            seen_fragments: set[tuple[int, int, int]] = set()
            used_resource_origins: set[str] = set()
            used_source_kinds: set[str] = set()
            recovered_only_from_hidden = False
            bad_fragments_for_model = 0

            localize_inverse: np.ndarray
            base_inverse, base_placement = choose_base_transform(visible_placements or hidden_placements)
            anchor_ipl = _find_linked_ipl_transform(visible_placements or hidden_placements, ipl_summary)
            if anchor_ipl is None and model.export_kind == "world_named":
                anchor_ipl = _find_nearest_ipl_transform(model.model_name, visible_placements or hidden_placements, ipl_summary)
            if anchor_ipl is not None:
                localize_inverse = np.linalg.inv(_ipl_matrix(anchor_ipl))
                if anchor_ipl.entity_id is not None:
                    diagnostics.append(
                        f"{archive_name}: localized {model.output_name} against linked entity 0x{anchor_ipl.entity_id:X} "
                        f"from {anchor_ipl.source_file} at ({anchor_ipl.position[0]:.3f}, {anchor_ipl.position[1]:.3f}, {anchor_ipl.position[2]:.3f})"
                    )
                else:
                    diagnostics.append(
                        f"{archive_name}: localized {model.output_name} against IPL transform from {anchor_ipl.source_file} "
                        f"at ({anchor_ipl.position[0]:.3f}, {anchor_ipl.position[1]:.3f}, {anchor_ipl.position[2]:.3f})"
                    )
            else:
                localize_inverse = base_inverse
                if base_placement is None:
                    diagnostics.append(
                        f"{archive_name}: all placement matrices for {model.output_name} were degenerate; used identity fallback"
                    )
                elif base_placement is not (visible_placements or hidden_placements)[0]:
                    diagnostics.append(
                        f"{archive_name}: skipped degenerate base placement for {model.output_name}; "
                        f"used res_id={base_placement.res_id} from sector={base_placement.sector_id}"
                    )

            for set_index, placements in enumerate(placement_sets):
                fallback_mode = set_index > 0
                for placement in placements:
                    blob, _origin = archive.resource_blob_for_res_id(placement.res_id)
                    if blob is None:
                        no_resource_res_ids += 1
                        missing_res_ids += 1
                        continue
                    try:
                        geometry = archive.geometry_for_res_id(placement.res_id)
                    except Exception:
                        geometry = None
                    if geometry is None:
                        decode_failed_res_ids += 1
                        missing_res_ids += 1
                        continue
                    fragment_key = (placement.res_id, len(geometry.faces), len(geometry.vertices))
                    if fragment_key in seen_fragments:
                        continue
                    seen_fragments.add(fragment_key)
                    local_matrix = localize_inverse @ rw_matrix(placement.matrix)
                    transformed_vertices = [transform_point(local_matrix, vertex) for vertex in geometry.vertices]
                    if not _fragment_vertices_valid(transformed_vertices):
                        skipped_bad_fragments += 1
                        bad_fragments_for_model += 1
                        diagnostics.append(
                            f"{archive_name}: skipped corrupt fragment for {model.output_name} "
                            f"(res_id={placement.res_id}, sector={placement.sector_id}, pass={placement.pass_index}, "
                            f"source={placement.source_kind})"
                        )
                        continue
                    base_index = len(vertices)
                    vertices.extend(transformed_vertices)
                    # PS2 UVs are emitted in the same convention as the standard
                    # MDL path, so flip V here before writing the DFF.
                    uvs.extend((u, 1.0 - v) for u, v in geometry.uvs)
                    if geometry.vertex_colors and len(geometry.vertex_colors) == len(geometry.vertices):
                        vertex_colors.extend(geometry.vertex_colors)
                    else:
                        vertex_colors.extend([(255, 255, 255, 255)] * len(geometry.vertices))
                    used_resource_origins.add(geometry.resource_origin)
                    used_source_kinds.add(placement.source_kind)
                    for face_index, (a, b, c) in enumerate(geometry.faces):
                        faces.append((base_index + a, base_index + b, base_index + c))
                        texture_res_id = geometry.face_texture_res_ids[face_index]
                        texture, naming_mode = archive.texture_for_res_id(texture_res_id) if texture_res_id >= 0 else (None, "synthetic")
                        texture_name = texture.name if texture is not None else ""
                        face_materials.append(texture_name)
                        if model.export_kind.startswith("interior") and texture_res_id >= 0:
                            if texture is not None and naming_mode != "synthetic":
                                interior_texture_names_recovered_ids.add(texture_res_id)
                            else:
                                interior_texture_names_fallback_ids.add(texture_res_id)
                        effective_txd_name = model.txd_name
                        if texture is not None and effective_txd_name:
                            txd_bucket = textures_by_txd.setdefault(effective_txd_name, {})
                            existing = txd_bucket.get(texture_name)
                            if existing is not None and existing.rgba != texture.rgba:
                                report.streamed_texture_conflicts.append(
                                    f"{archive_name}: texture '{texture_name}' had conflicting data in {effective_txd_name}; kept higher-resolution copy"
                                )
                            txd_bucket[texture_name] = merge_texture(existing, texture) if existing else texture
                            if effective_txd_name.lower() == "knackers":
                                existing_knackers = knackers_textures.get(texture_name)
                                if existing_knackers is not None and existing_knackers.rgba != texture.rgba:
                                    report.knackers_texture_conflicts.append(
                                        f"{archive_name}: texture '{texture_name}' had conflicting data; kept higher-resolution copy"
                                    )
                                knackers_textures[texture_name] = merge_texture(existing_knackers, texture) if existing_knackers else texture
                if faces and fallback_mode:
                    recovered_only_from_hidden = True
                if faces:
                    break

            if faces:
                mesh = MeshData(
                    vertices=vertices,
                    faces=faces,
                    uvs=uvs,
                    face_materials=face_materials,
                    vertex_colors=vertex_colors,
                )
                base_stem = sanitize_filename(model.output_name or model.model_name)
                export_signature = _geometry_signature(seen_fragments)
                output_stem = base_stem
                existing_signatures = exported_signatures_by_name.setdefault(base_stem, set())
                if model.export_kind == "interior_named":
                    if export_signature in existing_signatures:
                        diagnostics.append(
                            f"{archive_name}: skipped duplicate interior export for {model.model_name}; identical geometry already exported"
                        )
                        continue
                    if base_stem in used_output_stems:
                        diagnostics.append(
                            f"{archive_name}: skipped interior export for {model.model_name} from sector={model.placements[0].sector_id}, "
                            f"res_id={model.placements[0].res_id} because only exact IDE model names are allowed in main output"
                        )
                        interior_conflict_skips += 1
                        continue
                    else:
                        interior_named_exports += 1
                elif model.export_kind == "interior_fallback":
                    diagnostics.append(
                        f"{archive_name}: skipped unresolved interior resource from sector={model.placements[0].sector_id}, "
                        f"res_id={model.placements[0].res_id} because no IDE model name was resolved"
                    )
                    interior_unresolved_skips += 1
                    continue
                else:
                    if export_signature in existing_signatures:
                        diagnostics.append(
                            f"{archive_name}: skipped duplicate world export for {model.model_name}; identical geometry already exported"
                        )
                        continue
                    output_stem = _unique_stem(base_stem, used_output_stems)
                if not _mesh_vertices_finite(vertices):
                    dff_failed_models += 1
                    diagnostics.append(
                        f"{archive_name}: skipped DFF export for {model.output_name}: non-finite or extreme vertex coordinates"
                    )
                    continue
                if bad_fragments_for_model:
                    salvaged_models += 1
                    diagnostics.append(
                        f"{archive_name}: salvaged {model.output_name} after dropping {bad_fragments_for_model} corrupt fragment(s)"
                    )
                try:
                    write_dff_from_mesh(mesh, archive_dir / f"{output_stem}.dff", output_stem)
                except Exception as exc:
                    dff_failed_models += 1
                    diagnostics.append(
                        f"{archive_name}: DFF export failed for {model.output_name}: {exc}"
                    )
                    continue
                try:
                    write_col_from_mesh(mesh, archive_dir / f"{output_stem}.col", model_id=model.placements[0].res_id)
                except Exception as exc:
                    diagnostics.append(
                        f"{archive_name}: collision export failed for {model.output_name}: {exc}"
                    )
                exported_models += 1
                model_exported = True
                used_output_stems.add(output_stem)
                existing_signatures.add(export_signature)
                if model.export_kind.startswith("interior"):
                    interior_models_exported += 1
                if model.has_hidden_alternates and visible_placements and hidden_placements:
                    models_with_hidden_conflicts += 1
                    diagnostics.append(
                        f"{archive_name}: kept default-visible fragments for {model.output_name}; hidden alternates were left as fallback"
                    )
                if "area" in used_resource_origins:
                    models_recovered_via_area += 1
                if used_source_kinds == {"interior"}:
                    models_recovered_only_interior += 1
                elif "interior" in used_source_kinds:
                    models_recovered_via_interior += 1
                if used_source_kinds == {"swap-sector"} or recovered_only_from_hidden:
                    models_recovered_only_swap += 1
                elif "swap-sector" in used_source_kinds:
                    models_recovered_via_swap += 1
                if used_resource_origins == {"area"}:
                    models_recovered_only_area += 1
                _log(f"[streamed] wrote {archive_name}/{output_stem}.dff + .col")
            else:
                diagnostics.append(
                    f"{archive_name}: no geometry decoded for {model.output_name} from {len(model.placements)} candidate resource ids"
                )
        finally:
            if on_model_done is not None:
                on_model_done(archive_name, model, model_exported)

    for txd_name, textures in textures_by_txd.items():
        if textures:
            if txd_name.lower() == "knackers":
                continue
            write_txd_from_decoded_textures(archive_dir / f"{sanitize_filename(txd_name)}.txd", list(textures.values()))
            _log(f"[streamed] wrote {archive_name}/{sanitize_filename(txd_name)}.txd")

    if global_knackers_textures is None and knackers_textures:
        write_txd_from_decoded_textures(output_root / "knackers.txd", list(knackers_textures.values()))
        _log("[streamed] updated knackers.txd")

    return {
        "exported_models": exported_models,
        "missing_res_ids": missing_res_ids,
        "no_resource_res_ids": no_resource_res_ids,
        "decode_failed_res_ids": decode_failed_res_ids,
        "models_recovered_only_area": models_recovered_only_area,
        "models_recovered_only_interior": models_recovered_only_interior,
        "models_recovered_only_swap": models_recovered_only_swap,
        "models_recovered_via_area": models_recovered_via_area,
        "models_recovered_via_interior": models_recovered_via_interior,
        "models_recovered_via_swap": models_recovered_via_swap,
        "models_with_hidden_conflicts": models_with_hidden_conflicts,
        "dff_failed_models": dff_failed_models,
        "skipped_bad_fragments": skipped_bad_fragments,
        "salvaged_models": salvaged_models,
        "interior_models_exported": interior_models_exported,
        "interior_named_exports": interior_named_exports,
        "interior_unresolved_skips": interior_unresolved_skips,
        "interior_conflict_skips": interior_conflict_skips,
        "interior_texture_names_recovered": len(interior_texture_names_recovered_ids),
        "interior_texture_names_fallback": len(interior_texture_names_fallback_ids),
        "exported_txds": sum(1 for txd_name, textures in textures_by_txd.items() if textures and txd_name.lower() != "knackers"),
        "decoded_textures": sum(1 for texture, _naming_mode in archive.texture_cache.values() if texture is not None),
        "loaded_master_resource_blobs": len(archive.master_raw_by_res_id),
        "loaded_area_resource_blobs": len(archive.area_raw_by_res_id),
        "loaded_overlay_resource_blobs": len(archive.overlay_raw_variants_by_res_id),
    }


def _mesh_vertices_finite(vertices: list[tuple[float, float, float]]) -> bool:
    for x, y, z in vertices:
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return False
        if max(abs(x), abs(y), abs(z)) > 1e20:
            return False
    return True


def _fragment_vertices_valid(vertices: list[tuple[float, float, float]]) -> bool:
    if not vertices:
        return False
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for x, y, z in vertices:
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return False
        max_abs = max(abs(x), abs(y), abs(z))
        if max_abs > 1_000_000.0:
            return False
        mins[0] = min(mins[0], x)
        mins[1] = min(mins[1], y)
        mins[2] = min(mins[2], z)
        maxs[0] = max(maxs[0], x)
        maxs[1] = max(maxs[1], y)
        maxs[2] = max(maxs[2], z)
    if max(maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]) > 1_000_000.0:
        return False
    return True
