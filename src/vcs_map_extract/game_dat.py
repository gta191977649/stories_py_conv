from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import struct
from collections import defaultdict

from .ipl_parser import IplSummary, IplTransform
from .reference_data import load_streamed_link_table, load_vcs_name_table
from .streamed_world import LEVEL_IDS, LevelChunk, SOURCE_PRIORITY
from .utils import safe_mkdir


GTAG_HEADER_STRUCT = struct.Struct("<7I2H")
POOL_HEADER_STRUCT = struct.Struct("<4I")
COL_ENTRY_STRUCT = struct.Struct("<I?3x4f4f20siiI")
CULL_ZONE_STRUCT = struct.Struct("<8h")
C2D_EFFECT_STRUCT = struct.Struct("<4f4B1B3x40s")

RESOURCE_IMAGE_OFFSET = GTAG_HEADER_STRUCT.size
DEFAULT_IDE_LAST_INDEX = 395
ENTITY_ENTRY_SIZE = 96
ENTITY_MODEL_INDEX_OFFSET = 86

MITYPE_SIMPLE = 1
MITYPE_MLO = 2
MITYPE_TIME = 3
MITYPE_WEAPON = 4
MITYPE_CLUMP = 5
MITYPE_VEHICLE = 6
MITYPE_PED = 7

COMMENTED_MODELS = {"IslandLODbeach", "IslandLODmainland"}
STREAM_NEAR_DUPLICATE_TOLERANCE = 6.0


@dataclass(slots=True)
class PoolInfo:
    entries_ptr: int
    flags_ptr: int
    size: int
    name: str


@dataclass(slots=True)
class GameDatModelInfo:
    model_id: int
    hash_key: int
    model_name: str
    model_type: int
    txd_slot: int
    txd_name: str
    num_objects: int | None = None
    lod_distances: tuple[float, float, float] | None = None
    flags: int | None = None
    time_on: int | None = None
    time_off: int | None = None

    @property
    def ide_section(self) -> str | None:
        if self.model_type == MITYPE_SIMPLE:
            return "objs"
        if self.model_type == MITYPE_TIME:
            return "tobj"
        if self.model_type == MITYPE_CLUMP:
            return "hier"
        return None

    def ide_row(self) -> str | None:
        section = self.ide_section
        if section is None:
            return None
        base = f"{self.model_id}, {self.model_name}, {self.txd_name}"
        if section == "hier":
            return base
        if self.num_objects is None or self.lod_distances is None or self.flags is None:
            return None
        lod_values = ", ".join(_format_number(value) for value in self.lod_distances[: self.num_objects])
        row = f"{base}, {self.num_objects}"
        if lod_values:
            row = f"{row}, {lod_values}"
        row = f"{row}, {self.flags}"
        if section == "tobj":
            row = f"{row}, {self.time_on}, {self.time_off}"
        return row

    @property
    def is_map_model(self) -> bool:
        return self.model_type in {MITYPE_SIMPLE, MITYPE_MLO, MITYPE_TIME, MITYPE_CLUMP}


@dataclass(slots=True)
class GameDatDecodeStats:
    model_infos_total: int
    named_model_infos: int
    fallback_hash_names: int
    ide_files_written: int
    ipl_files_written: int
    building_instances: int
    treadable_instances: int
    dummy_instances: int
    generated_streamed_instances: int
    cull_zones: int
    unsupported_weapons: int
    unsupported_vehicles: int
    unsupported_peds: int


class GameDat:
    def __init__(self, data: bytes, names: dict[int, str]) -> None:
        self.data = data
        self.names = names
        self.num_model_infos = self._resource_u32(24)
        self.model_info_ptrs = self._resource_u32(28)
        self.building_pool = self._read_pool(self._resource_u32(4))
        self.treadable_pool = self._read_pool(self._resource_u32(8))
        self.dummy_pool = self._read_pool(self._resource_u32(12))
        self.texlist_pool = self._read_pool(self._resource_u32(64))
        self.col_pool = self._read_pool(self._resource_u32(72))
        self.num_2d_effects = self._resource_u32(52)
        self.effects_ptr = self._resource_u32(56)
        self.num_attribute_zones = self._resource_i32(120)
        self.attribute_zones_ptr = self._resource_u32(124)
        self._model_infos: list[GameDatModelInfo] | None = None
        self._model_info_by_id: dict[int, GameDatModelInfo] | None = None

    @classmethod
    def from_path(cls, path: Path) -> GameDat:
        data = path.read_bytes()
        ident = data[:4]
        if ident != b"GATG":
            raise ValueError(f"Unsupported GAME.dat header: {ident!r}")
        return cls(data, load_vcs_name_table())

    def _u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.data, offset)[0]

    def _i32(self, offset: int) -> int:
        return struct.unpack_from("<i", self.data, offset)[0]

    def _s16(self, offset: int) -> int:
        return struct.unpack_from("<h", self.data, offset)[0]

    def _u16(self, offset: int) -> int:
        return struct.unpack_from("<H", self.data, offset)[0]

    def _u8(self, offset: int) -> int:
        return self.data[offset]

    def _resource_u32(self, relative_offset: int) -> int:
        return self._u32(RESOURCE_IMAGE_OFFSET + relative_offset)

    def _resource_i32(self, relative_offset: int) -> int:
        return self._i32(RESOURCE_IMAGE_OFFSET + relative_offset)

    def _read_pool(self, pool_ptr: int) -> PoolInfo:
        entries_ptr, flags_ptr, size, _alloc_ptr = POOL_HEADER_STRUCT.unpack_from(self.data, pool_ptr)
        name = _decode_c_string(self.data[pool_ptr + 16: pool_ptr + 32])
        return PoolInfo(entries_ptr=entries_ptr, flags_ptr=flags_ptr, size=size, name=name)

    def _pool_slot_free(self, pool: PoolInfo, index: int) -> bool:
        return bool(self.data[pool.flags_ptr + index] & 0x80)

    def _read_texlist_name(self, txd_slot: int) -> str:
        if txd_slot < 0 or txd_slot >= self.texlist_pool.size:
            return "null"
        if self._pool_slot_free(self.texlist_pool, txd_slot):
            return "null"
        entry_ptr = self.texlist_pool.entries_ptr + (txd_slot * 28)
        return _decode_c_string(self.data[entry_ptr + 8: entry_ptr + 28]) or "null"

    def iter_model_infos(self) -> list[GameDatModelInfo]:
        if self._model_infos is not None:
            return self._model_infos

        model_infos: list[GameDatModelInfo] = []
        by_id: dict[int, GameDatModelInfo] = {}
        for model_id in range(self.num_model_infos):
            ptr = self._u32(self.model_info_ptrs + (model_id * 4))
            if ptr == 0:
                continue
            hash_key = self._u32(ptr + 8)
            model_type = self._u8(ptr + 16)
            txd_slot = self._s16(ptr + 30)
            model_name = self.names.get(hash_key, f"hash_{hash_key:08X}")
            model = GameDatModelInfo(
                model_id=model_id,
                hash_key=hash_key,
                model_name=model_name,
                model_type=model_type,
                txd_slot=txd_slot,
                txd_name=self._read_texlist_name(txd_slot),
            )
            if model_type in {MITYPE_SIMPLE, MITYPE_TIME, MITYPE_WEAPON}:
                model.num_objects = self._u8(ptr + 56)
                model.lod_distances = struct.unpack_from("<3f", self.data, ptr + 44)
                model.flags = _convert_model_flags(self._u16(ptr + 58))
                if model_type == MITYPE_TIME:
                    model.time_on = self._i32(ptr + 64)
                    model.time_off = self._i32(ptr + 68)
            model_infos.append(model)
            by_id[model_id] = model

        self._model_infos = model_infos
        self._model_info_by_id = by_id
        return model_infos

    @property
    def model_info_by_id(self) -> dict[int, GameDatModelInfo]:
        if self._model_info_by_id is None:
            self.iter_model_infos()
        return self._model_info_by_id or {}

    def iter_col_ranges(self) -> list[tuple[str, int, int]]:
        ranges: list[tuple[str, int, int]] = []
        generic_last_index: int | None = None
        for index in range(self.col_pool.size):
            if self._pool_slot_free(self.col_pool, index):
                continue
            entry_ptr = self.col_pool.entries_ptr + (index * COL_ENTRY_STRUCT.size)
            _unk, _loaded, *_rects, raw_name, first_index, last_index, _chk_ptr = COL_ENTRY_STRUCT.unpack_from(
                self.data, entry_ptr
            )
            if index == 0:
                continue
            if generic_last_index is None or first_index < generic_last_index:
                generic_last_index = first_index
            ranges.append((_decode_c_string(raw_name), first_index, last_index))

        ranges.sort(key=lambda item: item[0])
        output_ranges: list[tuple[str, int, int]] = []
        output_ranges.extend(ranges)
        output_ranges.append(("default", 0, DEFAULT_IDE_LAST_INDEX))
        if generic_last_index is not None and generic_last_index > DEFAULT_IDE_LAST_INDEX + 1:
            output_ranges.append(("generic", DEFAULT_IDE_LAST_INDEX + 1, generic_last_index - 1))
        return output_ranges

    def decode_ide_files(self, ide_dir: Path) -> tuple[int, int]:
        safe_mkdir(ide_dir)
        model_infos = self.iter_model_infos()
        fallback_hash_names = sum(1 for model in model_infos if model.model_name.startswith("hash_"))

        written = 0
        for name, first_index, last_index in self.iter_col_ranges():
            sections: dict[str, list[str]] = {"objs": [], "hier": [], "tobj": []}
            for model in model_infos:
                if not (first_index <= model.model_id <= last_index):
                    continue
                row = model.ide_row()
                if row is None:
                    continue
                section = model.ide_section
                if section is None:
                    continue
                sections[section].append(row)

            lines = ["# Decoded from GAME.dat"]
            for section_name in ("objs", "hier", "tobj"):
                rows = sections[section_name]
                if not rows:
                    continue
                lines.append(section_name)
                lines.extend(rows)
                lines.append("end")
            (ide_dir / f"{name}.ide").write_text("\n".join(lines) + "\n", encoding="utf-8")
            written += 1
        effects_rows = self._collect_2dfx_rows()
        if effects_rows:
            lines = ["# Decoded from GAME.dat", "2dfx", *effects_rows, "end"]
            (ide_dir / "2dfx.ide").write_text("\n".join(lines) + "\n", encoding="utf-8")
            written += 1
        return written, fallback_hash_names

    def _collect_2dfx_rows(self) -> list[str]:
        rows: list[str] = []
        for model in self.iter_model_infos():
            if not model.is_map_model:
                continue
            ptr = self._u32(self.model_info_ptrs + (model.model_id * 4))
            if ptr == 0:
                continue
            num_effects = self._u8(ptr + 17)
            first_effect = self._s16(ptr + 24)
            if num_effects <= 0 or first_effect < 0:
                continue
            for effect_index in range(first_effect, first_effect + num_effects):
                if effect_index >= self.num_2d_effects:
                    break
                effect_ptr = self.effects_ptr + (effect_index * 64)
                row = self._format_2dfx_row(model.model_id, effect_ptr)
                if row is not None:
                    rows.append(row)
        return rows

    def _format_2dfx_row(self, model_id: int, effect_ptr: int) -> str | None:
        px, py, pz, _pw = struct.unpack_from("<4f", self.data, effect_ptr)
        r, g, b, a = struct.unpack_from("<4B", self.data, effect_ptr + 16)
        effect_type = self._u8(effect_ptr + 20)
        if effect_type == 0:
            corona_ptr = self._u32(effect_ptr + 48)
            shadow_ptr = self._u32(effect_ptr + 52)
            return ", ".join(
                (
                    str(model_id),
                    _format_number(px),
                    _format_number(py),
                    _format_number(pz),
                    str(r),
                    str(g),
                    str(b),
                    str(a),
                    str(effect_type),
                    f"\"{self._read_texture_name(corona_ptr)}\"",
                    f"\"{self._read_texture_name(shadow_ptr)}\"",
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 24)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 28)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 32)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 36)[0]),
                    str(self._u8(effect_ptr + 43)),
                    str(self._u8(effect_ptr + 40)),
                    str(self._u8(effect_ptr + 41)),
                    str(self._u8(effect_ptr + 42)),
                    str(self._u8(effect_ptr + 44)),
                )
            )
        if effect_type == 1:
            return ", ".join(
                (
                    str(model_id),
                    _format_number(px),
                    _format_number(py),
                    _format_number(pz),
                    str(r),
                    str(g),
                    str(b),
                    str(a),
                    str(effect_type),
                    str(self._i32(effect_ptr + 24)),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 28)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 32)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 36)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 40)[0]),
                )
            )
        if effect_type == 2:
            return ", ".join(
                (
                    str(model_id),
                    _format_number(px),
                    _format_number(py),
                    _format_number(pz),
                    str(r),
                    str(g),
                    str(b),
                    str(a),
                    str(effect_type),
                    str(self._u8(effect_ptr + 36)),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 24)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 28)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 32)[0]),
                    str(self._u8(effect_ptr + 37)),
                )
            )
        if effect_type == 3:
            return ", ".join(
                (
                    str(model_id),
                    _format_number(px),
                    _format_number(py),
                    _format_number(pz),
                    str(r),
                    str(g),
                    str(b),
                    str(a),
                    str(effect_type),
                    str(self._u8(effect_ptr + 48)),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 24)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 28)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 32)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 36)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 40)[0]),
                    _format_number(struct.unpack_from("<f", self.data, effect_ptr + 44)[0]),
                )
            )
        return None

    def _read_texture_name(self, texture_ptr: int) -> str:
        if texture_ptr <= 0 or texture_ptr + 48 > len(self.data):
            return ""
        return _decode_c_string(self.data[texture_ptr + 16: texture_ptr + 48])

    def decode_ipl_files(self, root: Path, ipl_dir: Path) -> tuple[int, int, int, int, int, int]:
        safe_mkdir(ipl_dir)
        existing_keys: set[tuple[int, tuple[float, ...]]] = set()
        existing_positions: set[tuple[int, tuple[float, float, float]]] = set()
        existing_positions_by_model: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
        base_model_ids: set[int] = set()
        buildings = self._write_pool_ipl(
            ipl_dir / "Buildings.ipl",
            self.building_pool,
            existing_keys,
            existing_positions,
            existing_positions_by_model,
            base_model_ids,
        )
        treadables = self._write_pool_ipl(
            ipl_dir / "Treadables.ipl",
            self.treadable_pool,
            existing_keys,
            existing_positions,
            existing_positions_by_model,
            base_model_ids,
        )
        dummys = self._write_pool_ipl(
            ipl_dir / "Dummys.ipl",
            self.dummy_pool,
            existing_keys,
            existing_positions,
            existing_positions_by_model,
            base_model_ids,
        )
        generated_streamed = self._write_streamed_interior_ipl(
            root,
            ipl_dir / "wrld_stream.ipl",
            existing_keys,
            existing_positions,
            existing_positions_by_model,
            base_model_ids,
        )
        cull_zones = self._write_cull_ipl(ipl_dir / "cull.ipl")
        return 5, buildings, treadables, dummys, generated_streamed, cull_zones

    def build_ipl_summary(self) -> IplSummary:
        summary = IplSummary()
        self._append_pool_transforms(summary, self.building_pool, "GAME.dat:Buildings.ipl", 0)
        self._append_pool_transforms(summary, self.treadable_pool, "GAME.dat:Treadables.ipl", 1 << 16)
        self._append_pool_transforms(summary, self.dummy_pool, "GAME.dat:Dummys.ipl", 2 << 16)
        return summary

    def _write_pool_ipl(
        self,
        path: Path,
        pool: PoolInfo,
        existing_keys: set[tuple[int, tuple[float, ...]]],
        existing_positions: set[tuple[int, tuple[float, float, float]]],
        existing_positions_by_model: dict[int, list[tuple[float, float, float]]],
        base_model_ids: set[int],
    ) -> int:
        lines = ["# Decoded from GAME.dat", "inst"]
        written = 0
        model_info_by_id = self.model_info_by_id
        for index in range(pool.size):
            if self._pool_slot_free(pool, index):
                continue
            entry_ptr = pool.entries_ptr + (index * ENTITY_ENTRY_SIZE)
            model_id = self._s16(entry_ptr + ENTITY_MODEL_INDEX_OFFSET)
            if model_id < 0:
                continue
            model = model_info_by_id.get(model_id)
            if model is None:
                continue
            if not model.is_map_model:
                continue
            base_model_ids.add(model_id)
            matrix = struct.unpack_from("<16f", self.data, entry_ptr)
            transform = _matrix_to_ipl_transform(matrix)
            if transform is None:
                continue
            position, _scale, rotation = transform
            unit_scale = (1.0, 1.0, 1.0)
            position_key = (model_id, _ipl_position_key(position))
            if position_key in existing_positions:
                continue
            existing_positions.add(position_key)
            existing_positions_by_model[model_id].append(position)
            existing_keys.add((model_id, _ipl_transform_key(position, unit_scale, rotation)))
            row = _format_ipl_inst_row(model_id, model.model_name, 0, position, unit_scale, rotation)
            if model.model_name in COMMENTED_MODELS:
                row = f"# {row}"
            lines.append(row)
            written += 1
        lines.append("end")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return written

    def _append_pool_transforms(self, summary: IplSummary, pool: PoolInfo, source_file: str, entity_id_prefix: int) -> None:
        model_info_by_id = self.model_info_by_id
        inst_count = 0
        for index in range(pool.size):
            if self._pool_slot_free(pool, index):
                continue
            entry_ptr = pool.entries_ptr + (index * ENTITY_ENTRY_SIZE)
            model_id = self._s16(entry_ptr + ENTITY_MODEL_INDEX_OFFSET)
            if model_id < 0:
                continue
            model = model_info_by_id.get(model_id)
            if model is None or not model.is_map_model:
                continue
            matrix = struct.unpack_from("<16f", self.data, entry_ptr)
            transform = _matrix_to_ipl_transform(matrix)
            if transform is None:
                continue
            position, _scale, rotation = transform
            inst_count += 1
            entity_id = entity_id_prefix | index
            ipl_transform = IplTransform(
                model_id=model_id,
                model_name=model.model_name,
                interior=0,
                position=position,
                rotation=rotation,
                source_file=source_file,
                entity_id=entity_id,
            )
            summary.transforms_by_model.setdefault(model.model_name.lower(), []).append(ipl_transform)
            summary.transforms_by_id.setdefault(model_id, []).append(ipl_transform)
            summary.transforms_by_entity_id[entity_id] = ipl_transform
        summary.inst_count_by_file[source_file] = inst_count
        summary.nonzero_interior_by_file[source_file] = 0

    def _write_streamed_interior_ipl(
        self,
        root: Path,
        path: Path,
        existing_keys: set[tuple[int, tuple[float, ...]]],
        existing_positions: set[tuple[int, tuple[float, float, float]]],
        existing_positions_by_model: dict[int, list[tuple[float, float, float]]],
        base_model_ids: set[int],
    ) -> int:
        lines = ["# Decoded from LVZ/WRLD streamed placements", "inst"]
        placements: dict[tuple[str, int], tuple[GameDatModelInfo, int, tuple[float, ...], int, str, int, bool]] = {}
        links = load_streamed_link_table()

        for archive_name in LEVEL_IDS:
            level = LevelChunk.from_archive(root, archive_name)
            for visit, pass_index, instance in level.iter_instances():
                if visit.source_kind not in {"interior", "swap-sector"}:
                    continue
                link = links.get(instance.world_id)
                if link is None:
                    continue
                _linked_ipl_id, model_id = link
                if model_id in base_model_ids:
                    continue
                model = self.model_info_by_id.get(model_id)
                if model is None or not model.is_map_model:
                    continue
                absolute_matrix = _matrix_with_origin(instance.matrix, visit.origin)
                if _matrix_to_ipl_transform(absolute_matrix) is None:
                    continue
                key = (archive_name, instance.world_id)
                candidate = (
                    model,
                    0,
                    absolute_matrix,
                    pass_index,
                    visit.source_kind,
                    visit.sector_id,
                    visit.visible,
                )
                current = placements.get(key)
                if current is None or _prefer_streamed_placement(candidate, current):
                    placements[key] = candidate

        for archive_name, world_id in sorted(placements):
            model, interior_id, matrix, _pass_index, _source_kind, _sector_id, _visible = placements[(archive_name, world_id)]
            transform = _matrix_to_ipl_transform(matrix)
            if transform is None:
                continue
            position, _scale, rotation = transform
            unit_scale = (1.0, 1.0, 1.0)
            position_key = (model.model_id, _ipl_position_key(position))
            if position_key in existing_positions:
                continue
            if _has_nearby_model_position(
                model.model_id,
                position,
                existing_positions_by_model,
                STREAM_NEAR_DUPLICATE_TOLERANCE,
            ):
                continue
            dedupe_key = (model.model_id, _ipl_transform_key(position, unit_scale, rotation))
            if dedupe_key in existing_keys:
                continue
            existing_positions.add(position_key)
            existing_positions_by_model[model.model_id].append(position)
            existing_keys.add(dedupe_key)
            row = _format_ipl_inst_row(model.model_id, model.model_name, interior_id, position, unit_scale, rotation)
            if model.model_name in COMMENTED_MODELS:
                row = f"# {row}"
            lines.append(row)

        lines.append("end")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return max(0, len(lines) - 3)

    def _write_cull_ipl(self, path: Path) -> int:
        lines = ["# Decoded from GAME.dat", "cull"]
        for index in range(self.num_attribute_zones):
            x1, x2, y1, y2, z1, z2, attribute, wanted_level = CULL_ZONE_STRUCT.unpack_from(
                self.data, self.attribute_zones_ptr + (index * CULL_ZONE_STRUCT.size)
            )
            mid_x = (float(x1) + float(x2)) * 0.5
            mid_y = (float(y1) + float(y2)) * 0.5
            mid_z = (float(z1) + float(z2)) * 0.5
            lines.append(
                ", ".join(
                    (
                        _format_number(mid_x),
                        _format_number(mid_y),
                        _format_number(mid_z),
                        f"{x1}.0",
                        f"{y1}.0",
                        f"{z1}.0",
                        f"{x2}.0",
                        f"{y2}.0",
                        f"{z2}.0",
                        str(attribute),
                        str(wanted_level),
                    )
                )
            )
        lines.append("end")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.num_attribute_zones


def decode_game_dat(game_dat_path: Path, output_root: Path) -> GameDatDecodeStats:
    game_dat = GameDat.from_path(game_dat_path)
    root = game_dat_path.parent
    data_dir = output_root / "data"
    maps_dir = data_dir / "maps"
    ide_dir = maps_dir
    ipl_dir = maps_dir
    safe_mkdir(data_dir)
    safe_mkdir(maps_dir)
    model_infos = game_dat.iter_model_infos()
    ide_files_written, fallback_hash_names = game_dat.decode_ide_files(ide_dir)
    ipl_files_written, buildings, treadables, dummys, generated_streamed, cull_zones = game_dat.decode_ipl_files(root, ipl_dir)
    _write_gta_dat(data_dir / "gta.dat", maps_dir)
    unsupported_weapons = sum(1 for model in model_infos if model.model_type == MITYPE_WEAPON)
    unsupported_vehicles = sum(1 for model in model_infos if model.model_type == MITYPE_VEHICLE)
    unsupported_peds = sum(1 for model in model_infos if model.model_type == MITYPE_PED)
    return GameDatDecodeStats(
        model_infos_total=len(model_infos),
        named_model_infos=len(model_infos) - fallback_hash_names,
        fallback_hash_names=fallback_hash_names,
        ide_files_written=ide_files_written,
        ipl_files_written=ipl_files_written,
        building_instances=buildings,
        treadable_instances=treadables,
        dummy_instances=dummys,
        generated_streamed_instances=generated_streamed,
        cull_zones=cull_zones,
        unsupported_weapons=unsupported_weapons,
        unsupported_vehicles=unsupported_vehicles,
        unsupported_peds=unsupported_peds,
    )


def _write_gta_dat(path: Path, maps_dir: Path) -> None:
    ide_files = sorted(maps_dir.glob("*.ide"))
    ipl_files = sorted(maps_dir.glob("*.ipl"))
    lines = [
        "#",
        "# Object types",
        "#",
    ]
    lines.extend(f"IDE Data/Maps/{ide_path.name}" for ide_path in ide_files)
    lines.extend(
        (
            "",
            "#",
            "# Scene information",
            "#",
        )
    )
    lines.extend(f"IPL Data/Maps/{ipl_path.name}" for ipl_path in ipl_files)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _decode_c_string(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "ignore")


def _convert_model_flags(raw_flags: int) -> int:
    return ((raw_flags >> 2) & 1) | ((raw_flags & 0xFFE0) >> 4)


def _format_number(value: float) -> str:
    if abs(value) < 1e-8:
        value = 0.0
    if math.isfinite(value):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text not in {"-0", ""} else "0"
    return "0"


def _format_ipl_inst_row(
    model_id: int,
    model_name: str,
    interior: int,
    position: tuple[float, float, float],
    scale: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
) -> str:
    return ", ".join(
        (
            str(model_id),
            model_name,
            str(interior),
            _format_number(position[0]),
            _format_number(position[1]),
            _format_number(position[2]),
            _format_number(scale[0]),
            _format_number(scale[1]),
            _format_number(scale[2]),
            _format_number(rotation[0]),
            _format_number(rotation[1]),
            _format_number(rotation[2]),
            _format_number(rotation[3]),
        )
    )


def _matrix_with_origin(matrix: tuple[float, ...], origin: tuple[float, float, float]) -> tuple[float, ...]:
    adjusted = list(matrix)
    adjusted[12] += origin[0]
    adjusted[13] += origin[1]
    adjusted[14] += origin[2]
    return tuple(adjusted)


def _matrix_to_ipl_transform(
    matrix: tuple[float, ...],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float, float]] | None:
    position = (matrix[12], matrix[13], matrix[14])
    rows = (
        (matrix[0], matrix[1], matrix[2]),
        (matrix[4], matrix[5], matrix[6]),
        (matrix[8], matrix[9], matrix[10]),
    )
    if not all(math.isfinite(value) and abs(value) < 1e8 for value in (*position, *rows[0], *rows[1], *rows[2])):
        return None

    scales: list[float] = []
    normalized_rows: list[tuple[float, float, float]] = []
    for row in rows:
        length = math.sqrt(sum(component * component for component in row))
        if not math.isfinite(length) or length <= 1e-8 or length > 1e8:
            return None
        scales.append(length)
        normalized_rows.append(tuple(component / length for component in row))

    normalized_matrix = (
        normalized_rows[0][0], normalized_rows[0][1], normalized_rows[0][2], 0.0,
        normalized_rows[1][0], normalized_rows[1][1], normalized_rows[1][2], 0.0,
        normalized_rows[2][0], normalized_rows[2][1], normalized_rows[2][2], 0.0,
        position[0], position[1], position[2], 1.0,
    )
    return position, (scales[0], scales[1], scales[2]), _matrix_to_ipl_quaternion(normalized_matrix)


def _matrix_to_ipl_quaternion(matrix: tuple[float, ...]) -> tuple[float, float, float, float]:
    m00, m01, m02 = matrix[0], matrix[1], matrix[2]
    m10, m11, m12 = matrix[4], matrix[5], matrix[6]
    m20, m21, m22 = matrix[8], matrix[9], matrix[10]

    if m00 + m11 + m22 > 0.0:
        sq = math.sqrt(1.0 + m00 + m11 + m22)
        rp = 0.5 / sq
        x = rp * (m12 - m21)
        y = rp * (m20 - m02)
        z = rp * (m01 - m10)
        w = sq * 0.5
    elif m00 > m11 and m00 > m22:
        sq = math.sqrt(1.0 + m00 - m11 - m22)
        rp = 0.5 / sq
        x = sq * 0.5
        y = rp * (m10 + m01)
        z = rp * (m20 + m02)
        w = rp * (m12 - m21)
    elif m11 > m22:
        sq = math.sqrt(1.0 + m11 - m00 - m22)
        rp = 0.5 / sq
        x = rp * (m10 + m01)
        y = sq * 0.5
        z = rp * (m21 + m12)
        w = rp * (m20 - m02)
    else:
        sq = math.sqrt(1.0 + m22 - m00 - m11)
        rp = 0.5 / sq
        x = rp * (m20 + m02)
        y = rp * (m21 + m12)
        z = sq * 0.5
        w = rp * (m01 - m10)

    values = (-x, -y, -z, w)
    return tuple(0.0 if abs(value) < 1e-8 else value for value in values)


def _prefer_streamed_placement(
    candidate: tuple[GameDatModelInfo, int, tuple[float, ...], int, str, int, bool],
    current: tuple[GameDatModelInfo, int, tuple[float, ...], int, str, int, bool],
) -> bool:
    return (
        SOURCE_PRIORITY[candidate[4]],
        candidate[3],
        candidate[5],
        not candidate[6],
    ) < (
        SOURCE_PRIORITY[current[4]],
        current[3],
        current[5],
        not current[6],
    )


def _ipl_transform_key(
    position: tuple[float, float, float],
    scale: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
) -> tuple[float, ...]:
    return tuple(round(value, 5) for value in (*position, *scale, *rotation))


def _ipl_position_key(position: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(round(value, 5) for value in position)


def _has_nearby_model_position(
    model_id: int,
    position: tuple[float, float, float],
    positions_by_model: dict[int, list[tuple[float, float, float]]],
    tolerance: float,
) -> bool:
    tolerance_sq = tolerance * tolerance
    for existing in positions_by_model.get(model_id, ()):
        dx = position[0] - existing[0]
        dy = position[1] - existing[1]
        dz = position[2] - existing[2]
        if (dx * dx) + (dy * dy) + (dz * dz) <= tolerance_sq:
            return True
    return False
