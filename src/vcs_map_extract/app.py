from __future__ import annotations

import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from .constants import ARCHIVE_ORDER, EXPECTED_FILES, STANDARD_ARCHIVES, STREAMED_ARCHIVES
from .ide_catalog import parse_ide_directory
from .img_io import ImgDirectoryEntry, ImgReader
from .ipl_parser import parse_ipl_directory
from .models import ReportData, StreamedArchivePlan
from .name_resolver import NameResolver
from .packimg import write_packed_img
from .pure_backend import DecodedTexture, run_conversion_jobs, write_txd_from_decoded_textures
from .report import write_report
from .streamed_backend import export_streamed_archive
from .streamed_world import plan_streamed_archive
from .utils import normalize_input_root, safe_mkdir, sanitize_filename


TEXTURE_EXTENSIONS = (".xtx", ".tex", ".chk")


def _log(message: str) -> None:
    print(message, flush=True)


def _validate_root(root: Path) -> None:
    if not root.is_dir():
        raise FileNotFoundError(f"Input root does not exist: {root}")
    for archive_name, names in EXPECTED_FILES.items():
        for name in names:
            if not (root / name).exists():
                raise FileNotFoundError(f"Missing required {archive_name} input file: {root / name}")


def _collect_standard_entries(root: Path, archive_name: str) -> tuple[ImgReader, dict[str, ImgDirectoryEntry]]:
    img_path = root / f"{archive_name}.IMG"
    reader = ImgReader(img_path)
    mapping: dict[str, ImgDirectoryEntry] = {}
    for entry in reader.entries:
        mapping[entry.name.lower()] = entry
    return reader, mapping


def _queue_standard_jobs(
    temp_root: Path,
    root: Path,
    output_root: Path,
    ide_catalog: dict,
    summary: dict[str, dict[str, int]],
) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for archive_name in STANDARD_ARCHIVES:
        archive_dir = output_root / archive_name
        safe_mkdir(archive_dir)
        reader, entries = _collect_standard_entries(root, archive_name)
        summary[archive_name]["img_entries"] = len(entries)
        _log(f"[standard] scanning {archive_name}: {len(entries)} IMG entries")
        queued_models: set[str] = set()
        queued_txds: set[str] = set()
        queued_cols: set[str] = set()

        for ide_model in ide_catalog.values():
            mdl_key = f"{ide_model.model_name}.mdl".lower()
            if mdl_key in entries and mdl_key not in queued_models:
                queued_models.add(mdl_key)
                entry = entries[mdl_key]
                raw_path = temp_root / archive_name / entry.name
                safe_mkdir(raw_path.parent)
                raw_path.write_bytes(reader.read_entry(entry))
                output_path = archive_dir / f"{sanitize_filename(ide_model.model_name)}.dff"
                jobs.append({"type": "mdl", "archive": archive_name, "input": str(raw_path), "output": str(output_path)})

            txd_base = ide_model.txd_name.lower()
            if txd_base and txd_base != "null":
                for ext in TEXTURE_EXTENSIONS:
                    txd_key = f"{txd_base}{ext}"
                    if txd_key not in entries or txd_key in queued_txds:
                        continue
                    queued_txds.add(txd_key)
                    entry = entries[txd_key]
                    raw_path = temp_root / archive_name / entry.name
                    safe_mkdir(raw_path.parent)
                    raw_path.write_bytes(reader.read_entry(entry))
                    output_path = archive_dir / f"{sanitize_filename(ide_model.txd_name)}.txd"
                    jobs.append({"type": "tex", "archive": archive_name, "input": str(raw_path), "output": str(output_path)})
                    break

            col_key = f"{ide_model.model_name}.col2".lower()
            if col_key in entries and col_key not in queued_cols:
                queued_cols.add(col_key)
                entry = entries[col_key]
                raw_path = temp_root / archive_name / entry.name
                safe_mkdir(raw_path.parent)
                raw_path.write_bytes(reader.read_entry(entry))
                output_path = archive_dir / f"{sanitize_filename(ide_model.model_name)}.col"
                jobs.append({"type": "col2", "archive": archive_name, "input": str(raw_path), "output": str(output_path)})

        summary[archive_name]["queued_models"] = len(queued_models)
        summary[archive_name]["queued_txds"] = len(queued_txds)
        summary[archive_name]["queued_cols"] = len(queued_cols)
        _log(
            f"[standard] queued {archive_name}: "
            f"{len(queued_models)} dff, {len(queued_txds)} txd, {len(queued_cols)} col"
        )
    return jobs


def _summarize_standard_results(results: list[dict[str, object]], summary: dict[str, dict[str, int]], report: ReportData) -> None:
    per_archive = defaultdict(lambda: defaultdict(int))
    for result in results:
        archive_name = str(result.get("archive", ""))
        if archive_name:
            per_archive[archive_name]["completed_jobs"] += int(bool(result.get("ok")))
            per_archive[archive_name]["failed_jobs"] += int(not result.get("ok"))
        if not result.get("ok"):
            report.unresolved_streamed_names.append(
                f"{result.get('job')} failed for {result.get('input')}: {result.get('error')}"
            )
    for archive_name, metrics in per_archive.items():
        summary[archive_name].update(metrics)


def _ensure_knackers_txd(output_root: Path) -> None:
    target = output_root / "knackers.txd"
    if target.exists():
        return
    for archive_name in ARCHIVE_ORDER:
        candidate = output_root / archive_name / "knackers.txd"
        if candidate.exists():
            shutil.copyfile(candidate, target)
            return
    target.write_bytes(b"")


def run(input_path: str, output_path: str, packimg: bool) -> int:
    root = normalize_input_root(input_path)
    output_root = Path(output_path).expanduser().resolve()
    _log(f"[run] input={root}")
    _log(f"[run] output={output_root}")
    _validate_root(root)
    safe_mkdir(output_root)
    for archive_name in ARCHIVE_ORDER:
        safe_mkdir(output_root / archive_name)
    for archive_name in STREAMED_ARCHIVES:
        stale_knackers = output_root / archive_name / "knackers.txd"
        if stale_knackers.exists():
            stale_knackers.unlink()

    ide_catalog = parse_ide_directory(root / "ide")
    resolver = NameResolver(ide_catalog)
    report = ReportData()
    summary: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ipl_summary = parse_ipl_directory(root / "ipl")
    for source_file in sorted(ipl_summary.inst_count_by_file):
        report.ipl_diagnostics.append(
            f"{source_file}: inst_rows={ipl_summary.inst_count_by_file[source_file]}, "
            f"nonzero_interiors={ipl_summary.nonzero_interior_by_file.get(source_file, 0)}"
        )
    if any(ipl_summary.nonzero_interior_by_file.values()):
        report.ipl_diagnostics.append(
            f"nonzero interior IPL rows detected: {len(ipl_summary.nonzero_instances)}"
        )
    else:
        report.ipl_diagnostics.append(
            "ipl/*.ipl did not expose nonzero interior ids in this dump; LVZ interior swaps were used instead"
        )

    with tempfile.TemporaryDirectory(prefix="vcs_map_extract_raw_") as tmpdir:
        standard_jobs = _queue_standard_jobs(Path(tmpdir), root, output_root, ide_catalog, summary)
        _log(f"[standard] running {len(standard_jobs)} conversion jobs")
        standard_results = run_conversion_jobs(standard_jobs) if standard_jobs else []
        _summarize_standard_results(standard_results, summary, report)

    streamed_plans: list[StreamedArchivePlan] = []
    for archive_name in STREAMED_ARCHIVES:
        _log(f"[streamed] analysing {archive_name}")
        plan = plan_streamed_archive(root, archive_name, ide_catalog, resolver)
        streamed_plans.append(plan)
        summary[archive_name].update(plan.summary)
        report.unresolved_streamed_names.extend(plan.unresolved_names)

    global_knackers_textures: dict[str, DecodedTexture] = {}
    for plan in streamed_plans:
        archive_name = plan.archive_name
        if not plan.model_exports and not plan.txd_exports:
            continue
        _log(
            f"[streamed] exporting {archive_name}: "
            f"{len(plan.model_exports)} models, {len(plan.txd_exports)} txd groups"
        )
        try:
            metrics = export_streamed_archive(
                archive_name,
                root,
                output_root,
                plan,
                report,
                global_knackers_textures=global_knackers_textures,
            )
            summary[archive_name].update(metrics)
            _log(
                f"[streamed] finished {archive_name}: "
                f"{metrics.get('exported_models', 0)} models, "
                f"{metrics.get('exported_txds', 0)} txds, "
                f"{metrics.get('missing_res_ids', 0)} missing resources"
            )
        except Exception as exc:
            report.unresolved_streamed_names.append(f"{archive_name}: streamed export failed: {exc}")
            summary[archive_name]["streamed_failed"] = 1
            _log(f"[streamed] FAILED {archive_name}: {exc}")

    if global_knackers_textures:
        write_txd_from_decoded_textures(output_root / "knackers.txd", list(global_knackers_textures.values()))
        _log("[streamed] wrote root knackers.txd")

    _ensure_knackers_txd(output_root)

    report.summary_by_archive = {name: dict(summary[name]) for name in ARCHIVE_ORDER}
    report.missing_models = sorted(
        model.model_name
        for model in ide_catalog.values()
        if not any((output_root / archive / f"{sanitize_filename(model.model_name)}.dff").exists() for archive in ARCHIVE_ORDER)
    )
    missing_by_source_file: dict[str, list[str]] = defaultdict(list)
    missing_lookup = set(report.missing_models)
    for model in ide_catalog.values():
        if model.model_name in missing_lookup:
            missing_by_source_file[model.source_file].append(model.model_name)
    report.missing_models_by_source_file = {
        source_file: sorted(names)
        for source_file, names in sorted(missing_by_source_file.items())
    }

    if packimg:
        _log("[packimg] writing OUTPUT/vcs_map.img")
        _packed_path, conflicts = write_packed_img(output_root)
        report.duplicate_pack_conflicts.extend(conflicts)

    _log("[report] writing report.txt")
    write_report(output_root / "report.txt", report)
    _log("[done] extraction finished")
    return 0
