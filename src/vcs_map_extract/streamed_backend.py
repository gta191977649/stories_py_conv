from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import ReportData, StreamedArchivePlan, StreamedPlacement
from .pure_backend import DecodedTexture, MeshData, write_col_from_mesh, write_dff_from_mesh, write_txd_from_decoded_textures
from .streamed_world import LevelChunk, parse_area_resource_table
from .utils import sanitize_filename


UNPACK = 0x6C018000
STMASK = 0x20000000
STROW = 0x30000000
MSCAL = 0x14000006


def _log(message: str) -> None:
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
    resource_origin: str


class LVZArchive:
    def __init__(self, archive_name: str, lvz_path: Path, img_path: Path) -> None:
        self.archive_name = archive_name
        self.level = LevelChunk.from_archive(lvz_path.parent, archive_name)
        self.master_raw_by_res_id = self._load_master_resources()
        self.area_raw_by_res_id = self._load_area_resources()
        self.overlay_raw_variants_by_res_id = self._load_sector_overlay_resources()
        self.texture_cache: dict[int, DecodedTexture | None] = {}
        self.geometry_cache: dict[int, ParsedStreamedGeometry | None] = {}

    def _load_master_resources(self) -> dict[int, bytes]:
        pointers = self.level.read_master_resource_pointers()
        ordered = sorted((ptr, res_id) for res_id, ptr in pointers.items())
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

    def texture_for_res_id(self, res_id: int) -> DecodedTexture | None:
        if res_id not in self.texture_cache:
            decoded = None
            for blob, _origin in self.resource_blobs_for_res_id(res_id):
                decoded = self._decode_texture_blob(blob, res_id)
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
            for group in groups:
                for strip in group.strips:
                    strip.material_res_index = mesh.texture_id
                    strip.uvs = [(u * mesh.u_scale, v * mesh.v_scale) for u, v in strip.uvs]
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
                        face_texture_res_ids.append(mesh.texture_id)

        if not faces:
            return None
        return ParsedStreamedGeometry(vertices, faces, uvs, face_texture_res_ids, origin)

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
        face_texture_res_ids: list[int] = []
        for group in groups:
            for strip in group.strips:
                count = min(strip.count, len(strip.verts), len(strip.uvs))
                if count < 3:
                    continue
                material_res_index = strip.material_res_index if strip.material_res_index >= 0 else -1
                base = len(vertices)
                vertices.extend(strip.verts[:count])
                uvs.extend(strip.uvs[:count])
                for index in range(count - 2):
                    if index & 1:
                        face = (base + index + 1, base + index, base + index + 2)
                    else:
                        face = (base + index, base + index + 1, base + index + 2)
                    faces.append(face)
                    face_texture_res_ids.append(material_res_index)
        if not faces:
            return None
        return ParsedStreamedGeometry(vertices, faces, uvs, face_texture_res_ids, origin)

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
            base = len(vertices)
            vertices.extend(batch_vertices)
            uvs.extend([(0.0, 0.0)] * count)
            for index in range(count - 2):
                if index & 1:
                    face = (base + index + 1, base + index, base + index + 2)
                else:
                    face = (base + index, base + index + 1, base + index + 2)
                faces.append(face)
                face_texture_res_ids.append(-1)

            next_search = align_up4(w + (count_all * 6))
            try:
                off = self._find_unpack_near(blob, next_search)
            except ValueError:
                break
            first_batch = False

        if not faces:
            return None
        return ParsedStreamedGeometry(vertices, faces, uvs, face_texture_res_ids, origin)

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
            best_transform = translation_inverse(placement.matrix)
            best_placement = placement
    if best_transform is not None:
        return best_transform, best_placement
    return np.identity(4, dtype=np.float64), None


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

    for model in plan.model_exports:
        if not model.placements:
            continue
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

        for set_index, placements in enumerate(placement_sets):
            fallback_mode = set_index > 0
            before_faces = len(faces)
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
                local_matrix = base_inverse @ rw_matrix(placement.matrix)
                transformed_vertices = [transform_point(local_matrix, vertex) for vertex in geometry.vertices]
                if not _fragment_vertices_valid(transformed_vertices):
                    skipped_bad_fragments += 1
                    bad_fragments_for_model += 1
                    report.streamed_diagnostics.append(
                        f"{archive_name}: skipped corrupt fragment for {model.model_name} "
                        f"(res_id={placement.res_id}, sector={placement.sector_id}, pass={placement.pass_index}, "
                        f"source={placement.source_kind})"
                    )
                    continue
                base_index = len(vertices)
                vertices.extend(transformed_vertices)
                uvs.extend(geometry.uvs)
                used_resource_origins.add(geometry.resource_origin)
                used_source_kinds.add(placement.source_kind)
                for face_index, (a, b, c) in enumerate(geometry.faces):
                    faces.append((base_index + a, base_index + b, base_index + c))
                    texture_res_id = geometry.face_texture_res_ids[face_index]
                    texture = archive.texture_for_res_id(texture_res_id) if texture_res_id >= 0 else None
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
            mesh = MeshData(vertices=vertices, faces=faces, uvs=uvs, face_materials=face_materials)
            stem = sanitize_filename(model.model_name)
            if not _mesh_vertices_finite(vertices):
                dff_failed_models += 1
                report.streamed_diagnostics.append(
                    f"{archive_name}: skipped DFF export for {model.model_name}: non-finite or extreme vertex coordinates"
                )
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
                continue
            try:
                write_col_from_mesh(mesh, archive_dir / f"{stem}.col", model_id=model.placements[0].res_id)
            except Exception as exc:
                report.streamed_diagnostics.append(
                    f"{archive_name}: collision export failed for {model.model_name}: {exc}"
                )
            exported_models += 1
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
        else:
            report.streamed_diagnostics.append(
                f"{archive_name}: no geometry decoded for {model.model_name} from {len(model.placements)} candidate resource ids"
            )

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
        "exported_txds": sum(1 for textures in textures_by_txd.values() if textures),
        "decoded_textures": sum(1 for texture in archive.texture_cache.values() if texture is not None),
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
