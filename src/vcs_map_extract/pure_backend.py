from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

import numpy as np

from .mathutils_compat import install_mathutils_shim
from .utils import sanitize_filename


RW_VERSION = 0x36003


@lru_cache(maxsize=1)
def load_bleeds_modules():
    install_mathutils_shim()
    from .vendor.bleeds import col2, mdl, tex

    def quiet_log(self, msg: str) -> None:
        self.debug_log.append(str(msg))

    mdl.StoriesMDLContext.log = quiet_log
    return mdl, tex, col2


@lru_cache(maxsize=1)
def load_dragonff_modules():
    from .vendor.dragonff import col, dff, txd

    return dff, txd, col


@dataclass(slots=True)
class DecodedTexture:
    name: str
    rgba: bytes
    width: int
    height: int
    has_alpha: bool


@dataclass(slots=True)
class MeshData:
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]
    uvs: list[tuple[float, float]]
    face_materials: list[str]
    vertex_colors: list[tuple[int, int, int, int]] | None = None


def _sanitize_texture_name(name: str) -> str:
    clean = "".join(char for char in name if char.isalnum() or char in "_-.")
    clean = clean[:31].strip("._")
    return clean or "texture"


def _iter_decoded_textures(input_path: Path) -> list[DecodedTexture]:
    _mdl_mod, tex_mod, _col2_mod = load_bleeds_modules()
    data = input_path.read_bytes()
    if len(data) < 0x30:
        return []

    header = {
        "glob1": tex_mod.read_u32(data, 0x0C),
        "glob2": tex_mod.read_u32(data, 0x10),
        "coll_size": tex_mod.read_u32(data, 0x08),
        "first_slot": tex_mod.read_u32(data, 0x28),
        "last_slot": tex_mod.read_u32(data, 0x2C),
    }
    first_slot = header["first_slot"]
    last_slot = header["last_slot"]
    if not first_slot:
        return []

    visited: set[int] = set()
    textures: list[tuple[str, object, int, int, int]] = []
    base = tex_mod.slot_base_from_slot_ptr(first_slot)
    last_base = tex_mod.slot_base_from_slot_ptr(last_slot) if last_slot else None

    while True:
        if base in visited:
            break
        visited.add(base)
        container = tex_mod.parse_container(data, base)
        if not container:
            break

        name = container["name"]
        if name and all(32 <= ord(char) < 127 for char in name):
            tex_off = container["tex_off"]
            header_obj = tex_mod.parse_ps2_header(data, tex_off)
            if header_obj is None:
                header_obj = tex_mod.parse_psp_header(data, tex_off)
            if header_obj is not None:
                textures.append((name, header_obj, int(header_obj.raster_offset), int(base), int(tex_off)))

        next_slot = container["next_slot"]
        if next_slot == 0:
            break
        next_base = tex_mod.slot_base_from_slot_ptr(next_slot)
        if next_base == base or (last_base is not None and base == last_base):
            break
        base = next_base

    if not textures:
        return []

    textures.sort(key=lambda item: item[2])
    offsets = [offset for _name, _header_obj, offset, _base, _tex_off in textures]
    boundaries = {len(data), header["glob1"], header["glob2"], header["coll_size"]}
    for _name, _header_obj, offset, base, tex_off in textures:
        boundaries.update({offset, base, tex_off})
    block_sizes: list[int] = []
    for _name, header_obj, start, _base, _tex_off in textures:
        if isinstance(header_obj, tex_mod.Ps2TexHeader):
            minimum_size = max(
                0,
                ((header_obj.width * header_obj.height * header_obj.bpp) + 7) // 8
                + (16 * 4 if header_obj.bpp == 4 else 256 * 4 if header_obj.bpp == 8 else 0),
            )
        else:
            minimum_size = max(
                0,
                ((header_obj.width * header_obj.height * header_obj.bpp) + 7) // 8
                + (16 * 4 if header_obj.bpp == 4 else 256 * 4 if header_obj.bpp == 8 else 0),
            )
        candidates = [candidate for candidate in boundaries if candidate >= start + minimum_size]
        if not candidates:
            candidates = [candidate for candidate in boundaries if candidate > start]
        end = min(candidates) if candidates else len(data)
        block_sizes.append(max(0, end - start))

    decoded: list[DecodedTexture] = []
    for (name, header_obj, _offset, _base, _tex_off), block_size in zip(textures, block_sizes, strict=True):
        if isinstance(header_obj, tex_mod.Ps2TexHeader):
            rgba_array = tex_mod.decode_ps2_texture(
                data,
                header_obj,
                block_size,
                palette_override=None,
                four_bit_high_nibble_first=False,
            )
        else:
            rgba_array = tex_mod.decode_psp_texture(data, header_obj, block_size, palette_override=None)
        if rgba_array is None:
            continue
        height, width, _channels = rgba_array.shape
        has_alpha = bool(np.any(np.asarray(rgba_array, dtype=np.uint8)[:, :, 3] != 255))
        decoded.append(
            DecodedTexture(
                name=_sanitize_texture_name(name),
                rgba=np.asarray(rgba_array, dtype=np.uint8).tobytes(),
                width=width,
                height=height,
                has_alpha=has_alpha,
            )
        )
    return decoded


def _make_txd_native(texture: DecodedTexture):
    _dragon_dff, dragon_txd, _dragon_col = load_dragonff_modules()
    rgba = bytes(texture.rgba)
    has_alpha = texture.has_alpha
    native = dragon_txd.TextureNative()
    native.platform_id = _dragon_dff.NativePlatformType.D3D9
    native.filter_mode = 0x06
    native.uv_addressing = 0b00010001
    native.name = texture.name
    native.mask = ""
    native.width = texture.width
    native.height = texture.height
    native.num_levels = 1
    native.raster_type = 4
    native.platform_properties = SimpleNamespace(
        alpha=has_alpha,
        cube_texture=False,
        auto_mipmaps=False,
        compressed=False,
    )
    native.palette = b""
    if has_alpha:
        native.raster_format_flags = dragon_txd.RasterFormat.RASTER_8888 << 8
        native.d3d_format = dragon_txd.D3DFormat.D3D_8888
        native.depth = 32
        native.pixels = [dragon_txd.ImageEncoder.rgba_to_bgra8888(rgba)]
    else:
        native.raster_format_flags = dragon_txd.RasterFormat.RASTER_565 << 8
        native.d3d_format = dragon_txd.D3DFormat.D3D_565
        native.depth = 16
        native.pixels = [dragon_txd.ImageEncoder.rgba_to_bgra565(rgba)]
    return native


def write_txd_from_decoded_textures(output_path: Path, textures: list[DecodedTexture]) -> list[str]:
    _dragon_dff, dragon_txd, _dragon_col = load_dragonff_modules()
    txd_file = dragon_txd.txd()
    txd_file.device_id = dragon_txd.DeviceType.DEVICE_D3D9
    txd_file.native_textures = [_make_txd_native(texture) for texture in textures]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(txd_file.write_memory(RW_VERSION))
    return [texture.name for texture in textures]


def write_txd(input_path: Path, output_path: Path) -> list[str]:
    return write_txd_from_decoded_textures(output_path, _iter_decoded_textures(input_path))


def _vector_add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vector_sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vector_cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _vector_normalize(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = (value[0] ** 2 + value[1] ** 2 + value[2] ** 2) ** 0.5
    if length <= 1e-8:
        return (0.0, 0.0, 1.0)
    return (value[0] / length, value[1] / length, value[2] / length)


def _calculate_normals(
    vertices: list[tuple[float, float, float]],
    faces: Iterable[tuple[int, int, int]],
) -> list[tuple[float, float, float]]:
    if not vertices:
        return []
    face_array = np.asarray(list(faces), dtype=np.int32)
    if face_array.size == 0:
        return [(0.0, 0.0, 1.0)] * len(vertices)
    vertex_array = np.asarray(vertices, dtype=np.float64)
    pa = vertex_array[face_array[:, 0]]
    pb = vertex_array[face_array[:, 1]]
    pc = vertex_array[face_array[:, 2]]
    face_normals = np.cross(pb - pa, pc - pa)
    accum = np.zeros_like(vertex_array)
    np.add.at(accum, face_array[:, 0], face_normals)
    np.add.at(accum, face_array[:, 1], face_normals)
    np.add.at(accum, face_array[:, 2], face_normals)
    lengths = np.linalg.norm(accum, axis=1)
    normals = np.zeros_like(accum)
    valid = lengths > 1e-8
    if np.any(valid):
        normals[valid] = accum[valid] / lengths[valid, None]
    if np.any(~valid):
        normals[~valid] = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    return [tuple(normal) for normal in normals.tolist()]


def _calculate_bounds(vertices: list[tuple[float, float, float]]) -> tuple[tuple[float, float, float], float]:
    if not vertices:
        return (0.0, 0.0, 0.0), 0.0
    vertex_array = np.asarray(vertices, dtype=np.float64)
    center = vertex_array.mean(axis=0)
    radius = float(np.max(np.linalg.norm(vertex_array - center, axis=1)))
    return (float(center[0]), float(center[1]), float(center[2])), radius


def _make_materials(geo):
    dragon_dff, _dragon_txd, _dragon_col = load_dragonff_modules()
    material_count = max(
        len(getattr(geo, "materials", [])),
        max((getattr(part, "material_id", 0) for part in geo.parts), default=-1) + 1,
        1,
    )
    materials = []
    for index in range(material_count):
        mat = dragon_dff.Material()
        mat.flags = 0
        mat.color = dragon_dff.RGBA(255, 255, 255, 255)
        mat.surface_properties = dragon_dff.GeomSurfPro(1.0, 1.0, 1.0)
        mat.textures = []
        if index < len(geo.materials):
            desc = geo.materials[index]
            rgba = int(getattr(desc, "rgba", 0xFFFFFFFF))
            mat.color = dragon_dff.RGBA(
                rgba & 0xFF,
                (rgba >> 8) & 0xFF,
                (rgba >> 16) & 0xFF,
                (rgba >> 24) & 0xFF,
            )
            texture_name = getattr(desc, "texture", "")
            if texture_name:
                texture = dragon_dff.Texture()
                texture.filters = 0x06
                texture.uv_addressing = 0b00010001
                texture.name = _sanitize_texture_name(texture_name)
                texture.mask = ""
                mat.textures.append(texture)
        materials.append(mat)
    return materials


def _invert_matrix4(matrix: object) -> tuple[tuple[float, float, float, float], ...]:
    array = np.asarray(tuple(tuple(float(value) for value in row) for row in matrix), dtype=np.float64)
    inverted = np.linalg.inv(array)
    return tuple(tuple(float(value) for value in row) for row in inverted.tolist())


def _matrix_to_frame_components(matrix: object):
    dragon_dff, _dragon_txd, _dragon_col = load_dragonff_modules()
    rows = tuple(tuple(float(value) for value in row) for row in matrix)
    return (
        dragon_dff.Matrix(
            dragon_dff.Vector(rows[0][0], rows[1][0], rows[2][0]),
            dragon_dff.Vector(rows[0][1], rows[1][1], rows[2][1]),
            dragon_dff.Vector(rows[0][2], rows[1][2], rows[2][2]),
        ),
        dragon_dff.Vector(rows[0][3], rows[1][3], rows[2][3]),
    )


def _collect_part_skin(part) -> tuple[list[list[int]], list[list[float]]]:
    skin_indices = [[0, 0, 0, 0] for _ in part.verts]
    skin_weights = [[0.0, 0.0, 0.0, 0.0] for _ in part.verts]
    for strip in getattr(part, "strips_meta", []):
        for vertex_offset in range(min(strip.vertex_count, len(strip.skin_indices), len(strip.skin_weights))):
            vertex_index = strip.base_vertex_index + vertex_offset
            if not (0 <= vertex_index < len(skin_indices)):
                continue
            indices = list(strip.skin_indices[vertex_offset][:4])
            weights = list(strip.skin_weights[vertex_offset][:4])
            while len(indices) < 4:
                indices.append(0)
            while len(weights) < 4:
                weights.append(0.0)
            total_weight = sum(float(weight) for weight in weights)
            if total_weight > 1e-8:
                weights = [float(weight) / total_weight for weight in weights]
            skin_indices[vertex_index] = [int(index) for index in indices[:4]]
            skin_weights[vertex_index] = [float(weight) for weight in weights[:4]]
    return skin_indices, skin_weights


def _context_has_skin(ctx) -> bool:
    if getattr(ctx.atomic, "hierarchy_bones", None):
        return True
    ps2_geo = ctx.atomic.ps2_geometry
    for part in getattr(ps2_geo, "parts", []):
        for strip in getattr(part, "strips_meta", []):
            if getattr(strip, "skin_indices", None):
                return True
    psp_geo = ctx.atomic.psp_geometry
    return any(getattr(mesh, "bone_indices", None) for mesh in getattr(psp_geo, "meshes", []))


def _default_ped_bone_records(mdl_mod, armature) -> list[tuple[int, int, int, str, int]]:
    name_to_ptr = {
        mdl_mod.canon_frame_name(name): ptr
        for ptr, name in armature.frame_names.items()
    }
    records: list[tuple[int, int, int, str, int]] = []
    for index, canonical_name in enumerate(mdl_mod.commonBoneOrderVCS):
        ptr = name_to_ptr.get(canonical_name)
        if ptr is None:
            continue
        records.append(
            (
                int(mdl_mod.kamBoneIDVCS[index]),
                index,
                int(mdl_mod.kamBoneTypeVCS[index]),
                canonical_name,
                ptr,
            )
        )
    return records


def _ped_bone_records(ctx, mdl_mod) -> list[tuple[int, int, int, str, int]]:
    armature = ctx.atomic.armature
    name_to_ptr = {
        mdl_mod.canon_frame_name(name): ptr
        for ptr, name in armature.frame_names.items()
    }
    hierarchy_bones = list(getattr(ctx.atomic, "hierarchy_bones", []))
    records: list[tuple[int, int, int, str, int]] = []
    for bone in hierarchy_bones:
        if 0 <= bone.index < len(mdl_mod.commonBoneOrderVCS):
            canonical_name = mdl_mod.commonBoneOrderVCS[bone.index]
        else:
            canonical_name = ""
        ptr = name_to_ptr.get(canonical_name)
        if ptr is None:
            continue
        records.append((int(bone.id), int(bone.index), int(bone.type), canonical_name, ptr))
    return records or _default_ped_bone_records(mdl_mod, armature)


def _skin_inverse_matrices(ctx, bone_records: list[tuple[int, int, int, str, int]]) -> list[tuple[tuple[float, float, float, float], ...]]:
    matrices = list(getattr(ctx.atomic, "skin_inverse_matrices", []))
    if len(matrices) == len(bone_records):
        return [
            tuple(tuple(float(value) for value in row) for row in matrix)
            for matrix in matrices
        ]
    armature = ctx.atomic.armature
    return [_invert_matrix4(armature.frame_mats_world[ptr]) for _bone_id, _index, _type, _name, ptr in bone_records]


def _write_skinned_clump(ctx, output_path: Path, frame_name: str) -> None:
    mdl_mod, _tex_mod, _col2_mod = load_bleeds_modules()
    dragon_dff, _dragon_txd, _dragon_col = load_dragonff_modules()

    bone_records = _ped_bone_records(ctx, mdl_mod)
    if not bone_records:
        raise ValueError("No ped hierarchy frames were resolved")

    armature = ctx.atomic.armature
    frame_ptrs = [ptr for _bone_id, _index, _type, _name, ptr in bone_records]
    frame_index_by_ptr = {ptr: index for index, ptr in enumerate(frame_ptrs)}

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    uvs: list[tuple[float, float]] = []
    face_materials: list[str] = []
    vertex_colors: list[tuple[int, int, int, int]] = []
    vertex_bone_indices: list[list[int]] = []
    vertex_bone_weights: list[list[float]] = []

    ps2_geo = ctx.atomic.ps2_geometry
    if ps2_geo.parts:
        for part in ps2_geo.parts:
            base_index = len(vertices)
            vertices.extend(tuple(map(float, vertex)) for vertex in part.verts)

            part_uvs = list(getattr(part, "uvs", []))
            for vertex_index in range(len(part.verts)):
                if vertex_index < len(part_uvs):
                    u, v = part_uvs[vertex_index]
                else:
                    u, v = 0.0, 0.0
                uvs.append((float(u), float(v)))

            part_colors = list(getattr(part, "vertex_colors", []))
            for vertex_index in range(len(part.verts)):
                if vertex_index < len(part_colors):
                    r, g, b, a = part_colors[vertex_index]
                    vertex_colors.append((int(r), int(g), int(b), int(a)))
                else:
                    vertex_colors.append((255, 255, 255, 255))

            part_skin_indices, part_skin_weights = _collect_part_skin(part)
            for indices, weights in zip(part_skin_indices, part_skin_weights, strict=True):
                if sum(weights) <= 1e-8:
                    vertex_bone_indices.append([0, 0, 0, 0])
                    vertex_bone_weights.append([1.0, 0.0, 0.0, 0.0])
                else:
                    vertex_bone_indices.append([int(index) for index in indices[:4]])
                    vertex_bone_weights.append([float(weight) for weight in weights[:4]])

            material_index = int(getattr(part, "material_id", 0))
            material_name = (
                getattr(ps2_geo.materials[material_index], "texture", "")
                if material_index < len(ps2_geo.materials)
                else ""
            )
            for face in part.faces:
                a, b, c = (base_index + int(face[0]), base_index + int(face[1]), base_index + int(face[2]))
                faces.append((a, b, c))
                face_materials.append(material_name)
    else:
        psp_geo = ctx.atomic.psp_geometry
        for mesh in psp_geo.meshes:
            base_index = len(vertices)
            vertices.extend(tuple(map(float, vertex)) for vertex in mesh.verts)

            mesh_uvs = list(getattr(mesh, "uvs", []))
            for vertex_index in range(len(mesh.verts)):
                if vertex_index < len(mesh_uvs):
                    u, v = mesh_uvs[vertex_index]
                else:
                    u, v = 0.0, 0.0
                uvs.append((float(u), float(v)))

            mesh_colors = list(getattr(mesh, "colors", []))
            for vertex_index in range(len(mesh.verts)):
                if vertex_index < len(mesh_colors):
                    r, g, b, a = mesh_colors[vertex_index]
                    vertex_colors.append((int(r), int(g), int(b), int(a)))
                else:
                    vertex_colors.append((255, 255, 255, 255))

            mesh_indices = list(getattr(mesh, "bone_indices", []))
            mesh_weights = list(getattr(mesh, "bone_weights", []))
            for vertex_index in range(len(mesh.verts)):
                indices = list(mesh_indices[vertex_index][:4]) if vertex_index < len(mesh_indices) else [0, 0, 0, 0]
                weights = list(mesh_weights[vertex_index][:4]) if vertex_index < len(mesh_weights) else [1.0, 0.0, 0.0, 0.0]
                while len(indices) < 4:
                    indices.append(0)
                while len(weights) < 4:
                    weights.append(0.0)
                total_weight = sum(float(weight) for weight in weights)
                if total_weight <= 1e-8:
                    vertex_bone_indices.append([0, 0, 0, 0])
                    vertex_bone_weights.append([1.0, 0.0, 0.0, 0.0])
                else:
                    vertex_bone_indices.append([int(index) for index in indices[:4]])
                    vertex_bone_weights.append([float(weight) / total_weight for weight in weights[:4]])

            material_index = int(getattr(mesh, "mat_id", 0))
            material_name = (
                getattr(psp_geo.materials[material_index], "texture", "")
                if material_index < len(psp_geo.materials)
                else ""
            )
            for face in mesh.faces:
                a, b, c = (base_index + int(face[0]), base_index + int(face[1]), base_index + int(face[2]))
                faces.append((a, b, c))
                face_materials.append(material_name)

    if not vertices or not faces:
        raise ValueError(f"No skinned geometry data available for {output_path}")

    normals = _calculate_normals(vertices, faces)
    (cx, cy, cz), radius = _calculate_bounds(vertices)

    material_names: list[str] = []
    seen_material_names: set[str] = set()
    for name in face_materials:
        if not name or name in seen_material_names:
            continue
        seen_material_names.add(name)
        material_names.append(name)
    material_slot = {name: index for index, name in enumerate(material_names)}
    materials = []
    for name in material_names:
        mat = dragon_dff.Material()
        mat.flags = 0
        mat.color = dragon_dff.RGBA(255, 255, 255, 255)
        mat.surface_properties = dragon_dff.GeomSurfPro(1.0, 1.0, 1.0)
        texture = dragon_dff.Texture()
        texture.filters = 0x06
        texture.uv_addressing = 0b00010001
        texture.name = _sanitize_texture_name(name)
        texture.mask = ""
        mat.textures = [texture]
        materials.append(mat)

    geometry = dragon_dff.Geometry()
    geometry.surface_properties = dragon_dff.GeomSurfPro(1.0, 1.0, 1.0)
    geometry.vertices = [dragon_dff.Vector(*vertex) for vertex in vertices]
    geometry.normals = [dragon_dff.Vector(*normal) for normal in normals]
    geometry.uv_layers = [[dragon_dff.TexCoords(float(u), float(v)) for u, v in uvs]]
    geometry.prelit_colors = [dragon_dff.RGBA(*color) for color in vertex_colors]
    geometry.triangles = [
        dragon_dff.Triangle(b, a, material_slot.get(face_materials[index], 0), c)
        for index, (a, b, c) in enumerate(faces)
    ]
    geometry.materials = materials or _make_materials(SimpleNamespace(materials=[], parts=[SimpleNamespace(material_id=0)]))
    geometry.bounding_sphere = dragon_dff.Sphere(cx, cy, cz, radius)

    skin = dragon_dff.SkinPLG()
    skin.num_bones = len(bone_records)
    skin.vertex_bone_indices = vertex_bone_indices
    skin.vertex_bone_weights = vertex_bone_weights
    skin.bone_matrices = _skin_inverse_matrices(ctx, bone_records)
    geometry.extensions["skin"] = skin

    root_bone_id = int(bone_records[0][0])
    root_frame_index = 0

    dff_file = dragon_dff.dff()
    for bone_list_index, (bone_id, bone_index, bone_type, _canonical_name, ptr) in enumerate(bone_records):
        frame = dragon_dff.Frame()
        frame.rotation_matrix, frame.position = _matrix_to_frame_components(armature.frame_mats_local[ptr])
        parent_ptr = armature.frame_parent_ptrs.get(ptr, 0)
        frame.parent = frame_index_by_ptr[parent_ptr] if parent_ptr in frame_index_by_ptr else -1
        if frame.parent < 0:
            root_frame_index = bone_list_index
        frame.creation_flags = 0
        frame.name = armature.frame_names.get(ptr, frame_name)
        frame.bone_data = dragon_dff.HAnimPLG()
        frame.bone_data.header = dragon_dff.HAnimHeader(0x100, int(bone_id), len(bone_records) if bone_list_index == root_frame_index else 0)
        if bone_list_index == root_frame_index:
            frame.bone_data.bones = [
                dragon_dff.Bone(int(entry_bone_id), int(entry_index), int(entry_type))
                for entry_bone_id, entry_index, entry_type, _entry_name, _entry_ptr in bone_records
            ]
        dff_file.frame_list.append(frame)

    atomic = dragon_dff.Atomic()
    atomic.frame = root_frame_index
    atomic.geometry = 0
    atomic.flags = 0x04
    atomic.unk = 0

    dff_file.geometry_list.append(geometry)
    dff_file.atomic_list.append(atomic)
    output_path.write_bytes(dff_file.write_memory(RW_VERSION))


def write_dff_from_mesh(mesh: MeshData, output_path: Path, frame_name: str) -> None:
    dragon_dff, _dragon_txd, _dragon_col = load_dragonff_modules()
    if not mesh.vertices or not mesh.faces:
        raise ValueError(f"No mesh data available for {output_path}")

    normals = _calculate_normals(mesh.vertices, mesh.faces)
    (cx, cy, cz), radius = _calculate_bounds(mesh.vertices)

    material_names: list[str] = []
    seen_material_names: set[str] = set()
    for name in mesh.face_materials:
        if not name or name in seen_material_names:
            continue
        seen_material_names.add(name)
        material_names.append(name)
    material_slot = {name: index for index, name in enumerate(material_names)}
    materials = []
    for name in material_names:
        mat = dragon_dff.Material()
        mat.flags = 0
        mat.color = dragon_dff.RGBA(255, 255, 255, 255)
        mat.surface_properties = dragon_dff.GeomSurfPro(1.0, 1.0, 1.0)
        texture = dragon_dff.Texture()
        texture.filters = 0x06
        texture.uv_addressing = 0b00010001
        texture.name = _sanitize_texture_name(name)
        texture.mask = ""
        mat.textures = [texture]
        materials.append(mat)

    geometry = dragon_dff.Geometry()
    geometry.surface_properties = dragon_dff.GeomSurfPro(1.0, 1.0, 1.0)
    geometry.vertices = [dragon_dff.Vector(*vertex) for vertex in mesh.vertices]
    geometry.normals = [dragon_dff.Vector(*normal) for normal in normals]
    geometry.uv_layers = [[dragon_dff.TexCoords(float(u), float(v)) for u, v in mesh.uvs]]
    if mesh.vertex_colors and len(mesh.vertex_colors) == len(mesh.vertices):
        geometry.prelit_colors = [dragon_dff.RGBA(*color) for color in mesh.vertex_colors]
    geometry.triangles = [
        dragon_dff.Triangle(b, a, material_slot.get(mesh.face_materials[index], 0), c)
        for index, (a, b, c) in enumerate(mesh.faces)
    ]
    geometry.materials = materials or _make_materials(SimpleNamespace(materials=[], parts=[SimpleNamespace(material_id=0)]))
    geometry.bounding_sphere = dragon_dff.Sphere(cx, cy, cz, radius)

    frame = dragon_dff.Frame()
    frame.rotation_matrix = dragon_dff.Matrix(
        dragon_dff.Vector(1.0, 0.0, 0.0),
        dragon_dff.Vector(0.0, 1.0, 0.0),
        dragon_dff.Vector(0.0, 0.0, 1.0),
    )
    frame.position = dragon_dff.Vector(0.0, 0.0, 0.0)
    frame.parent = -1
    frame.creation_flags = 0
    frame.name = frame_name

    atomic = dragon_dff.Atomic()
    atomic.frame = 0
    atomic.geometry = 0
    atomic.flags = 0x04
    atomic.unk = 0

    dff_file = dragon_dff.dff()
    dff_file.frame_list.append(frame)
    dff_file.geometry_list.append(geometry)
    dff_file.atomic_list.append(atomic)
    output_path.write_bytes(dff_file.write_memory(RW_VERSION))


def write_empty_dff(output_path: Path, frame_name: str) -> None:
    dragon_dff, _dragon_txd, _dragon_col = load_dragonff_modules()
    frame = dragon_dff.Frame()
    frame.rotation_matrix = dragon_dff.Matrix(
        dragon_dff.Vector(1.0, 0.0, 0.0),
        dragon_dff.Vector(0.0, 1.0, 0.0),
        dragon_dff.Vector(0.0, 0.0, 1.0),
    )
    frame.position = dragon_dff.Vector(0.0, 0.0, 0.0)
    frame.parent = -1
    frame.creation_flags = 0
    frame.name = frame_name

    dff_file = dragon_dff.dff()
    dff_file.frame_list.append(frame)
    output_path.write_bytes(dff_file.write_memory(RW_VERSION))


def _read_ps2_mdl_context(input_path: Path):
    mdl_mod, _tex_mod, _col2_mod = load_bleeds_modules()
    attempts: list[str] = []
    candidates: list[tuple[int, object]] = []
    for mdl_type in ("SIM", "PED"):
        try:
            ctx = mdl_mod.read_stories_mdl(str(input_path), "PS2", mdl_type)
        except Exception as exc:
            attempts.append(f"{mdl_type}: {exc}")
            continue
        ps2_geo = ctx.atomic.ps2_geometry
        psp_geo = ctx.atomic.psp_geometry
        if ps2_geo.parts or psp_geo.meshes:
            score = 0
            armature = getattr(ctx.atomic, "armature", SimpleNamespace(frame_names={}))
            if getattr(ctx, "mdl_type", "") == "PED":
                score += 10
            if _context_has_skin(ctx):
                score += 100
            score += len(getattr(armature, "frame_names", {}))
            score += len(getattr(ctx.atomic, "hierarchy_bones", []))
            candidates.append((score, ctx))
            continue
        attempts.append(f"{mdl_type}: no geometry parts or meshes")
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    details = "; ".join(attempts) if attempts else "no parser attempts made"
    raise ValueError(f"No PS2 geometry parts or PSP meshes decoded from {input_path} ({details})")


def write_dff(input_path: Path, output_path: Path) -> None:
    frame_name = sanitize_filename(input_path.stem)
    try:
        ctx = _read_ps2_mdl_context(input_path)
    except ValueError:
        # Preserve archive completeness for unsupported or non-renderable MDLs.
        write_empty_dff(output_path, frame_name)
        return

    if getattr(ctx, "mdl_type", "") == "PED" and _context_has_skin(ctx):
        try:
            _write_skinned_clump(ctx, output_path, frame_name)
            return
        except Exception:
            # Fall back to static export if skeletal conversion still fails.
            pass

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_materials: list[str] = []
    uvs: list[tuple[float, float]] = []
    vertex_colors: list[tuple[int, int, int, int]] = []

    ps2_geo = ctx.atomic.ps2_geometry
    if ps2_geo.parts:
        for part in ps2_geo.parts:
            base_index = len(vertices)
            part_vertices = [tuple(map(float, vertex)) for vertex in part.verts]
            vertices.extend(part_vertices)

            part_uvs = list(getattr(part, "uvs", []))
            for vertex_index in range(len(part_vertices)):
                if vertex_index < len(part_uvs):
                    u, v = part_uvs[vertex_index]
                else:
                    u, v = 0.0, 0.0
                # PS2 MDL UVs already match the reference DFF convention.
                uvs.append((float(u), float(v)))

            part_colors = list(getattr(part, "vertex_colors", []))
            for vertex_index in range(len(part_vertices)):
                if vertex_index < len(part_colors):
                    r, g, b, a = part_colors[vertex_index]
                    vertex_colors.append((int(r), int(g), int(b), int(a)))
                else:
                    vertex_colors.append((255, 255, 255, 255))

            material_index = int(getattr(part, "material_id", 0))
            material_name = (
                getattr(ps2_geo.materials[material_index], "texture", "")
                if material_index < len(ps2_geo.materials)
                else ""
            )
            for face in part.faces:
                a, b, c = (base_index + int(face[0]), base_index + int(face[1]), base_index + int(face[2]))
                faces.append((a, b, c))
                face_materials.append(material_name)
    else:
        psp_geo = ctx.atomic.psp_geometry
        for mesh in psp_geo.meshes:
            base_index = len(vertices)
            mesh_vertices = [tuple(map(float, vertex)) for vertex in mesh.verts]
            vertices.extend(mesh_vertices)

            mesh_uvs = list(getattr(mesh, "uvs", []))
            for vertex_index in range(len(mesh_vertices)):
                if vertex_index < len(mesh_uvs):
                    u, v = mesh_uvs[vertex_index]
                else:
                    u, v = 0.0, 0.0
                uvs.append((float(u), float(v)))

            mesh_colors = list(getattr(mesh, "colors", []))
            for vertex_index in range(len(mesh_vertices)):
                if vertex_index < len(mesh_colors):
                    r, g, b, a = mesh_colors[vertex_index]
                    vertex_colors.append((int(r), int(g), int(b), int(a)))
                else:
                    vertex_colors.append((255, 255, 255, 255))

            material_index = int(getattr(mesh, "mat_id", 0))
            material_name = (
                getattr(psp_geo.materials[material_index], "texture", "")
                if material_index < len(psp_geo.materials)
                else ""
            )
            for face in mesh.faces:
                a, b, c = (base_index + int(face[0]), base_index + int(face[1]), base_index + int(face[2]))
                faces.append((a, b, c))
                face_materials.append(material_name)

    mesh = MeshData(
        vertices=vertices,
        faces=faces,
        uvs=uvs,
        face_materials=face_materials,
        vertex_colors=vertex_colors,
    )
    write_dff_from_mesh(mesh, output_path, frame_name)


def _build_box_mesh(aabb_min: tuple[float, float, float, float], aabb_max: tuple[float, float, float, float]):
    min_x, min_y, min_z, _ = aabb_min
    max_x, max_y, max_z, _ = aabb_max
    verts = [
        (min_x, min_y, min_z),
        (max_x, min_y, min_z),
        (max_x, max_y, min_z),
        (min_x, max_y, min_z),
        (min_x, min_y, max_z),
        (max_x, min_y, max_z),
        (max_x, max_y, max_z),
        (min_x, max_y, max_z),
    ]
    faces = [
        (0, 1, 2), (0, 2, 3),
        (4, 6, 5), (4, 7, 6),
        (0, 5, 1), (0, 4, 5),
        (1, 6, 2), (1, 5, 6),
        (2, 7, 3), (2, 6, 7),
        (3, 4, 0), (3, 7, 4),
    ]
    return verts, faces


def _shift_vertices(
    vertices: list[tuple[float, float, float]],
    origin: tuple[float, float, float],
) -> list[tuple[float, float, float]]:
    if not vertices:
        return []
    shifted = np.asarray(vertices, dtype=np.float64) - np.asarray(origin, dtype=np.float64)
    return [tuple(vertex) for vertex in shifted.tolist()]


def write_col(input_path: Path, output_path: Path) -> int:
    _mdl_mod, _tex_mod, col2_mod = load_bleeds_modules()
    _dragon_dff, _dragon_txd, dragon_col = load_dragonff_modules()

    with input_path.open("rb") as handle:
        header = col2_mod.parse_col2_header(handle, str(input_path))
        entries, _table_report = col2_mod.scan_primary_resource_table(
            handle,
            header=header,
            log=lambda _message: None,
        )
        colmodels = col2_mod.find_colmodels_from_entries(
            handle,
            header=header,
            entries=entries,
            report_lines=[],
            log=lambda _message: None,
        )

        dragon_col.Sections.init_sections(3)
        coll_file = dragon_col.coll()
        model_index = 0

        for base_off in sorted(colmodels):
            entry = colmodels[base_off]
            col_header = entry["header"]
            refs = entry["refs"]
            model_index += 1

            faces, max_index = col2_mod.read_colmodel_triangles(
                handle,
                tris_off=col_header["tris_off"],
                num_tris=col_header["numTris"],
                data_end=header["data_end"],
                log=lambda _message: None,
            )
            if faces:
                verts = col2_mod.read_colmodel_vertices(
                    handle,
                    verts_off=col_header["verts_off"],
                    required_vertices=max_index + 1,
                    data_end=header["data_end"],
                    log=lambda _message: None,
                )
                max_valid = len(verts) - 1
                faces = [face for face in faces if all(0 <= index <= max_valid for index in face)]
            else:
                verts, faces = _build_box_mesh(col_header["aabb_min"], col_header["aabb_max"])

            if not verts or not faces:
                continue

            local_center = tuple(map(float, col_header["center"]))
            local_verts = _shift_vertices([tuple(map(float, vertex)) for vertex in verts], local_center)
            local_min = (
                float(col_header["aabb_min"][0] - local_center[0]),
                float(col_header["aabb_min"][1] - local_center[1]),
                float(col_header["aabb_min"][2] - local_center[2]),
            )
            local_max = (
                float(col_header["aabb_max"][0] - local_center[0]),
                float(col_header["aabb_max"][1] - local_center[1]),
                float(col_header["aabb_max"][2] - local_center[2]),
            )

            model = dragon_col.ColModel()
            model.version = 3
            suffix = f"_{model_index:03d}" if len(colmodels) > 1 else ""
            model.model_name = sanitize_filename(output_path.stem + suffix)[:22]
            model.model_id = refs[0] if refs else model_index
            model.bounds = dragon_col.TBounds(
                local_min,
                local_max,
                (0.0, 0.0, 0.0),
                col_header["radius"],
            )
            model.mesh_verts = local_verts
            model.mesh_faces = [dragon_col.TFace(int(a), int(b), int(c), 0, 0) for a, b, c in faces]
            coll_file.models.append(model)

    output_path.write_bytes(coll_file.write_memory())
    return len(coll_file.models)


def write_col_from_mesh(mesh: MeshData, output_path: Path, model_id: int = 0) -> int:
    _dragon_dff, _dragon_txd, dragon_col = load_dragonff_modules()
    if not mesh.vertices or not mesh.faces:
        raise ValueError(f"No collision mesh data available for {output_path}")

    dragon_col.Sections.init_sections(3)
    coll_file = dragon_col.coll()
    model = dragon_col.ColModel()
    model.version = 3
    model.model_name = sanitize_filename(output_path.stem)[:22]
    model.model_id = model_id & 0xFFFF

    (cx, cy, cz), radius = _calculate_bounds(mesh.vertices)
    min_x = min(vertex[0] for vertex in mesh.vertices)
    min_y = min(vertex[1] for vertex in mesh.vertices)
    min_z = min(vertex[2] for vertex in mesh.vertices)
    max_x = max(vertex[0] for vertex in mesh.vertices)
    max_y = max(vertex[1] for vertex in mesh.vertices)
    max_z = max(vertex[2] for vertex in mesh.vertices)
    origin = (cx, cy, cz)
    local_vertices = _shift_vertices(mesh.vertices, origin)
    model.bounds = dragon_col.TBounds(
        (min_x - cx, min_y - cy, min_z - cz),
        (max_x - cx, max_y - cy, max_z - cz),
        (0.0, 0.0, 0.0),
        radius,
    )
    model.mesh_verts = local_vertices
    model.mesh_faces = [dragon_col.TFace(int(a), int(b), int(c), 0, 0) for a, b, c in mesh.faces]
    coll_file.models.append(model)
    output_path.write_bytes(coll_file.write_memory())
    return 1


def run_conversion_jobs(
    jobs: list[dict[str, str]],
    *,
    log: Callable[[str], None] | None = print,
    log_success: bool = True,
    on_job_done: Callable[[dict[str, str], dict[str, object]], None] | None = None,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for job in jobs:
        input_path = Path(job["input"])
        output_path = Path(job["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if log is not None and log_success:
                log(f"[convert] {job['archive']} {job['type']} {input_path.name} -> {output_path.name}")
            result: dict[str, object] = {
                "job": job["type"],
                "archive": job.get("archive"),
                "input": str(input_path),
                "output": str(output_path),
                "ok": True,
            }
            if job["type"] == "mdl":
                write_dff(input_path, output_path)
            elif job["type"] == "tex":
                result["texture_names"] = write_txd(input_path, output_path)
            elif job["type"] == "col2":
                result["models"] = write_col(input_path, output_path)
            else:
                raise ValueError(f"Unsupported job type: {job['type']}")
        except Exception as exc:  # pragma: no cover - exercised by live data failures
            if log is not None:
                log(f"[convert] FAILED {job['archive']} {job['type']} {input_path.name}: {exc}")
            result = {
                "job": job["type"],
                "archive": job.get("archive"),
                "input": str(input_path),
                "output": str(output_path),
                "ok": False,
                "error": str(exc),
            }
        results.append(result)
        if on_job_done is not None:
            on_job_done(job, result)
    return results
