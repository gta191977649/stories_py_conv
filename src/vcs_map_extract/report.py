from __future__ import annotations

from pathlib import Path

from .models import ReportData


def write_report(path: Path, report: ReportData) -> None:
    lines: list[str] = []
    lines.append("Summary")
    for archive in sorted(report.summary_by_archive):
        metrics = report.summary_by_archive[archive]
        bits = ", ".join(f"{key}={metrics[key]}" for key in sorted(metrics))
        lines.append(f"{archive}: {bits}")

    lines.append("")
    lines.append("Missing Models")
    lines.extend(report.missing_models or ["<none>"])

    lines.append("")
    lines.append("Unresolved Streamed Names")
    lines.extend(report.unresolved_streamed_names or ["<none>"])

    lines.append("")
    lines.append("Duplicate Pack Conflicts")
    lines.extend(report.duplicate_pack_conflicts or ["<none>"])

    lines.append("")
    lines.append("Knackers Texture Conflicts")
    lines.extend(report.knackers_texture_conflicts or ["<none>"])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
