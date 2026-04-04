from __future__ import annotations

import hashlib
import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .models import ReportData, StreamedArchivePlan, StreamedPlacement
from .pure_backend import DecodedTexture, MeshData, write_col_from_mesh, write_dff_from_mesh, write_txd_from_decoded_textures
from .reference_data import load_vcs_name_table
from .streamed_world import LevelChunk, parse_area_resource_table
from .utils import sanitize_filename
from .vendor.bleeds.tex import Ps2TexHeader, decode_ps2_texture, parse_ps2_header


UNPACK = 0x6C018000
STMASK = 0x20000000
STROW = 0x30000000
MSCAL = 0x14000006
RESOURCE_ORIGIN_PRIORITY = {
    "area": 0,
    "master": 1,
    "overlay": 2,
}

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
    colors: list[tuple[int, int, int, int]] = field(default_factory=list)
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
    vertex_array: np.ndarray
    face_array: np.ndarray
    uv_array: np.ndarray
    vertex_color_array: np.ndarray
    unique_texture_res_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ResourceBlobVariant:
    res_id: int
    origin: str
    provenance: str
    blob: bytes


@dataclass(slots=True)
class DecodedResourceVariant:
    variant: ResourceBlobVariant
    geometry: ParsedStreamedGeometry


def _nearly_equal(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) <= eps


def _white_vertex_colors(count: int) -> list[tuple[int, int, int, int]]:
    return [(255, 255, 255, 255)] * max(0, count)


def _decode_ps2_prelight(color: int) -> tuple[int, int, int, int]:
    return (
        (color & 0x1F) * 255 // 0x1F,
        ((color >> 5) & 0x1F) * 255 // 0x1F,
        ((color >> 10) & 0x1F) * 255 // 0x1F,
        0xFF if (color & 0x8000) else 0,
    )


def _build_ps2_flags(width: int, height: int, bpp: int, mip_count: int, swizzle_mask: int) -> int:
    width_pow2 = int(round(math.log2(width))) if width > 0 else 0
    height_pow2 = int(round(math.log2(height))) if height > 0 else 0
    return (
        (swizzle_mask & 0xFF)
        | ((mip_count & 0xF) << 8)
        | ((bpp & 0x3F) << 14)
        | ((width_pow2 & 0x3F) << 20)
        | ((height_pow2 & 0x3F) << 26)
    )


def _make_ps2_header_variant(
    header: Ps2TexHeader,
    *,
    width: int | None = None,
    height: int | None = None,
    swizzle_mask: int | None = None,
) -> Ps2TexHeader:
    resolved_width = int(width if width is not None else header.width)
    resolved_height = int(height if height is not None else header.height)
    resolved_swizzle = int(swizzle_mask if swizzle_mask is not None else header.swizzle_mask)
    return Ps2TexHeader(
        reserved0=header.reserved0,
        reserved1=header.reserved1,
        raster_offset=header.raster_offset,
        flags=_build_ps2_flags(
            resolved_width,
            resolved_height,
            header.bpp,
            header.mip_count,
            resolved_swizzle,
        ),
    )


def _texture_decode_score(rgba: np.ndarray) -> float:
    if rgba.size == 0:
        return float("-inf")
    horizontal_matches = float(np.mean(np.all(rgba[:, 1:, :] == rgba[:, :-1, :], axis=2))) if rgba.shape[1] > 1 else 1.0
    vertical_matches = float(np.mean(np.all(rgba[1:, :, :] == rgba[:-1, :, :], axis=2))) if rgba.shape[0] > 1 else 1.0
    delta_h = float(np.mean(np.abs(np.diff(rgba[:, :, :3].astype(np.int16), axis=1)))) if rgba.shape[1] > 1 else 0.0
    delta_v = float(np.mean(np.abs(np.diff(rgba[:, :, :3].astype(np.int16), axis=0)))) if rgba.shape[0] > 1 else 0.0
    return (horizontal_matches + vertical_matches) - ((delta_h + delta_v) / 255.0)


def _blob_signature(blob: bytes) -> tuple[int, str]:
    return (len(blob), hashlib.sha1(blob).hexdigest())


def _variant_cache_key(variant: ResourceBlobVariant) -> tuple[int, str, int, str]:
    return (
        int(variant.res_id),
        variant.origin,
        len(variant.blob),
        hashlib.sha1(variant.blob[:256]).hexdigest(),
    )


def _vertex_bounds_signature(vertices: list[tuple[float, float, float]]) -> tuple[float, ...]:
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for x, y, z in vertices:
        mins[0] = min(mins[0], x)
        mins[1] = min(mins[1], y)
        mins[2] = min(mins[2], z)
        maxs[0] = max(maxs[0], x)
        maxs[1] = max(maxs[1], y)
        maxs[2] = max(maxs[2], z)
    return tuple(round(value, 4) for value in (*mins, *maxs))


def _matrix_signature(matrix: np.ndarray) -> tuple[float, ...]:
    return tuple(round(float(value), 4) for value in matrix.reshape(-1))


def _variant_sort_key(variant: ResourceBlobVariant) -> tuple[int, int, str]:
    return (
        RESOURCE_ORIGIN_PRIORITY.get(variant.origin, 99),
        -len(variant.blob),
        variant.provenance,
    )


def _finalize_geometry(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    uvs: list[tuple[float, float]],
    vertex_colors: list[tuple[int, int, int, int]] | None,
    face_texture_res_ids: list[int],
    origin: str,
) -> ParsedStreamedGeometry | None:
    if not faces:
        return None
    resolved_vertex_colors = list(vertex_colors or ())
    if len(resolved_vertex_colors) != len(vertices):
        resolved_vertex_colors = _white_vertex_colors(len(vertices))
    unique_texture_res_ids = tuple(sorted({res_id for res_id in face_texture_res_ids if res_id >= 0}))
    return ParsedStreamedGeometry(
        vertices=vertices,
        faces=faces,
        uvs=uvs,
        vertex_colors=resolved_vertex_colors,
        face_texture_res_ids=face_texture_res_ids,
        resource_origin=origin,
        vertex_array=np.asarray(vertices, dtype=np.float64),
        face_array=np.asarray(faces, dtype=np.int32),
        uv_array=np.asarray(uvs, dtype=np.float64),
        vertex_color_array=np.asarray(resolved_vertex_colors, dtype=np.uint8),
        unique_texture_res_ids=unique_texture_res_ids,
    )


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
        strip_colors = strip.colors[:count] if len(strip.colors) >= count else _white_vertex_colors(count)
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
    def __init__(
        self,
        archive_name: str,
        lvz_path: Path | None = None,
        img_path: Path | None = None,
        *,
        root: Path | None = None,
        level=None,
    ) -> None:
        self.archive_name = archive_name
        if root is not None:
            lvz_path = root / f"{archive_name}.LVZ"
            img_path = root / f"{archive_name}.IMG"
        if lvz_path is None and root is None:
            raise ValueError("LVZArchive requires either root or lvz_path")
        self.level = level if level is not None else LevelChunk.from_archive((root or lvz_path.parent), archive_name)
        self.master_variants_by_res_id = self._load_master_resources()
        self.area_variants_by_res_id = self._load_area_resources()
        self.overlay_variants_by_res_id = self._load_sector_overlay_resources()
        self.known_hash_names = load_vcs_name_table()
        self.known_names = {name.lower(): name for name in self.known_hash_names.values()}
        self.texture_cache: dict[int, DecodedTexture | None] = {}
        self.geometry_cache: dict[tuple[int, str, int, str], ParsedStreamedGeometry | None] = {}

    def _append_unique_variant(
        self,
        variants_by_res_id: dict[int, list[ResourceBlobVariant]],
        *,
        res_id: int,
        origin: str,
        provenance: str,
        blob: bytes,
    ) -> None:
        variants = variants_by_res_id.setdefault(res_id, [])
        signature = _blob_signature(blob)
        if any(_blob_signature(existing.blob) == signature for existing in variants):
            return
        variants.append(
            ResourceBlobVariant(
                res_id=res_id,
                origin=origin,
                provenance=provenance,
                blob=blob,
            )
        )

    def _load_master_resources(self) -> dict[int, list[ResourceBlobVariant]]:
        pointers = self.level.read_master_resource_pointers()
        ordered = sorted((ptr, res_id) for res_id, ptr in pointers.items())
        boundaries = [ptr for ptr, _res_id in ordered] + [len(self.level.data)]
        resources: dict[int, list[ResourceBlobVariant]] = {}
        for index, (ptr, res_id) in enumerate(ordered):
            end = boundaries[index + 1]
            if end > ptr:
                self._append_unique_variant(
                    resources,
                    res_id=res_id,
                    origin="master",
                    provenance="master",
                    blob=self.level.data[ptr:end],
                )
        return resources

    def _load_area_resources(self) -> dict[int, list[ResourceBlobVariant]]:
        resources: dict[int, list[ResourceBlobVariant]] = {}
        for area_index, area in enumerate(self.level.areas):
            _log(f"[streamed] {self.archive_name} area {area_index + 1}/{len(self.level.areas)}")
            for res_id, blob in parse_area_resource_table(self.level, area).items():
                self._append_unique_variant(
                    resources,
                    res_id=res_id,
                    origin="area",
                    provenance=f"area[{area_index}]",
                    blob=blob,
                )
        return resources

    def _load_sector_overlay_resources(self) -> dict[int, list[ResourceBlobVariant]]:
        resources: dict[int, list[ResourceBlobVariant]] = {}
        reachable = self.level.iter_reachable_sectors()
        for sector_id in sorted(reachable):
            sector = self.level.parse_sector(sector_id)
            for res_id, blob in sector.resources.items():
                self._append_unique_variant(
                    resources,
                    res_id=res_id,
                    origin="overlay",
                    provenance=f"sector[{sector_id}]",
                    blob=blob,
                )
        for variants in resources.values():
            variants.sort(key=lambda variant: len(variant.blob), reverse=True)
        return resources

    def resource_variants_for_res_id(self, res_id: int) -> list[ResourceBlobVariant]:
        candidates: list[ResourceBlobVariant] = []
        candidates.extend(self.area_variants_by_res_id.get(res_id, []))
        candidates.extend(self.master_variants_by_res_id.get(res_id, []))
        candidates.extend(self.overlay_variants_by_res_id.get(res_id, []))
        candidates.sort(key=_variant_sort_key)
        return candidates

    def resource_blobs_for_res_id(self, res_id: int) -> list[tuple[bytes, str]]:
        return [(variant.blob, variant.origin) for variant in self.resource_variants_for_res_id(res_id)]

    def resource_blob_for_res_id(self, res_id: int) -> tuple[bytes | None, str | None]:
        candidates = self.resource_variants_for_res_id(res_id)
        if candidates:
            return candidates[0].blob, candidates[0].origin
        return None, None

    def _recover_texture_name(self, blob: bytes, res_id: int) -> tuple[str, str]:
        known_names = getattr(self, "known_names", {})
        known_hash_names = getattr(self, "known_hash_names", {})
        for match in re.finditer(rb"[A-Za-z0-9_.-]{3,31}", blob):
            try:
                candidate = match.group(0).decode("ascii")
            except UnicodeDecodeError:
                continue
            resolved = known_names.get(candidate.lower())
            if resolved:
                return resolved, "embedded"
        if len(blob) >= 4:
            hash_name = known_hash_names.get(read_u32(blob, 0))
            if hash_name:
                return hash_name, "hash"
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
        name, naming_mode = self._recover_texture_name(blob, res_id)

        header_variants: list[Ps2TexHeader] = [_make_ps2_header_variant(header)]
        if header.width != header.height:
            header_variants.append(_make_ps2_header_variant(header, width=header.height, height=header.width))
        for swizzle_mask in (header.swizzle_mask, 0, 1):
            header_variants.append(_make_ps2_header_variant(header, swizzle_mask=swizzle_mask))
            if header.width != header.height:
                header_variants.append(
                    _make_ps2_header_variant(
                        header,
                        width=header.height,
                        height=header.width,
                        swizzle_mask=swizzle_mask,
                    )
                )

        seen_variants: set[tuple[int, int, int]] = set()
        best_rgba: np.ndarray | None = None
        best_score = float("-inf")
        nibble_orders = (True,) if header.bpp == 4 else (False,)
        for candidate_header in header_variants:
            variant_key = (candidate_header.width, candidate_header.height, candidate_header.swizzle_mask)
            if variant_key in seen_variants:
                continue
            seen_variants.add(variant_key)
            for high_nibble_first in nibble_orders:
                rgba = decode_ps2_texture(
                    blob,
                    candidate_header,
                    len(blob) - 16,
                    four_bit_high_nibble_first=high_nibble_first,
                )
                if rgba is None:
                    continue
                score = _texture_decode_score(rgba)
                if score > best_score:
                    best_score = score
                    best_rgba = rgba
        if best_rgba is None:
            return None, naming_mode
        return (
            DecodedTexture(
                name=name,
                rgba=best_rgba.astype(np.uint8).tobytes(),
                width=int(best_rgba.shape[1]),
                height=int(best_rgba.shape[0]),
                has_alpha=bool(np.any(best_rgba[:, :, 3] < 255)),
            ),
            naming_mode,
        )

    def texture_for_res_id(self, res_id: int) -> DecodedTexture | None:
        if res_id not in self.texture_cache:
            decoded = None
            for blob, origin in self.resource_blobs_for_res_id(res_id):
                decoded, _naming_mode = self._decode_texture_blob(blob, res_id, origin)
                if decoded is not None:
                    break
            self.texture_cache[res_id] = decoded
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

    def _read_vec3_i16_norm(
        self,
        blob: bytes,
        offset: int,
        *,
        denominator: float,
        scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
        position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> tuple[float, float, float]:
        return (
            (read_i16(blob, offset + 0) / denominator) * scale[0] + position[0],
            (read_i16(blob, offset + 2) / denominator) * scale[1] + position[1],
            (read_i16(blob, offset + 4) / denominator) * scale[2] + position[2],
        )

    def _read_uv(self, blob: bytes, offset: int, *, denominator: float) -> tuple[float, float]:
        return (blob[offset + 0] / denominator, blob[offset + 1] / denominator)

    def _parse_one_batch(
        self,
        blob: bytes,
        pos: int,
        *,
        first_batch: bool,
        position_denominator: float,
        uv_denominator: float,
        position_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
        position_offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> tuple[TriStrip, int]:
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
            self._read_vec3_i16_norm(
                blob,
                w + ((index + skip) * 6),
                denominator=position_denominator,
                scale=position_scale,
                position=position_offset,
            )
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
        uvs = [self._read_uv(blob, w + ((index + skip) * 2), denominator=uv_denominator) for index in range(count)]
        w = align_up4(w + (count_all * 2))
        if (read_u32(blob, w) & 0xFF004000) != 0x6F000000:
            raise ValueError("Unexpected prelight header")
        if w + 4 + (count_all * 2) > len(blob):
            raise ValueError("Prelight payload truncated")
        colors = [_decode_ps2_prelight(struct.unpack_from("<H", blob, w + 4 + ((index + skip) * 2))[0]) for index in range(count)]
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

    def _parse_groups(
        self,
        blob: bytes,
        start_off: int,
        *,
        position_denominator: float = 32767.5,
        uv_denominator: float = 127.5,
        position_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
        position_offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> list[StripGroup]:
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
                strip, next_off = self._parse_one_batch(
                    blob,
                    off,
                    first_batch=first_batch,
                    position_denominator=position_denominator,
                    uv_denominator=uv_denominator,
                    position_scale=position_scale,
                    position_offset=position_offset,
                )
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
        if not (0 < num_meshes <= 64):
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
            groups = self._parse_groups(packet, 0, position_denominator=32767.0, uv_denominator=128.0)
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

        return _finalize_geometry(vertices, faces, uvs, vertex_colors, face_texture_res_ids, origin)

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
        return _finalize_geometry(vertices, faces, uvs, vertex_colors, face_texture_res_ids, origin)

    def _find_ps2_geometry_transform(
        self,
        blob: bytes,
        unpack_offset: int,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        header_size = 64
        start_search = max(0, align_down4(unpack_offset - 128))
        end_search = min(unpack_offset, len(blob) - header_size)
        for header_start in range(start_search, end_search + 1, 4):
            if header_start + header_size > len(blob):
                break
            dma_offset = struct.unpack_from("<H", blob, header_start + 26)[0]
            if dma_offset <= 0 or header_start + dma_offset != unpack_offset:
                continue
            scale = struct.unpack_from("<3f", blob, header_start + 40)
            position = struct.unpack_from("<3f", blob, header_start + 52)
            if not all(math.isfinite(value) for value in (*scale, *position)):
                continue
            if max(abs(value) for value in (*scale, *position)) > 1_000_000.0:
                continue
            return (
                (float(scale[0]), float(scale[1]), float(scale[2])),
                (float(position[0]), float(position[1]), float(position[2])),
            )
        return None

    def _parse_embedded_unpack_geometry(self, blob: bytes, origin: str) -> ParsedStreamedGeometry | None:
        marker = struct.pack("<I", UNPACK)
        start = blob.find(marker)
        if start < 0:
            return None
        header_transform = self._find_ps2_geometry_transform(blob, start)
        position_scale = header_transform[0] if header_transform is not None else (1.0, 1.0, 1.0)
        position_offset = header_transform[1] if header_transform is not None else (0.0, 0.0, 0.0)
        groups = self._parse_groups(
            blob,
            start,
            position_denominator=32767.5,
            uv_denominator=127.5,
            position_scale=position_scale,
            position_offset=position_offset,
        )
        return self._groups_to_geometry(groups, origin)

    def _parse_position_only_geometry(self, blob: bytes, origin: str) -> ParsedStreamedGeometry | None:
        marker = struct.pack("<I", UNPACK)
        off = blob.find(marker)
        if off < 0:
            return None
        header_transform = self._find_ps2_geometry_transform(blob, off)
        position_scale = header_transform[0] if header_transform is not None else (1.0, 1.0, 1.0)
        position_offset = header_transform[1] if header_transform is not None else (0.0, 0.0, 0.0)

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
            batch_vertices = [
                self._read_vec3_i16_norm(
                    blob,
                    w + ((index + skip) * 6),
                    denominator=32767.5,
                    scale=position_scale,
                    position=position_offset,
                )
                for index in range(count)
            ]
            base = len(vertices)
            vertices.extend(batch_vertices)
            batch_uvs = [(0.0, 0.0)] * count
            uvs.extend(batch_uvs)
            vertex_colors.extend(_white_vertex_colors(count))
            for face in _triangulate_strip_faces(batch_vertices, base_index=base, uvs=batch_uvs):
                faces.append(face)
                face_texture_res_ids.append(-1)

            next_search = align_up4(w + (count_all * 6))
            try:
                off = self._find_unpack_near(blob, next_search)
            except ValueError:
                break
            first_batch = False

        return _finalize_geometry(vertices, faces, uvs, vertex_colors, face_texture_res_ids, origin)

    def geometry_for_variant(self, variant: ResourceBlobVariant) -> ParsedStreamedGeometry | None:
        cache_key = _variant_cache_key(variant)
        if cache_key in self.geometry_cache:
            return self.geometry_cache[cache_key]
        geometry = self._parse_streamed_geometry_blob(variant.blob, variant.origin)
        if geometry is None:
            materials, next_off = self._parse_mdl_material_list(variant.blob, 0)
            header_transform = self._find_ps2_geometry_transform(variant.blob, next_off)
            position_scale = header_transform[0] if header_transform is not None else (1.0, 1.0, 1.0)
            position_offset = header_transform[1] if header_transform is not None else (0.0, 0.0, 0.0)
            groups = self._parse_groups(
                variant.blob,
                next_off,
                position_denominator=32767.5,
                uv_denominator=127.5,
                position_scale=position_scale,
                position_offset=position_offset,
            )
            self._assign_materials(materials, groups)
            geometry = self._groups_to_geometry(groups, variant.origin)
        if geometry is None:
            geometry = self._parse_embedded_unpack_geometry(variant.blob, variant.origin)
        if geometry is None:
            geometry = self._parse_position_only_geometry(variant.blob, variant.origin)
        self.geometry_cache[cache_key] = geometry
        return geometry

    def decoded_variants_for_res_id(self, res_id: int) -> list[DecodedResourceVariant]:
        decoded: list[DecodedResourceVariant] = []
        for variant in self.resource_variants_for_res_id(res_id):
            geometry = self.geometry_for_variant(variant)
            if geometry is None:
                continue
            decoded.append(DecodedResourceVariant(variant=variant, geometry=geometry))
        return decoded

    def geometry_for_res_id(self, res_id: int) -> ParsedStreamedGeometry | None:
        decoded = self.decoded_variants_for_res_id(res_id)
        if decoded:
            return decoded[0].geometry
        return None


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


def rotation_translation_inverse(values: tuple[float, ...]) -> np.ndarray:
    world = rw_matrix(values)
    linear = world[:3, :3]
    basis_lengths = np.linalg.norm(linear, axis=0)
    rotation = np.identity(3, dtype=np.float64)
    for axis, length in enumerate(basis_lengths):
        if math.isfinite(length) and length > 1e-8:
            rotation[:, axis] = linear[:, axis] / length
    matrix = np.identity(4, dtype=np.float64)
    matrix[:3, :3] = rotation.T
    matrix[:3, 3] = -(rotation.T @ world[:3, 3])
    return matrix


def _ipl_transform_matrix(position: tuple[float, float, float], rotation: tuple[float, float, float, float]) -> np.ndarray:
    rx, ry, rz, rw = rotation
    x = -float(rx)
    y = -float(ry)
    z = -float(rz)
    w = float(rw)
    length = math.sqrt((x * x) + (y * y) + (z * z) + (w * w))
    if not math.isfinite(length) or length <= 1e-8:
        x = y = z = 0.0
        w = 1.0
    else:
        inv = 1.0 / length
        x *= inv
        y *= inv
        z *= inv
        w *= inv
    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z
    matrix = np.identity(4, dtype=np.float64)
    matrix[:3, :3] = np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float64,
    )
    matrix[:3, 3] = np.asarray(position, dtype=np.float64)
    return matrix


def _ipl_matrix(transform) -> np.ndarray:
    return _ipl_transform_matrix(transform.position, transform.rotation)


def _resolve_exact_linked_inverse(placements: list[StreamedPlacement], ipl_summary) -> tuple[np.ndarray | None, int | None]:
    if ipl_summary is None:
        return None, None
    transforms_by_entity_id = getattr(ipl_summary, "transforms_by_entity_id", None)
    if not isinstance(transforms_by_entity_id, dict):
        return None, None
    for placement in placements:
        linked_ipl_id = placement.linked_ipl_id
        if linked_ipl_id is None:
            continue
        transform = transforms_by_entity_id.get(linked_ipl_id)
        if transform is None:
            continue
        return np.linalg.inv(_ipl_transform_matrix(transform.position, transform.rotation)), linked_ipl_id
    return None, None


def _unwrap_texture_result(texture_result) -> DecodedTexture | None:
    if isinstance(texture_result, tuple):
        if texture_result and isinstance(texture_result[0], DecodedTexture):
            return texture_result[0]
        return None
    if isinstance(texture_result, DecodedTexture):
        return texture_result
    return None


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
            best_transform = rotation_translation_inverse(placement.matrix)
            best_placement = placement
    if best_transform is not None:
        return best_transform, best_placement
    return np.identity(4, dtype=np.float64), None


def merge_texture(existing: DecodedTexture, incoming: DecodedTexture) -> DecodedTexture:
    if incoming.width * incoming.height > existing.width * existing.height:
        return incoming
    return existing


def _cluster_sets_for_model(model) -> list[list[StreamedPlacement]]:
    return [cluster for cluster in [model.placements, *model.alternate_placement_sets] if cluster]


def _cluster_eval_placements(placements: list[StreamedPlacement]) -> list[StreamedPlacement]:
    visible = [placement for placement in placements if placement.visible]
    return visible or placements


def _resolve_single_linked_inverse(placements: list[StreamedPlacement], ipl_summary) -> tuple[np.ndarray | None, int | None]:
    linked_ids = {
        placement.linked_ipl_id
        for placement in placements
        if placement.linked_ipl_id is not None
    }
    if len(linked_ids) != 1:
        return None, None
    return _resolve_exact_linked_inverse(placements, ipl_summary)


def _cluster_localized_signatures(
    placements: list[StreamedPlacement],
    *,
    ipl_summary,
) -> tuple[tuple[float, ...], ...]:
    eval_placements = _cluster_eval_placements(placements)
    base_inverse, _base_placement = choose_base_transform(eval_placements)
    exact_inverse, _exact_linked_id = _resolve_single_linked_inverse(eval_placements, ipl_summary)
    if exact_inverse is not None:
        base_inverse = exact_inverse
    localized = {
        _matrix_signature(base_inverse @ rw_matrix(placement.matrix))
        for placement in eval_placements
    }
    return tuple(sorted(localized))


def _model_clusters_are_consistent(model, *, ipl_summary) -> tuple[bool, list[tuple[int, tuple[tuple[float, ...], ...]]]]:
    cluster_signatures: list[tuple[int, tuple[tuple[float, ...], ...]]] = []
    for cluster_index, placements in enumerate(_cluster_sets_for_model(model)):
        cluster_signatures.append(
            (
                cluster_index,
                _cluster_localized_signatures(
                    placements,
                    ipl_summary=ipl_summary,
                ),
            )
        )
    if len(cluster_signatures) <= 1:
        return True, cluster_signatures
    unique_signatures = {signature for _cluster_index, signature in cluster_signatures}
    return len(unique_signatures) == 1, cluster_signatures


def export_streamed_archive(
    archive_name: str,
    root: Path,
    output_root: Path,
    plan: StreamedArchivePlan,
    report: ReportData,
    dxt_level: int | None = None,
    global_knackers_textures: dict[str, DecodedTexture] | None = None,
    ipl_summary=None,
    on_model_done: Callable[[str, StreamedModelPlan, bool], None] | None = None,
) -> dict[str, int]:
    _log(f"[streamed] load {archive_name}.LVZ")
    archive = LVZArchive(archive_name, root=root, level=getattr(plan, "preloaded_level", None))
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
    interior_conflict_skips = 0
    interior_unresolved_skips = 0
    exported_output_names: set[str] = set()

    for model in plan.model_exports:
        output_stem = sanitize_filename(model.output_name or model.model_name)
        if model.export_kind.startswith("interior") and model.unresolved_name:
            interior_unresolved_skips += 1
            report.interior_diagnostics.append(
                f"{archive_name}: skipped {model.output_name}: no IDE model name was resolved"
            )
            if on_model_done is not None:
                on_model_done(archive_name, model, False)
            continue
        if model.export_kind.startswith("interior") and output_stem in exported_output_names:
            interior_conflict_skips += 1
            report.interior_diagnostics.append(
                f"{archive_name}: skipped {model.output_name}: canonical output name {output_stem} was already exported"
            )
            if on_model_done is not None:
                on_model_done(archive_name, model, False)
            continue
        if not model.placements:
            if on_model_done is not None:
                on_model_done(archive_name, model, False)
            continue
        clusters_consistent, cluster_signatures = _model_clusters_are_consistent(model, ipl_summary=ipl_summary)
        if not clusters_consistent:
            summary = ", ".join(
                f"cluster {cluster_index}: {len(signature)} localized transform(s)"
                for cluster_index, signature in cluster_signatures
            )
            report.streamed_diagnostics.append(
                f"{archive_name}: {model.output_name} has alternate placement cluster disagreement after localization ({summary}); exporting primary cluster only"
            )
        _log(
            f"[streamed] {archive_name} model {model.model_name}: "
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
        face_materials: list[str] = []
        seen_fragments: set[tuple[int, int, int]] = set()
        used_resource_origins: set[str] = set()
        used_source_kinds: set[str] = set()
        recovered_only_from_hidden = False
        bad_fragments_for_model = 0
        vertex_colors: list[tuple[int, int, int, int]] = []
        localized_bounds_counts_by_res_id: dict[int, dict[tuple[float, ...], int]] = {}
        reported_multi_variant_res_ids: set[int] = set()

        base_inverse, base_placement = choose_base_transform(visible_placements or hidden_placements)
        if base_placement is None:
            report.streamed_diagnostics.append(
                f"{archive_name}: all placement matrices for {model.model_name} were degenerate; used identity fallback"
            )
        elif base_placement is not (visible_placements or hidden_placements)[0]:
            report.streamed_diagnostics.append(
                f"{archive_name}: skipped degenerate base placement for {model.model_name}; "
                f"used res_id={base_placement.res_id} from sector={base_placement.sector_id}"
            )
        exact_inverse, exact_linked_id = _resolve_single_linked_inverse(visible_placements or hidden_placements, ipl_summary)
        if exact_inverse is not None:
            base_inverse = exact_inverse
            if model.export_kind.startswith("interior"):
                report.interior_diagnostics.append(
                    f"{archive_name}: {model.output_name} used exact linked entity 0x{exact_linked_id:X} transform"
                )
            else:
                report.streamed_diagnostics.append(
                    f"{archive_name}: {model.output_name} used exact linked entity 0x{exact_linked_id:X} transform"
                )

        for set_index, placements in enumerate(placement_sets):
            fallback_mode = set_index > 0
            before_faces = len(faces)
            for placement in placements:
                raw_variants = archive.resource_variants_for_res_id(placement.res_id)
                if not raw_variants:
                    no_resource_res_ids += 1
                    missing_res_ids += 1
                    continue
                try:
                    decoded_variants = archive.decoded_variants_for_res_id(placement.res_id)
                except Exception:
                    decoded_variants = []
                if not decoded_variants:
                    decode_failed_res_ids += 1
                    missing_res_ids += 1
                    continue
                local_matrix = base_inverse @ rw_matrix(placement.matrix)
                candidate_rows: list[
                    tuple[int, int, int, int, DecodedResourceVariant, list[tuple[float, float, float]], tuple[float, ...]]
                ] = []
                decoded_signatures: set[tuple[str, tuple[float, ...]]] = set()
                signature_counts = localized_bounds_counts_by_res_id.setdefault(placement.res_id, {})
                dominant_signature = None
                dominant_count = 0
                for signature, count in signature_counts.items():
                    if count > dominant_count:
                        dominant_signature = signature
                        dominant_count = count
                for decoded_variant in decoded_variants:
                    transformed_vertices = [
                        transform_point(local_matrix, vertex)
                        for vertex in decoded_variant.geometry.vertices
                    ]
                    if not _fragment_vertices_valid(transformed_vertices):
                        continue
                    bounds_signature = _vertex_bounds_signature(transformed_vertices)
                    decoded_signatures.add((decoded_variant.variant.origin, bounds_signature))
                    candidate_rows.append(
                        (
                            1 if dominant_signature is not None and bounds_signature == dominant_signature else 0,
                            -RESOURCE_ORIGIN_PRIORITY.get(decoded_variant.variant.origin, 99),
                            len(decoded_variant.geometry.faces),
                            len(decoded_variant.geometry.vertices),
                            decoded_variant,
                            transformed_vertices,
                            bounds_signature,
                        )
                    )
                if len(decoded_signatures) > 1 and placement.res_id not in reported_multi_variant_res_ids:
                    reported_multi_variant_res_ids.add(placement.res_id)
                    report.streamed_diagnostics.append(
                        f"{archive_name}: {model.model_name} res_id={placement.res_id} has {len(decoded_signatures)} decoded streamed variants"
                    )
                if not candidate_rows:
                    skipped_bad_fragments += 1
                    bad_fragments_for_model += 1
                    report.streamed_diagnostics.append(
                        f"{archive_name}: skipped corrupt fragment for {model.model_name} "
                        f"(res_id={placement.res_id}, sector={placement.sector_id}, pass={placement.pass_index}, "
                        f"source={placement.source_kind})"
                    )
                    continue
                _match_dominant, _origin_rank, _face_count, _vert_count, selected_variant, transformed_vertices, bounds_signature = max(
                    candidate_rows,
                    key=lambda item: (item[0], item[1], item[2], item[3]),
                )
                signature_counts[bounds_signature] = signature_counts.get(bounds_signature, 0) + 1
                geometry = selected_variant.geometry
                fragment_key = (
                    placement.res_id,
                    *_variant_cache_key(selected_variant.variant),
                    *bounds_signature,
                )
                if fragment_key in seen_fragments:
                    continue
                seen_fragments.add(fragment_key)
                base_index = len(vertices)
                vertices.extend(transformed_vertices)
                uvs.extend(geometry.uvs)
                vertex_colors.extend(geometry.vertex_colors)
                used_resource_origins.add(geometry.resource_origin)
                used_source_kinds.add(placement.source_kind)
                for face_index, (a, b, c) in enumerate(geometry.faces):
                    faces.append((base_index + a, base_index + b, base_index + c))
                    texture_res_id = geometry.face_texture_res_ids[face_index]
                    texture = _unwrap_texture_result(archive.texture_for_res_id(texture_res_id)) if texture_res_id >= 0 else None
                    texture_name = texture.name if texture is not None else ""
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
            if faces and fallback_mode:
                recovered_only_from_hidden = True
            if faces:
                break

        if faces:
            mesh = MeshData(vertices=vertices, faces=faces, uvs=uvs, face_materials=face_materials, vertex_colors=vertex_colors)
            stem = output_stem
            if not _mesh_vertices_finite(vertices):
                dff_failed_models += 1
                report.streamed_diagnostics.append(
                    f"{archive_name}: skipped DFF export for {model.model_name}: non-finite or extreme vertex coordinates"
                )
                if on_model_done is not None:
                    on_model_done(archive_name, model, False)
                continue
            if bad_fragments_for_model:
                salvaged_models += 1
                report.streamed_diagnostics.append(
                    f"{archive_name}: salvaged {model.model_name} after dropping {bad_fragments_for_model} corrupt fragment(s)"
                )
            try:
                write_dff_from_mesh(mesh, archive_dir / f"{stem}.dff", stem)
            except Exception as exc:
                dff_failed_models += 1
                report.streamed_diagnostics.append(
                    f"{archive_name}: DFF export failed for {model.model_name}: {exc}"
                )
                if on_model_done is not None:
                    on_model_done(archive_name, model, False)
                continue
            try:
                write_col_from_mesh(mesh, archive_dir / f"{stem}.col", model_id=model.placements[0].res_id)
            except Exception as exc:
                report.streamed_diagnostics.append(
                    f"{archive_name}: collision export failed for {model.model_name}: {exc}"
                )
            exported_models += 1
            exported_output_names.add(output_stem)
            if model.has_hidden_alternates and visible_placements and hidden_placements:
                models_with_hidden_conflicts += 1
                report.streamed_diagnostics.append(
                    f"{archive_name}: kept default-visible fragments for {model.model_name}; hidden alternates were left as fallback"
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
            _log(f"[streamed] wrote {archive_name}/{stem}.dff + .col")
            if on_model_done is not None:
                on_model_done(archive_name, model, True)
        else:
            report.streamed_diagnostics.append(
                f"{archive_name}: no geometry decoded for {model.model_name} from {len(model.placements)} candidate resource ids"
            )
            if on_model_done is not None:
                on_model_done(archive_name, model, False)

    for txd_name, textures in textures_by_txd.items():
        if textures:
            if txd_name.lower() == "knackers":
                continue
            write_txd_from_decoded_textures(
                archive_dir / f"{sanitize_filename(txd_name)}.txd",
                list(textures.values()),
                dxt_level=dxt_level,
            )
            _log(f"[streamed] wrote {archive_name}/{sanitize_filename(txd_name)}.txd")

    if global_knackers_textures is None and knackers_textures:
        write_txd_from_decoded_textures(
            output_root / "knackers.txd",
            list(knackers_textures.values()),
            dxt_level=dxt_level,
        )
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
        "interior_conflict_skips": interior_conflict_skips,
        "interior_unresolved_skips": interior_unresolved_skips,
        "exported_txds": sum(1 for txd_name, textures in textures_by_txd.items() if textures and txd_name.lower() != "knackers"),
        "decoded_textures": sum(1 for texture in archive.texture_cache.values() if texture is not None),
        "loaded_master_resource_blobs": sum(len(variants) for variants in archive.master_variants_by_res_id.values()),
        "loaded_area_resource_blobs": sum(len(variants) for variants in archive.area_variants_by_res_id.values()),
        "loaded_overlay_resource_blobs": sum(len(variants) for variants in archive.overlay_variants_by_res_id.values()),
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
