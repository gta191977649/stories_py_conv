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
    lines.append("Missing Models By IDE")
    if report.missing_models_by_source_file:
        for source_file in sorted(report.missing_models_by_source_file):
            names = report.missing_models_by_source_file[source_file]
            lines.append(f"{source_file}: {len(names)}")
            lines.extend(f"  {name}" for name in names)
    else:
        lines.append("<none>")

    lines.append("")
    lines.append("VCS Names Coverage")
    if report.vcsnames_coverage:
        for key in sorted(report.vcsnames_coverage):
            lines.append(f"{key}={report.vcsnames_coverage[key]}")
    else:
        lines.append("<none>")

    lines.append("")
    lines.append("Missing Geometry VCS Names")
    lines.extend(report.missing_geometry_vcsnames or ["<none>"])

    lines.append("")
    lines.append("Non-Geometry VCS Names")
    lines.extend(report.non_geometry_vcsnames or ["<none>"])

    lines.append("")
    lines.append("IPL Diagnostics")
    lines.extend(report.ipl_diagnostics or ["<none>"])

    lines.append("")
    lines.append("Unresolved Streamed Names")
    lines.extend(report.unresolved_streamed_names or ["<none>"])

    lines.append("")
    lines.append("Streamed Diagnostics")
    lines.extend(report.streamed_diagnostics or ["<none>"])

    lines.append("")
    lines.append("Interior Diagnostics")
    lines.extend(report.interior_diagnostics or ["<none>"])

    lines.append("")
    lines.append("Duplicate Pack Conflicts")
    lines.extend(report.duplicate_pack_conflicts or ["<none>"])

    lines.append("")
    lines.append("Streamed Texture Conflicts")
    lines.extend(report.streamed_texture_conflicts or ["<none>"])

    lines.append("")
    lines.append("Knackers Texture Conflicts")
    lines.extend(report.knackers_texture_conflicts or ["<none>"])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
