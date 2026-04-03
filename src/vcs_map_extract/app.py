from __future__ import annotations

import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .constants import ARCHIVE_ORDER, EXPECTED_FILES, STANDARD_ARCHIVES, STREAMED_ARCHIVES
from .game_dat import GameDat, decode_game_dat
from .ide_catalog import parse_ide_directory
from .img_io import ImgDirectoryEntry, ImgReader
from .ipl_parser import merge_ipl_summaries, parse_ipl_directory
from .models import ReportData, StreamedArchivePlan
from .name_resolver import NameResolver
from .packimg import set_log_sink as set_packimg_log_sink, write_packed_img
from .progress import ProgressDisplay
from .report import write_report
from .utils import normalize_input_root, safe_mkdir, sanitize_filename

if TYPE_CHECKING:
    from .pure_backend import DecodedTexture


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


def _validate_game_dat(root: Path) -> Path:
    game_dat_path = root / "GAME.dat"
    if not game_dat_path.is_file():
        raise FileNotFoundError(f"Missing required GAME.dat input file: {game_dat_path}")
    return game_dat_path


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
    resolver: NameResolver,
    summary: dict[str, dict[str, int]],
    on_archive_done: Callable[[str, int, int, int], None] | None = None,
) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for archive_name in STANDARD_ARCHIVES:
        archive_dir = output_root / archive_name
        safe_mkdir(archive_dir)
        reader, entries = _collect_standard_entries(root, archive_name)
        summary[archive_name]["img_entries"] = len(entries)
        if on_archive_done is None:
            _log(f"[standard] scanning {archive_name}: {len(entries)} IMG entries")
        queued_models: set[str] = set()
        queued_txds: set[str] = set()
        queued_cols: set[str] = set()
        queued_output_models: set[str] = set()
        queued_output_cols: set[str] = set()

        for ide_model in ide_catalog.values():
            mdl_key = f"{ide_model.model_name}.mdl".lower()
            if mdl_key in entries and mdl_key not in queued_models:
                output_stem = sanitize_filename(resolver.canonical_model_name(ide_model.model_id, ide_model.model_name))
                if output_stem in queued_output_models:
                    queued_models.add(mdl_key)
                else:
                    queued_output_models.add(output_stem)
                    queued_models.add(mdl_key)
                    entry = entries[mdl_key]
                    raw_path = temp_root / archive_name / entry.name
                    safe_mkdir(raw_path.parent)
                    raw_path.write_bytes(reader.read_entry(entry))
                    output_path = archive_dir / f"{output_stem}.dff"
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
                output_stem = sanitize_filename(resolver.canonical_model_name(ide_model.model_id, ide_model.model_name))
                queued_cols.add(col_key)
                if output_stem in queued_output_cols:
                    continue
                queued_output_cols.add(output_stem)
                entry = entries[col_key]
                raw_path = temp_root / archive_name / entry.name
                safe_mkdir(raw_path.parent)
                raw_path.write_bytes(reader.read_entry(entry))
                output_path = archive_dir / f"{output_stem}.col"
                jobs.append({"type": "col2", "archive": archive_name, "input": str(raw_path), "output": str(output_path)})

        summary[archive_name]["queued_models"] = len(queued_models)
        summary[archive_name]["queued_txds"] = len(queued_txds)
        summary[archive_name]["queued_cols"] = len(queued_cols)
        if on_archive_done is None:
            _log(
                f"[standard] queued {archive_name}: "
                f"{len(queued_models)} dff, {len(queued_txds)} txd, {len(queued_cols)} col"
            )
        if on_archive_done is not None:
            on_archive_done(archive_name, len(queued_models), len(queued_txds), len(queued_cols))
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


def _cleanup_stale_generated_outputs(output_root: Path) -> None:
    for archive_name in STREAMED_ARCHIVES:
        archive_dir = output_root / archive_name
        for path in archive_dir.glob("*__int_*.*"):
            if path.is_file():
                path.unlink()
        for path in archive_dir.glob("interior_*.*"):
            if path.is_file():
                path.unlink()
        stale_interior_generic = archive_dir / "interior_generic.txd"
        if stale_interior_generic.exists():
            stale_interior_generic.unlink()


def _clean_output_dir(output_root: Path) -> None:
    if not output_root.exists():
        safe_mkdir(output_root)
        return
    for child in output_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _progress_phase_count(clean: bool, export: bool, buildimg: bool, decode_dat: bool) -> int:
    phases = 0
    phases += int(clean)
    phases += int(decode_dat)
    if export:
        phases += 6
        phases += int(buildimg)
    return max(1, phases)


def run(input_path: str, output_path: str, clean: bool, export: bool, buildimg: bool, decode_dat: bool = False) -> int:
    root = normalize_input_root(input_path)
    output_root = Path(output_path).expanduser().resolve()
    safe_mkdir(output_root)
    progress = ProgressDisplay(_progress_phase_count(clean, export, buildimg, decode_dat))
    try:
        if clean:
            progress.start_phase("Clean Output", 1, unit="task")
            _clean_output_dir(output_root)
            safe_mkdir(output_root)
            progress.advance(detail=output_root.name)
            progress.finish_phase(summary=f"Cleaned {output_root}")
            if not export and not decode_dat:
                return 0

        if decode_dat:
            game_dat_path = _validate_game_dat(root)
            progress.start_phase("Decode GAME.dat", 1, unit="task")
            stats = decode_game_dat(game_dat_path, output_root)
            progress.advance(detail=game_dat_path.name)
            progress.finish_phase(
                summary=(
                    f"Decoded GAME.dat: wrote {stats.ide_files_written} IDE files, "
                    f"{stats.ipl_files_written} IPL files, {stats.model_infos_total} model infos"
                )
            )
            if stats.unsupported_weapons or stats.unsupported_vehicles or stats.unsupported_peds:
                progress.log(
                    "[decode-dat] skipped unsupported non-map sections: "
                    f"weapons={stats.unsupported_weapons}, vehicles={stats.unsupported_vehicles}, peds={stats.unsupported_peds}"
                )
            if not export:
                return 0

        if not export:
            raise ValueError("Export mode was not selected. Use --export for model extraction.")

        _validate_root(root)
        from .pure_backend import run_conversion_jobs, write_txd_from_decoded_textures
        from .streamed_backend import export_streamed_archive, set_log_sink as set_streamed_log_sink
        from .streamed_world import plan_streamed_archive

        progress.start_phase("Load Metadata", 1, unit="task")
        for archive_name in ARCHIVE_ORDER:
            safe_mkdir(output_root / archive_name)
        _cleanup_stale_generated_outputs(output_root)
        for archive_name in STREAMED_ARCHIVES:
            stale_knackers = output_root / archive_name / "knackers.txd"
            if stale_knackers.exists():
                stale_knackers.unlink()

        ide_catalog = parse_ide_directory(root / "ide")
        game_dat = None
        game_dat_models = None
        game_dat_path = root / "GAME.dat"
        if game_dat_path.is_file():
            try:
                game_dat = GameDat.from_path(game_dat_path)
                game_dat_models = game_dat.model_info_by_id
            except Exception as exc:
                progress.log(f"[names] failed to read {game_dat_path}: {exc}")
        resolver = NameResolver(ide_catalog, game_dat_models=game_dat_models)
        report = ReportData()
        summary: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        ipl_summary = parse_ipl_directory(root / "ipl")
        if game_dat is not None:
            ipl_summary = merge_ipl_summaries(ipl_summary, game_dat.build_ipl_summary())
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
        progress.advance(detail=f"{len(ide_catalog)} IDE models")
        progress.finish_phase(summary="Loaded IDE, IPL, and GAME.dat metadata")

        with tempfile.TemporaryDirectory(prefix="vcs_map_extract_raw_") as tmpdir:
            progress.start_phase("Queue Standard Assets", len(STANDARD_ARCHIVES), unit="archive")
            standard_jobs = _queue_standard_jobs(
                Path(tmpdir),
                root,
                output_root,
                ide_catalog,
                resolver,
                summary,
                on_archive_done=lambda archive_name, models, txds, cols: progress.advance(
                    detail=f"{archive_name}: {models} dff, {txds} txd, {cols} col"
                ),
            )
            progress.finish_phase(summary=f"Queued {len(standard_jobs)} standard conversion jobs")

            progress.start_phase("Convert Standard Assets", len(standard_jobs), unit="job")
            standard_results = run_conversion_jobs(
                standard_jobs,
                log=progress.log,
                log_success=False,
                on_job_done=lambda job, result: progress.advance(
                    detail=f"{job['archive']} {job['type']} {'ok' if result.get('ok') else 'failed'}"
                ),
            ) if standard_jobs else []
            progress.finish_phase(summary=f"Completed {len(standard_results)} standard jobs")
            _summarize_standard_results(standard_results, summary, report)

        progress.start_phase("Analyse Streamed Archives", len(STREAMED_ARCHIVES), unit="archive")
        streamed_plans: list[StreamedArchivePlan] = []
        previous_streamed_sink = set_streamed_log_sink(None)
        try:
            for archive_name in STREAMED_ARCHIVES:
                plan = plan_streamed_archive(root, archive_name, ide_catalog, resolver)
                streamed_plans.append(plan)
                summary[archive_name].update(plan.summary)
                report.unresolved_streamed_names.extend(plan.unresolved_names)
                progress.advance(detail=f"{archive_name}: {len(plan.model_exports)} models")
        finally:
            set_streamed_log_sink(previous_streamed_sink)
        progress.finish_phase(summary="Planned streamed archive exports")

        total_streamed_models = sum(len(plan.model_exports) for plan in streamed_plans)
        progress.start_phase("Export Streamed Models", total_streamed_models, unit="model")
        global_knackers_textures: dict[str, "DecodedTexture"] = {}
        previous_streamed_sink = set_streamed_log_sink(None)
        try:
            for plan in streamed_plans:
                archive_name = plan.archive_name
                if not plan.model_exports and not plan.txd_exports:
                    continue
                try:
                    metrics = export_streamed_archive(
                        archive_name,
                        root,
                        output_root,
                        plan,
                        report,
                        global_knackers_textures=global_knackers_textures,
                        ipl_summary=ipl_summary,
                        on_model_done=lambda archive, model, exported: progress.advance(
                            detail=f"{archive}: {model.output_name} {'exported' if exported else 'skipped'}"
                        ),
                    )
                    summary[archive_name].update(metrics)
                except Exception as exc:
                    report.unresolved_streamed_names.append(f"{archive_name}: streamed export failed: {exc}")
                    summary[archive_name]["streamed_failed"] = 1
                    progress.log(f"[streamed] FAILED {archive_name}: {exc}")
        finally:
            set_streamed_log_sink(previous_streamed_sink)
        progress.finish_phase(summary="Finished streamed model export")

        if global_knackers_textures:
            write_txd_from_decoded_textures(output_root / "knackers.txd", list(global_knackers_textures.values()))
        _ensure_knackers_txd(output_root)

        report.summary_by_archive = {name: dict(summary[name]) for name in ARCHIVE_ORDER}
        exported_stems_by_archive = {
            archive: {
                path.stem
                for path in (output_root / archive).glob("*.dff")
            }
            for archive in ARCHIVE_ORDER
        }
        report.missing_models = sorted(
            resolver.canonical_model_name(model.model_id, model.model_name)
            for model in ide_catalog.values()
            if not any(
                sanitize_filename(resolver.canonical_model_name(model.model_id, model.model_name)) == stem
                for archive in ARCHIVE_ORDER
                for stem in exported_stems_by_archive[archive]
            )
        )
        missing_by_source_file: dict[str, list[str]] = defaultdict(list)
        missing_lookup = set(report.missing_models)
        for model in ide_catalog.values():
            canonical_name = resolver.canonical_model_name(model.model_id, model.model_name)
            if canonical_name in missing_lookup:
                missing_by_source_file[model.source_file].append(canonical_name)
        report.missing_models_by_source_file = {
            source_file: sorted(names)
            for source_file, names in sorted(missing_by_source_file.items())
        }

        if buildimg:
            progress.start_phase("Build IMG", 1, unit="task")
            previous_packimg_sink = set_packimg_log_sink(None)
            try:
                _packed_path, conflicts = write_packed_img(output_root)
            finally:
                set_packimg_log_sink(previous_packimg_sink)
            report.duplicate_pack_conflicts.extend(conflicts)
            progress.advance(detail="vcs_map.img")
            progress.finish_phase(summary="Packed vcs_map.img")

        progress.start_phase("Write Report", 1, unit="task")
        write_report(output_root / "report.txt", report)
        progress.advance(detail="report.txt")
        progress.finish_phase(summary="Wrote report.txt")
        progress.log("Extraction finished")
        return 0
    finally:
        progress.close()
