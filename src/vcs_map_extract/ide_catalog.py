from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from .models import IdeModel


SECTIONS = {"objs", "tobj"}


def parse_ide_directory(ide_dir: Path) -> OrderedDict[str, IdeModel]:
    if not ide_dir.is_dir():
        raise FileNotFoundError(f"IDE directory not found: {ide_dir}")

    catalog: OrderedDict[str, IdeModel] = OrderedDict()
    for ide_path in sorted(ide_dir.glob("*.ide")):
        current_section: str | None = None
        for raw_line in ide_path.read_text(errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lowered = line.lower()
            if lowered in SECTIONS:
                current_section = lowered
                continue
            if lowered == "end":
                current_section = None
                continue
            if current_section not in SECTIONS:
                continue

            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 3:
                continue

            try:
                model_id = int(parts[0], 10)
            except ValueError:
                continue

            model = IdeModel(
                model_id=model_id,
                model_name=parts[1],
                txd_name=parts[2],
                section=current_section,
                source_file=ide_path.name,
            )
            catalog.setdefault(model.model_name.lower(), model)
    return catalog
