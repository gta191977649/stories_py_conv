from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

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
    textures: list[tuple[str, object, int]] = []
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
                textures.append((name, header_obj, int(header_obj.raster_offset)))

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
    offsets = [offset for _name, _header_obj, offset in textures]
    block_sizes: list[int] = []
    for index, start in enumerate(offsets):
        if index + 1 < len(offsets):
            end = offsets[index + 1]
        else:
            candidates = [header["glob1"], header["glob2"], header["coll_size"], len(data)]
            end = min(candidate for candidate in candidates if candidate > start)
        block_sizes.append(max(0, end - start))

    decoded: list[DecodedTexture] = []
    for (name, header_obj, _offset), block_size in zip(textures, block_sizes, strict=True):
        if isinstance(header_obj, tex_mod.Ps2TexHeader):
            rgba_array = tex_mod.decode_ps2_texture(data, header_obj, block_size, palette_override=None)
        else:
            rgba_array = tex_mod.decode_psp_texture(data, header_obj, block_size, palette_override=None)
        if rgba_array is None:
            continue
        height, width, _channels = rgba_array.shape
        decoded.append(
            DecodedTexture(
                name=_sanitize_texture_name(name),
                rgba=np.asarray(rgba_array, dtype=np.uint8).tobytes(),
                width=width,
                height=height,
            )
        )
    return decoded


def _make_txd_native(texture: DecodedTexture):
    _dragon_dff, dragon_txd, _dragon_col = load_dragonff_modules()
    native = dragon_txd.TextureNative()
    native.platform_id = _dragon_dff.NativePlatformType.D3D9
    native.filter_mode = 0x06
    native.uv_addressing = 0b00010001
    native.name = texture.name
    native.mask = ""
    native.raster_format_flags = dragon_txd.RasterFormat.RASTER_8888 << 8
    native.d3d_format = dragon_txd.D3DFormat.D3D_8888
    native.width = texture.width
    native.height = texture.height
    native.depth = 32
    native.num_levels = 1
    native.raster_type = 4
    native.platform_properties = SimpleNamespace(
        alpha=True,
        cube_texture=False,
        auto_mipmaps=False,
        compressed=False,
    )
    native.palette = b""
    native.pixels = [dragon_txd.ImageEncoder.rgba_to_bgra8888(texture.rgba)]
    return native


def write_txd_from_decoded_textures(output_path: Path, textures: list[DecodedTexture]) -> list[str]:
    _dragon_dff, dragon_txd, _dragon_col = load_dragonff_modules()
    txd_file = dragon_txd.txd()
    txd_file.device_id = dragon_txd.DeviceType.DEVICE_D3D9
    txd_file.native_textures = [_make_txd_native(texture) for texture in textures]
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
    accum = [(0.0, 0.0, 0.0) for _ in vertices]
    for a, b, c in faces:
        pa = vertices[a]
        pb = vertices[b]
        pc = vertices[c]
        normal = _vector_cross(_vector_sub(pb, pa), _vector_sub(pc, pa))
        accum[a] = _vector_add(accum[a], normal)
        accum[b] = _vector_add(accum[b], normal)
        accum[c] = _vector_add(accum[c], normal)
    return [_vector_normalize(normal) for normal in accum]


def _calculate_bounds(vertices: list[tuple[float, float, float]]) -> tuple[tuple[float, float, float], float]:
    if not vertices:
        return (0.0, 0.0, 0.0), 0.0
    cx = sum(vertex[0] for vertex in vertices) / len(vertices)
    cy = sum(vertex[1] for vertex in vertices) / len(vertices)
    cz = sum(vertex[2] for vertex in vertices) / len(vertices)
    radius = max(
        ((vertex[0] - cx) ** 2 + (vertex[1] - cy) ** 2 + (vertex[2] - cz) ** 2) ** 0.5
        for vertex in vertices
    )
    return (cx, cy, cz), radius


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


def write_dff_from_mesh(mesh: MeshData, output_path: Path, frame_name: str) -> None:
    dragon_dff, _dragon_txd, _dragon_col = load_dragonff_modules()
    if not mesh.vertices or not mesh.faces:
        raise ValueError(f"No mesh data available for {output_path}")

    normals = _calculate_normals(mesh.vertices, mesh.faces)
    (cx, cy, cz), radius = _calculate_bounds(mesh.vertices)

    material_names = sorted(set(name for name in mesh.face_materials if name))
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


def write_dff(input_path: Path, output_path: Path) -> None:
    mdl_mod, _tex_mod, _col2_mod = load_bleeds_modules()
    dragon_dff, _dragon_txd, _dragon_col = load_dragonff_modules()

    ctx = mdl_mod.read_stories_mdl(str(input_path), "PS2", "SIM")
    geo = ctx.atomic.ps2_geometry
    if not geo.parts:
        raise ValueError(f"No PS2 geometry parts decoded from {input_path}")

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    triangles = []
    uvs = []
    vertex_colors: list[tuple[int, int, int, int]] = []

    for part in geo.parts:
        base_index = len(vertices)
        part_vertices = [tuple(map(float, vertex)) for vertex in part.verts]
        vertices.extend(part_vertices)

        part_uvs = list(getattr(part, "uvs", []))
        for vertex_index in range(len(part_vertices)):
            if vertex_index < len(part_uvs):
                u, v = part_uvs[vertex_index]
            else:
                u, v = 0.0, 0.0
            uvs.append(dragon_dff.TexCoords(float(u), 1.0 - float(v)))

        part_colors = list(getattr(part, "vertex_colors", []))
        for vertex_index in range(len(part_vertices)):
            if vertex_index < len(part_colors):
                r, g, b, a = part_colors[vertex_index]
                vertex_colors.append((int(r), int(g), int(b), int(a)))
            else:
                vertex_colors.append((255, 255, 255, 255))

        material_index = int(getattr(part, "material_id", 0))
        for face in part.faces:
            a, b, c = (base_index + int(face[0]), base_index + int(face[1]), base_index + int(face[2]))
            faces.append((a, b, c))
            triangles.append(dragon_dff.Triangle(b, a, material_index, c))

    mesh = MeshData(
        vertices=vertices,
        faces=faces,
        uvs=[(uv.u, uv.v) for uv in uvs],
        face_materials=[
            getattr(geo.materials[triangle.material], "texture", "") if triangle.material < len(geo.materials) else ""
            for triangle in triangles
        ],
        vertex_colors=vertex_colors,
    )
    write_dff_from_mesh(mesh, output_path, sanitize_filename(input_path.stem))


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
    return [
        (
            vertex[0] - origin[0],
            vertex[1] - origin[1],
            vertex[2] - origin[2],
        )
        for vertex in vertices
    ]


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


def run_conversion_jobs(jobs: list[dict[str, str]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for job in jobs:
        input_path = Path(job["input"])
        output_path = Path(job["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            print(
                f"[convert] {job['archive']} {job['type']} {input_path.name} -> {output_path.name}",
                flush=True,
            )
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
            print(
                f"[convert] FAILED {job['archive']} {job['type']} {input_path.name}: {exc}",
                flush=True,
            )
            result = {
                "job": job["type"],
                "archive": job.get("archive"),
                "input": str(input_path),
                "output": str(output_path),
                "ok": False,
                "error": str(exc),
            }
        results.append(result)
    return results
