from __future__ import annotations

import struct
from collections import defaultdict
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
SECTOR_HEADER_PTR_OFFSET = LEVEL_BODY_OFFSET + 0x04
SECLIST_END_INDEX = 8
SECTOR_ENTRY_SIZE = 0x50
WRLD_IDENT = 0x57524C44
MAX_REPORTED_UNRESOLVED = 128
SECTOR_HEADER_STRUCT = struct.Struct("<IIIIIIIHH")
INSTANCE_STRUCT = struct.Struct("<HH12x16f")


def _read_u16(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<H", blob, offset)[0]


def _read_u32(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def _ptr_to_body_offset(raw_ptr: int) -> int:
    return raw_ptr - LEVEL_BODY_OFFSET


def _iter_sector_headers(lvz_bytes: bytes):
    lvz = maybe_decompress(lvz_bytes)
    header_table = _read_u32(lvz, SECTOR_HEADER_PTR_OFFSET)
    for index in range(4096):
        off = header_table + (index * SECTOR_HEADER_STRUCT.size)
        if off + SECTOR_HEADER_STRUCT.size > len(lvz):
            break
        ident, shrink, file_size, data_size, reloc_tab, num_relocs, global_tab, num_classes, num_funcs = SECTOR_HEADER_STRUCT.unpack_from(lvz, off)
        if ident != WRLD_IDENT:
            break
        yield {
            "index": index,
            "file_size": file_size,
            "data_size": data_size,
            "reloc_tab": reloc_tab,
            "num_relocs": num_relocs,
            "global_tab": global_tab,
        }


def _iter_sector_instances(root: Path, archive_name: str):
    lvz_bytes = (root / f"{archive_name}.LVZ").read_bytes()
    img_bytes = (root / f"{archive_name}.IMG").read_bytes()
    for sector in _iter_sector_headers(lvz_bytes):
        body_start = sector["global_tab"]
        body_end = body_start + max(0, sector["file_size"] - LEVEL_BODY_OFFSET)
        body = img_bytes[body_start:body_end]
        if len(body) < 48:
            continue
        passes_base = 0x08
        first_off = _ptr_to_body_offset(_read_u32(body, passes_base))
        end_off = _ptr_to_body_offset(_read_u32(body, passes_base + (SECLIST_END_INDEX * 4)))
        if not (0 <= first_off <= end_off <= len(body)):
            continue
        if end_off - first_off < SECTOR_ENTRY_SIZE:
            continue
        count = (end_off - first_off) // SECTOR_ENTRY_SIZE
        span = memoryview(body)[first_off : first_off + (count * SECTOR_ENTRY_SIZE)]
        for row in INSTANCE_STRUCT.iter_unpack(span):
            inst_id, res_id, *matrix = row
            yield sector["index"], (inst_id & 0x7FFF), res_id, tuple(float(value) for value in matrix)


def plan_streamed_archive(
    root: Path,
    archive_name: str,
    ide_catalog: dict[str, IdeModel],
    resolver: NameResolver,
) -> StreamedArchivePlan:
    level_id = LEVEL_IDS[archive_name]
    total_rows = 0
    linked_rows = 0
    unique_ipl_ids: set[int] = set()
    unresolved_ids: set[int] = set()
    unresolved_names: list[str] = []
    by_model_res: dict[str, dict[int, tuple[int, tuple[float, ...], int]]] = defaultdict(dict)
    model_meta: dict[str, tuple[str, str]] = {}
    txd_exports: dict[str, set[int]] = defaultdict(set)
    world_to_model_name = {
        world_id: resolver.model_id_to_name[model_id]
        for world_id, model_id in resolver.streamed_links.items()
        if (world_id >> 16) == level_id and model_id in resolver.model_id_to_name
    }

    for sector_index, ipl_id, res_id, matrix in _iter_sector_instances(root, archive_name):
        total_rows += 1
        unique_ipl_ids.add(ipl_id)
        world_id = (level_id << 16) | ipl_id
        model_name = world_to_model_name.get(world_id)
        if model_name is None:
            if world_id not in unresolved_ids and len(unresolved_ids) < MAX_REPORTED_UNRESOLVED:
                unresolved_ids.add(world_id)
                unresolved_names.append(f"{archive_name}: unresolved world id 0x{world_id:08X}")
            continue

        linked_rows += 1
        ide_model = ide_catalog.get(model_name.lower())
        txd_name = ide_model.txd_name if ide_model is not None else ""
        source_file = ide_model.source_file if ide_model is not None else f"{archive_name}.LVZ"
        model_meta.setdefault(model_name, (txd_name, source_file))
        best = by_model_res[model_name].get(res_id)
        if best is None:
            by_model_res[model_name][res_id] = (1, matrix, sector_index)
        else:
            by_model_res[model_name][res_id] = (best[0] + 1, best[1], min(best[2], sector_index))

    model_exports: list[StreamedModelPlan] = []
    for model_name in sorted(by_model_res):
        txd_name, source_file = model_meta[model_name]
        best_res_id, (count, matrix, sector_index) = min(
            by_model_res[model_name].items(),
            key=lambda item: (-item[1][0], item[1][2], item[0]),
        )
        model_exports.append(
            StreamedModelPlan(
                model_name=model_name,
                txd_name=txd_name,
                source_file=source_file,
                placements=[StreamedPlacement(ipl_id=sector_index, res_id=best_res_id, matrix=matrix)],
                unresolved_name=False,
            )
        )
        if txd_name and txd_name.lower() != "null":
            txd_exports[txd_name].add(best_res_id)

    summary = {
        "img_rows": total_rows,
        "nonzero_rows": total_rows,
        "unique_ipl_ids": len(unique_ipl_ids),
        "linked_rows": linked_rows,
        "planned_models": len(model_exports),
        "planned_txds": len(txd_exports),
        "unresolved_ids": len(unresolved_ids),
    }
    return StreamedArchivePlan(
        archive_name=archive_name,
        model_exports=model_exports,
        txd_exports={name: sorted(res_ids) for name, res_ids in sorted(txd_exports.items())},
        summary=summary,
        unresolved_names=unresolved_names,
    )
