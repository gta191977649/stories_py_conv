from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math


@dataclass(slots=True)
class IplTransform:
    model_id: int
    model_name: str
    interior: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    source_file: str
    entity_id: int | None = None


@dataclass(slots=True)
class IplInstance:
    instance_id: int
    model_name: str
    interior: int
    source_file: str


@dataclass(slots=True)
class IplSummary:
    inst_count_by_file: dict[str, int] = field(default_factory=dict)
    nonzero_interior_by_file: dict[str, int] = field(default_factory=dict)
    nonzero_instances: list[IplInstance] = field(default_factory=list)
    transforms_by_model: dict[str, list[IplTransform]] = field(default_factory=dict)
    transforms_by_id: dict[int, list[IplTransform]] = field(default_factory=dict)
    transforms_by_entity_id: dict[int, IplTransform] = field(default_factory=dict)


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def parse_ipl_directory(ipl_dir: Path) -> IplSummary:
    summary = IplSummary()
    if not ipl_dir.is_dir():
        return summary

    for ipl_path in sorted(ipl_dir.glob("*.ipl")):
        in_inst = False
        inst_count = 0
        nonzero_count = 0
        for raw_line in ipl_path.read_text(errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lowered = line.lower()
            if lowered == "inst":
                in_inst = True
                continue
            if lowered == "end":
                in_inst = False
                continue
            if not in_inst:
                continue

            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 13:
                continue
            try:
                instance_id = int(parts[0], 10)
                interior = int(parts[2], 10)
            except ValueError:
                continue

            px = _parse_float(parts[3])
            py = _parse_float(parts[4])
            pz = _parse_float(parts[5])
            rx = _parse_float(parts[9])
            ry = _parse_float(parts[10])
            rz = _parse_float(parts[11])
            rw = _parse_float(parts[12])

            inst_count += 1
            if None not in (px, py, pz, rx, ry, rz, rw):
                rotation = (float(rx), float(ry), float(rz), float(rw))
                if all(math.isfinite(value) for value in (*rotation, float(px), float(py), float(pz))):
                    summary.transforms_by_model.setdefault(parts[1].lower(), []).append(
                        transform := IplTransform(
                            model_id=instance_id,
                            model_name=parts[1],
                            interior=interior,
                            position=(float(px), float(py), float(pz)),
                            rotation=rotation,
                            source_file=ipl_path.name,
                        )
                    )
                    summary.transforms_by_id.setdefault(instance_id, []).append(transform)
            if interior != 0:
                nonzero_count += 1
                summary.nonzero_instances.append(
                    IplInstance(
                        instance_id=instance_id,
                        model_name=parts[1],
                        interior=interior,
                        source_file=ipl_path.name,
                    )
                )

        summary.inst_count_by_file[ipl_path.name] = inst_count
        summary.nonzero_interior_by_file[ipl_path.name] = nonzero_count

    return summary


def merge_ipl_summaries(*summaries: IplSummary) -> IplSummary:
    merged = IplSummary()
    for summary in summaries:
        merged.inst_count_by_file.update(summary.inst_count_by_file)
        merged.nonzero_interior_by_file.update(summary.nonzero_interior_by_file)
        merged.nonzero_instances.extend(summary.nonzero_instances)
        for model_name, transforms in summary.transforms_by_model.items():
            merged.transforms_by_model.setdefault(model_name, []).extend(transforms)
        for model_id, transforms in summary.transforms_by_id.items():
            merged.transforms_by_id.setdefault(model_id, []).extend(transforms)
        merged.transforms_by_entity_id.update(summary.transforms_by_entity_id)
    return merged
