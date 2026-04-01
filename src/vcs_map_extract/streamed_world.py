from __future__ import annotations

import struct
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .models import IdeModel, StreamedArchivePlan, StreamedModelPlan, StreamedPlacement
from .name_resolver import NameResolver
from .utils import maybe_decompress


LEVEL_IDS = {
    "BEACH": 1,
    "MAINLA": 2,
    "MALL": 3,
}
LEVEL_BODY_OFFSET = 0x20
NUM_SECTOR_ROWS = 36
SECLIST_END_INDEX = 8
SECTOR_ENTRY_SIZE = 0x50
WRLD_IDENT = 0x57524C44
AREA_IDENT = 0x41524541
MAX_REPORTED_UNRESOLVED = 128
XINC = 125.0
YINC = 108.25
XSTART = -2400.0
YSTART = -2000.0
SOURCE_PRIORITY = {
    "world": 0,
    "interior": 1,
    "swap-sector": 2,
}

SECTOR_HEADER_STRUCT = struct.Struct("<IIIIIIIHH")
SECTOR_ROW_STRUCT = struct.Struct("<Ii")
RESOURCE_TABLE_ENTRY_STRUCT = struct.Struct("<III")
LEVEL_SWAP_STRUCT = struct.Struct("<BBh")
INTERIOR_SWAP_STRUCT = struct.Struct("<BBBBh")
AREA_INFO_STRUCT = struct.Struct("<hhIII")
INSTANCE_STRUCT = struct.Struct("<HH12x16f")

LEVEL_RESOURCE_TABLE_PTR_OFFSET = 0x20
SECTOR_ROWS_OFFSET = 0x24
SECTOR_END_OFFSET = 0x144
NUM_RESOURCES_OFFSET = 0x14C
NUM_SWAP_INFOS_OFFSET = 0x2D0
SWAP_INFOS_PTR_OFFSET = 0x2D4
NUM_LEVEL_SWAPS_OFFSET = 0x2D8
LEVEL_SWAPS_PTR_OFFSET = 0x2DC
NUM_INTERIORS_OFFSET = 0x2E0
INTERIORS_PTR_OFFSET = 0x2E4
NUM_AREAS_OFFSET = 0x2F0
AREAS_PTR_OFFSET = 0x2F4


def _read_u16(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<H", blob, offset)[0]


def _read_u32(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def _ptr_to_body_offset(raw_ptr: int) -> int:
    return raw_ptr - LEVEL_BODY_OFFSET


def _matrix_signature(values: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(round(float(value), 6) for value in values)


def _world_sector_origin(secx: int, secy: int) -> tuple[float, float, float]:
    return (
        XSTART + (XINC / 2.0) + (XINC * secx) - ((secy & 1) * (XINC / 2.0)),
        YSTART + (YINC / 2.0) + (YINC * secy),
        0.0,
    )


def _matrix_with_origin(
    values: tuple[float, ...],
    origin: tuple[float, float, float],
) -> tuple[float, ...]:
    adjusted = list(values)
    adjusted[12] += origin[0]
    adjusted[13] += origin[1]
    adjusted[14] += origin[2]
    return tuple(adjusted)


@dataclass(slots=True)
class SectorHeader:
    index: int
    offset: int
    file_size: int
    data_size: int
    reloc_tab: int
    num_relocs: int
    global_tab: int


@dataclass(slots=True)
class LevelSwap:
    time_off: int
    time_on: int
    sector_id: int

    def default_visible(self) -> bool:
        if self.time_off == 0xFF:
            return False
        if self.time_off & 0x80:
            return False
        return self.time_on == 0


@dataclass(slots=True)
class InteriorSwap:
    secx: int
    secy: int
    swap_slot: int
    swap_state: int
    sector_id: int

    def default_visible(self) -> bool:
        return self.swap_state == 0


@dataclass(slots=True)
class AreaInfo:
    a: int
    b: int
    file_offset: int
    file_size: int
    num_resources: int


@dataclass(slots=True)
class SectorInstance:
    ipl_id: int
    world_id: int
    res_id: int
    pass_index: int
    matrix: tuple[float, ...]


@dataclass(slots=True)
class ParsedSector:
    header: SectorHeader
    resources: dict[int, bytes]
    instances_by_pass: list[list[SectorInstance]]
    swap_defs: list[LevelSwap]


@dataclass(slots=True)
class SectorVisit:
    sector_id: int
    source_kind: str
    visible: bool
    origin: tuple[float, float, float]
    interior_chain: bool = False


@dataclass(slots=True)
class LevelChunk:
    archive_name: str
    level_id: int
    data: bytes
    img_bytes: bytes
    resource_table_ptr: int
    num_resources: int
    sector_rows: list[tuple[int, int]]
    sector_end_header_ptr: int
    sector_end_start_off: int
    sector_headers: list[SectorHeader]
    num_world_sectors: int
    swap_infos_ptr: int
    num_swap_infos: int
    level_swaps: list[LevelSwap]
    interiors: list[InteriorSwap]
    areas: list[AreaInfo]
    _sector_cache: dict[int, ParsedSector] = field(default_factory=dict)
    _reachable_cache: dict[int, SectorVisit] | None = None

    @classmethod
    def from_archive(cls, root: Path, archive_name: str) -> LevelChunk:
        level_id = LEVEL_IDS[archive_name]
        lvz_bytes = maybe_decompress((root / f"{archive_name}.LVZ").read_bytes())
        img_bytes = (root / f"{archive_name}.IMG").read_bytes()
        resource_table_ptr = _read_u32(lvz_bytes, LEVEL_RESOURCE_TABLE_PTR_OFFSET)
        num_resources = _read_u32(lvz_bytes, NUM_RESOURCES_OFFSET)

        sector_rows = [
            SECTOR_ROW_STRUCT.unpack_from(lvz_bytes, SECTOR_ROWS_OFFSET + (index * SECTOR_ROW_STRUCT.size))
            for index in range(NUM_SECTOR_ROWS)
        ]
        sector_end_header_ptr, sector_end_start_off = SECTOR_ROW_STRUCT.unpack_from(lvz_bytes, SECTOR_END_OFFSET)

        sector_headers: list[SectorHeader] = []
        for index in range(16384):
            off = sector_rows[0][0] + (index * SECTOR_HEADER_STRUCT.size)
            if off + SECTOR_HEADER_STRUCT.size > len(lvz_bytes):
                break
            ident, shrink, file_size, data_size, reloc_tab, num_relocs, global_tab, num_classes, num_funcs = SECTOR_HEADER_STRUCT.unpack_from(lvz_bytes, off)
            if ident != WRLD_IDENT:
                break
            sector_headers.append(
                SectorHeader(
                    index=index,
                    offset=off,
                    file_size=file_size,
                    data_size=data_size,
                    reloc_tab=reloc_tab,
                    num_relocs=num_relocs,
                    global_tab=global_tab,
                )
            )
        num_world_sectors = sum(1 for header in sector_headers if header.offset < sector_end_header_ptr)

        num_swap_infos = _read_u32(lvz_bytes, NUM_SWAP_INFOS_OFFSET)
        swap_infos_ptr = _read_u32(lvz_bytes, SWAP_INFOS_PTR_OFFSET)

        num_level_swaps = _read_u32(lvz_bytes, NUM_LEVEL_SWAPS_OFFSET)
        level_swaps_ptr = _read_u32(lvz_bytes, LEVEL_SWAPS_PTR_OFFSET)
        level_swaps = [
            LevelSwap(*LEVEL_SWAP_STRUCT.unpack_from(lvz_bytes, level_swaps_ptr + (index * LEVEL_SWAP_STRUCT.size)))
            for index in range(num_level_swaps)
            if level_swaps_ptr + ((index + 1) * LEVEL_SWAP_STRUCT.size) <= len(lvz_bytes)
        ]

        num_interiors = _read_u32(lvz_bytes, NUM_INTERIORS_OFFSET)
        interiors_ptr = _read_u32(lvz_bytes, INTERIORS_PTR_OFFSET)
        interiors = [
            InteriorSwap(*INTERIOR_SWAP_STRUCT.unpack_from(lvz_bytes, interiors_ptr + (index * INTERIOR_SWAP_STRUCT.size)))
            for index in range(num_interiors)
            if interiors_ptr + ((index + 1) * INTERIOR_SWAP_STRUCT.size) <= len(lvz_bytes)
        ]

        num_areas = _read_u32(lvz_bytes, NUM_AREAS_OFFSET)
        areas_ptr = _read_u32(lvz_bytes, AREAS_PTR_OFFSET)
        areas = [
            AreaInfo(*AREA_INFO_STRUCT.unpack_from(lvz_bytes, areas_ptr + (index * AREA_INFO_STRUCT.size)))
            for index in range(num_areas)
            if areas_ptr + ((index + 1) * AREA_INFO_STRUCT.size) <= len(lvz_bytes)
        ]

        return cls(
            archive_name=archive_name,
            level_id=level_id,
            data=lvz_bytes,
            img_bytes=img_bytes,
            resource_table_ptr=resource_table_ptr,
            num_resources=num_resources,
            sector_rows=sector_rows,
            sector_end_header_ptr=sector_end_header_ptr,
            sector_end_start_off=sector_end_start_off,
            sector_headers=sector_headers,
            num_world_sectors=num_world_sectors,
            swap_infos_ptr=swap_infos_ptr,
            num_swap_infos=num_swap_infos,
            level_swaps=level_swaps,
            interiors=interiors,
            areas=areas,
        )

    def read_master_resource_pointers(self) -> dict[int, int]:
        pointers: dict[int, int] = {}
        for res_id in range(self.num_resources):
            off = self.resource_table_ptr + (res_id * RESOURCE_TABLE_ENTRY_STRUCT.size)
            if off + RESOURCE_TABLE_ENTRY_STRUCT.size > len(self.data):
                break
            raw_ptr, _dma_chain, _entry_id = RESOURCE_TABLE_ENTRY_STRUCT.unpack_from(self.data, off)
            if 0 < raw_ptr < len(self.data):
                pointers[res_id] = raw_ptr
        return pointers

    def parse_sector(self, sector_id: int) -> ParsedSector:
        cached = self._sector_cache.get(sector_id)
        if cached is not None:
            return cached

        header = self.sector_headers[sector_id]
        body_start = header.global_tab
        body_end = body_start + max(0, header.file_size - LEVEL_BODY_OFFSET)
        body = self.img_bytes[body_start:body_end]
        if len(body) < 52:
            parsed = ParsedSector(header=header, resources={}, instances_by_pass=[[] for _ in range(SECLIST_END_INDEX)], swap_defs=[])
            self._sector_cache[sector_id] = parsed
            return parsed

        passes_base = 0x08
        pointer_offsets = [self._ptr_if_valid(body, _read_u32(body, 0))]
        pass_offsets = [self._ptr_if_valid(body, _read_u32(body, passes_base + (index * 4))) for index in range(SECLIST_END_INDEX + 1)]
        pointer_offsets.extend(off for off in pass_offsets if off is not None)
        swaps_ptr = self._ptr_if_valid(body, _read_u32(body, 48))
        if swaps_ptr is not None:
            pointer_offsets.append(swaps_ptr)

        resources = self._resource_slices(body, [off for off in pointer_offsets if off is not None])

        instances_by_pass: list[list[SectorInstance]] = [[] for _ in range(SECLIST_END_INDEX)]
        for pass_index in range(SECLIST_END_INDEX):
            start = pass_offsets[pass_index]
            end = pass_offsets[pass_index + 1]
            if start is None or end is None or not (0 <= start <= end <= len(body)):
                continue
            if end - start < SECTOR_ENTRY_SIZE:
                continue
            count = (end - start) // SECTOR_ENTRY_SIZE
            span = memoryview(body)[start : start + (count * SECTOR_ENTRY_SIZE)]
            for row in INSTANCE_STRUCT.iter_unpack(span):
                inst_id, res_id, *matrix = row
                ipl_id = inst_id & 0x7FFF
                instances_by_pass[pass_index].append(
                    SectorInstance(
                        ipl_id=ipl_id,
                        world_id=(self.level_id << 16) | ipl_id,
                        res_id=res_id,
                        pass_index=pass_index,
                        matrix=tuple(float(value) for value in matrix),
                    )
                )

        swap_defs: list[LevelSwap] = []
        num_swaps = struct.unpack_from("<h", body, 44)[0]
        if swaps_ptr is not None and num_swaps > 0:
            for index in range(num_swaps):
                off = swaps_ptr + (index * LEVEL_SWAP_STRUCT.size)
                if off + LEVEL_SWAP_STRUCT.size > len(body):
                    break
                swap_defs.append(LevelSwap(*LEVEL_SWAP_STRUCT.unpack_from(body, off)))

        parsed = ParsedSector(
            header=header,
            resources=resources,
            instances_by_pass=instances_by_pass,
            swap_defs=swap_defs,
        )
        self._sector_cache[sector_id] = parsed
        return parsed

    def iter_reachable_sectors(self) -> dict[int, SectorVisit]:
        if self._reachable_cache is not None:
            return self._reachable_cache

        visits: dict[int, SectorVisit] = {}
        queue: deque[SectorVisit] = deque()
        world_sector_id = 0
        for secy in range(NUM_SECTOR_ROWS):
            row_header_ptr, row_start_off = self.sector_rows[secy]
            next_header_ptr = self.sector_rows[secy + 1][0] if secy + 1 < NUM_SECTOR_ROWS else self.sector_end_header_ptr
            row_count = max(0, (next_header_ptr - row_header_ptr) // SECTOR_HEADER_STRUCT.size)
            for index_in_row in range(row_count):
                queue.append(
                    SectorVisit(
                        sector_id=world_sector_id,
                        source_kind="world",
                        visible=True,
                        origin=_world_sector_origin(row_start_off + index_in_row, secy),
                        interior_chain=False,
                    )
                )
                world_sector_id += 1
        for interior in self.interiors:
            queue.append(
                SectorVisit(
                    sector_id=interior.sector_id,
                    source_kind="interior",
                    visible=interior.default_visible(),
                    origin=_world_sector_origin(interior.secx, interior.secy),
                    interior_chain=True,
                )
            )

        while queue:
            visit = queue.popleft()
            if not (0 <= visit.sector_id < len(self.sector_headers)):
                continue
            existing = visits.get(visit.sector_id)
            if existing is not None:
                existing.visible = existing.visible or visit.visible
                existing.interior_chain = existing.interior_chain or visit.interior_chain
                if SOURCE_PRIORITY[visit.source_kind] < SOURCE_PRIORITY[existing.source_kind]:
                    existing.source_kind = visit.source_kind
                continue

            visits[visit.sector_id] = visit
            sector = self.parse_sector(visit.sector_id)
            for swap in sector.swap_defs:
                queue.append(
                    SectorVisit(
                        sector_id=swap.sector_id,
                        source_kind="swap-sector",
                        visible=swap.default_visible(),
                        origin=visit.origin,
                        interior_chain=visit.interior_chain,
                    )
                )

        self._reachable_cache = visits
        return visits

    def iter_instances(self):
        for sector_id, visit in sorted(self.iter_reachable_sectors().items()):
            sector = self.parse_sector(sector_id)
            for pass_index, rows in enumerate(sector.instances_by_pass):
                for instance in rows:
                    yield visit, pass_index, instance

    @staticmethod
    def _ptr_if_valid(body: bytes, raw_ptr: int) -> int | None:
        offset = _ptr_to_body_offset(raw_ptr)
        if 0 <= offset <= len(body):
            return offset
        return None

    @staticmethod
    def _resource_slices(body: bytes, pointer_offsets: list[int]) -> dict[int, bytes]:
        if len(body) < 8:
            return {}
        resources_ptr = _ptr_to_body_offset(_read_u32(body, 0))
        num_resources = _read_u16(body, 4)
        if not (0 <= resources_ptr < len(body)):
            return {}
        count = min(num_resources, max(0, (len(body) - resources_ptr) // 8))
        entries: list[tuple[int, int]] = []
        for index in range(count):
            off = resources_ptr + (index * 8)
            if off + 8 > len(body):
                break
            res_id = _read_u32(body, off)
            data_ptr = _ptr_to_body_offset(_read_u32(body, off + 4))
            if 0 <= data_ptr < len(body):
                entries.append((res_id, data_ptr))
        boundaries = sorted({len(body), resources_ptr, *[ptr for ptr in pointer_offsets if 0 <= ptr <= len(body)], *[data_ptr for _, data_ptr in entries]})
        slices: dict[int, bytes] = {}
        for res_id, data_ptr in entries:
            next_boundary = next((value for value in boundaries if value > data_ptr), len(body))
            if next_boundary > data_ptr:
                slices.setdefault(res_id, body[data_ptr:next_boundary])
        return slices


def parse_area_resource_table(level: LevelChunk, area: AreaInfo) -> dict[int, bytes]:
    chunk = level.img_bytes[area.file_offset : area.file_offset + area.file_size]
    if len(chunk) < LEVEL_BODY_OFFSET + 8 or _read_u32(chunk, 0) != AREA_IDENT:
        return {}
    body = chunk[LEVEL_BODY_OFFSET:]
    num_resources = _read_u32(body, 0)
    resources_ptr = _ptr_to_body_offset(_read_u32(body, 4))
    if not (0 <= resources_ptr < len(body)):
        return {}
    count = min(num_resources, area.num_resources, max(0, (len(body) - resources_ptr) // 8))
    entries: list[tuple[int, int]] = []
    for index in range(count):
        off = resources_ptr + (index * 8)
        if off + 8 > len(body):
            break
        res_id, _res_aux, data_ptr = struct.unpack_from("<hhI", body, off)
        data_off = _ptr_to_body_offset(data_ptr)
        if 0 <= data_off < len(body):
            entries.append((res_id & 0xFFFF, data_off))
    boundaries = sorted({len(body), resources_ptr, *[data_ptr for _, data_ptr in entries]})
    resources: dict[int, bytes] = {}
    for res_id, data_off in entries:
        next_boundary = next((value for value in boundaries if value > data_off), len(body))
        if next_boundary > data_off:
            resources.setdefault(res_id, body[data_off:next_boundary])
    return resources


def plan_streamed_archive(
    root: Path,
    archive_name: str,
    ide_catalog: dict[str, IdeModel],
    resolver: NameResolver,
) -> StreamedArchivePlan:
    level = LevelChunk.from_archive(root, archive_name)
    total_rows = 0
    linked_rows = 0
    no_link_rows = 0
    unique_ipl_ids: set[int] = set()
    unresolved_ids: set[int] = set()
    unresolved_names: list[str] = []
    world_contributions: dict[str, dict[tuple[int, tuple[float, ...]], dict[int, StreamedPlacement]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    world_model_meta: dict[str, tuple[str, str]] = {}
    interior_contributions: dict[str, dict[tuple[object, ...], dict[int, StreamedPlacement]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    interior_model_meta: dict[str, tuple[str, str, str]] = {}
    txd_exports: dict[str, set[int]] = defaultdict(set)
    model_hidden_alternates: dict[str, bool] = defaultdict(bool)
    placement_attempts = 0
    interior_instances_seen = 0
    interior_sector_ids: set[int] = set()

    area_resource_count = 0
    for area in level.areas:
        area_resource_count += len(parse_area_resource_table(level, area))

    for visit, pass_index, instance in level.iter_instances():
        total_rows += 1
        placement_attempts += 1
        unique_ipl_ids.add(instance.ipl_id)
        is_interior_visit = getattr(visit, "interior_chain", visit.source_kind in {"interior", "swap-sector"})
        if is_interior_visit:
            interior_instances_seen += 1
            interior_sector_ids.add(visit.sector_id)
        model_meta = resolver.resolve_streamed_model_meta(instance.world_id) if hasattr(resolver, "resolve_streamed_model_meta") else None
        if model_meta is not None:
            linked_ipl_id, _model_id, model_name, txd_name, source_file = model_meta
        else:
            link = resolver.resolve_streamed_link(instance.world_id)
            model_name = link[1] if link is not None else None
            linked_ipl_id = link[0] if link is not None else None
            txd_name = ""
            source_file = f"{archive_name}.LVZ"
        if model_name is None and not is_interior_visit:
            no_link_rows += 1
            if instance.world_id not in unresolved_ids and len(unresolved_ids) < MAX_REPORTED_UNRESOLVED:
                unresolved_ids.add(instance.world_id)
                unresolved_names.append(
                    f"{archive_name}: unresolved world id 0x{instance.world_id:08X} "
                    f"(sector={visit.sector_id}, pass={pass_index}, source={visit.source_kind})"
                )
            continue

        absolute_matrix = _matrix_with_origin(instance.matrix, getattr(visit, "origin", (0.0, 0.0, 0.0)))
        placement = StreamedPlacement(
            ipl_id=instance.ipl_id,
            linked_ipl_id=linked_ipl_id,
            world_id=instance.world_id,
            res_id=instance.res_id,
            sector_id=visit.sector_id,
            pass_index=pass_index,
            source_kind=visit.source_kind,
            visible=visit.visible,
            matrix=absolute_matrix,
        )
        if model_name is not None:
            linked_rows += 1
            ide_model = ide_catalog.get(model_name.lower())
            if ide_model is not None:
                if not txd_name or txd_name.lower() == "null":
                    txd_name = ide_model.txd_name
                source_file = ide_model.source_file
            if not visit.visible:
                model_hidden_alternates[model_name] = True
            if is_interior_visit:
                interior_model_meta.setdefault(model_name, (txd_name, source_file, "interior_named"))
                cluster_key = (visit.sector_id, instance.res_id, visit.source_kind)
                cluster = interior_contributions[model_name][cluster_key]
            else:
                world_model_meta.setdefault(model_name, (txd_name, source_file))
                cluster_key = (
                    linked_ipl_id if linked_ipl_id is not None else instance.ipl_id,
                    _matrix_signature(absolute_matrix) if linked_ipl_id is None else (),
                )
                cluster = world_contributions[model_name][cluster_key]
            best = cluster.get(instance.res_id)
            if best is None:
                cluster[instance.res_id] = placement
            else:
                best.placement_count += 1
                if _prefer_placement(placement, best):
                    placement.placement_count = best.placement_count
                    cluster[instance.res_id] = placement
        elif is_interior_visit:
            fallback_name = f"interior_{archive_name.lower()}_{visit.sector_id}_{instance.res_id}"
            interior_model_meta.setdefault(fallback_name, ("", f"{archive_name}.LVZ", "interior_fallback"))
            cluster_key = (visit.sector_id, instance.res_id, visit.source_kind)
            cluster = interior_contributions[fallback_name][cluster_key]
            best = cluster.get(instance.res_id)
            if best is None:
                cluster[instance.res_id] = placement
            else:
                best.placement_count += 1
                if _prefer_placement(placement, best):
                    placement.placement_count = best.placement_count
                    cluster[instance.res_id] = placement

    model_exports: list[StreamedModelPlan] = []
    for model_name in sorted(world_contributions):
        txd_name, source_file = world_model_meta[model_name]
        clusters = world_contributions[model_name]
        cluster_items = sorted(
            clusters.items(),
            key=lambda item: _cluster_sort_key(item[1].values()),
        )
        placements = sorted(
            cluster_items[0][1].values(),
            key=lambda item: (
                not item.visible,
                SOURCE_PRIORITY[item.source_kind],
                item.pass_index,
                item.sector_id,
                item.res_id,
            ),
        )
        model_exports.append(
            StreamedModelPlan(
                model_name=model_name,
                output_name=model_name,
                txd_name=txd_name,
                source_file=source_file,
                placements=placements,
                unresolved_name=False,
                has_hidden_alternates=model_hidden_alternates[model_name],
                export_kind="world_named",
            )
        )
        if txd_name and txd_name.lower() != "null":
            txd_exports[txd_name].update(placement.res_id for placement in placements)

    for model_name in sorted(interior_contributions):
        txd_name, source_file, export_kind = interior_model_meta[model_name]
        clusters = interior_contributions[model_name]
        cluster_items = sorted(
            clusters.items(),
            key=lambda item: _cluster_sort_key(item[1].values()),
        )
        for cluster_index, (_cluster_key, cluster) in enumerate(cluster_items):
            placements = sorted(
                cluster.values(),
                key=lambda item: (
                    not item.visible,
                    SOURCE_PRIORITY[item.source_kind],
                    item.pass_index,
                    item.sector_id,
                    item.res_id,
                ),
            )
            output_name = model_name
            if export_kind == "interior_fallback" and cluster_index:
                output_name = f"{model_name}_{cluster_index}"
            model_exports.append(
                StreamedModelPlan(
                    model_name=model_name,
                    output_name=output_name,
                    txd_name=txd_name,
                    source_file=source_file,
                    placements=placements,
                    unresolved_name=(export_kind == "interior_fallback"),
                    has_hidden_alternates=any(not placement.visible for placement in placements),
                    export_kind=export_kind,
                )
            )
            if txd_name and txd_name.lower() != "null":
                txd_exports[txd_name].update(placement.res_id for placement in placements)

    reachable = level.iter_reachable_sectors()
    interior_export_plans = [model for model in model_exports if model.export_kind.startswith("interior")]
    summary = {
        "master_resources": level.num_resources,
        "world_sectors_loaded": level.num_world_sectors,
        "interior_sectors_loaded": len(level.interiors),
        "swap_sectors_loaded": sum(1 for visit in reachable.values() if visit.source_kind == "swap-sector"),
        "area_chunks_loaded": len(level.areas),
        "area_resources_loaded": area_resource_count,
        "img_rows": total_rows,
        "nonzero_rows": total_rows,
        "unique_ipl_ids": len(unique_ipl_ids),
        "linked_rows": linked_rows,
        "no_link_rows": no_link_rows,
        "planned_models": len(model_exports),
        "planned_world_models": len([model for model in model_exports if model.export_kind == "world_named"]),
        "planned_interior_models": len(interior_export_plans),
        "planned_interior_fallbacks": len([model for model in interior_export_plans if model.export_kind == "interior_fallback"]),
        "interior_sectors_scanned": len(interior_sector_ids),
        "interior_instances_seen": interior_instances_seen,
        "model_clusters": sum(len(clusters) for clusters in world_contributions.values()) + sum(len(clusters) for clusters in interior_contributions.values()),
        "planned_txds": len(txd_exports),
        "planned_res_ids": sum(len(model.placements) for model in model_exports),
        "unresolved_ids": len(unresolved_ids),
        "num_swap_infos": level.num_swap_infos,
        "placement_attempts": placement_attempts,
    }
    return StreamedArchivePlan(
        archive_name=archive_name,
        model_exports=model_exports,
        txd_exports={name: sorted(res_ids) for name, res_ids in sorted(txd_exports.items())},
        summary=summary,
        unresolved_names=unresolved_names,
    )


def _prefer_placement(candidate: StreamedPlacement, current: StreamedPlacement) -> bool:
    return (
        (not candidate.visible, SOURCE_PRIORITY[candidate.source_kind], candidate.pass_index, candidate.sector_id, candidate.res_id)
        < (not current.visible, SOURCE_PRIORITY[current.source_kind], current.pass_index, current.sector_id, current.res_id)
    )


def _cluster_sort_key(placements: object) -> tuple[int, int, int, int, int, int]:
    items = list(placements)
    unique_res_ids = len(items)
    visible_count = sum(1 for item in items if item.visible)
    world_count = sum(1 for item in items if item.source_kind == "world")
    total_uses = sum(item.placement_count for item in items)
    best_priority = min(SOURCE_PRIORITY[item.source_kind] for item in items)
    best_pass = min(item.pass_index for item in items)
    return (-unique_res_ids, -visible_count, -world_count, -total_uses, best_priority, best_pass)
