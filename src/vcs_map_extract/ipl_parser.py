from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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

            inst_count += 1
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
