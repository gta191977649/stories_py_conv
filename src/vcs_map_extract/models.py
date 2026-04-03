from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class IdeModel:
    model_id: int
    model_name: str
    txd_name: str
    section: str
    source_file: str


@dataclass(slots=True)
class ArchiveEntry:
    archive_name: str
    entry_name: str
    data: bytes


@dataclass(slots=True)
class ConversionResult:
    archive_name: str
    stem: str
    dff_path: Path | None = None
    txd_path: Path | None = None
    col_path: Path | None = None
    texture_names: set[str] = field(default_factory=set)
    unresolved_name: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReportData:
    summary_by_archive: dict[str, dict[str, int]] = field(default_factory=dict)
    unresolved_streamed_names: list[str] = field(default_factory=list)
    duplicate_pack_conflicts: list[str] = field(default_factory=list)
    knackers_texture_conflicts: list[str] = field(default_factory=list)
    streamed_texture_conflicts: list[str] = field(default_factory=list)
    streamed_diagnostics: list[str] = field(default_factory=list)
    interior_diagnostics: list[str] = field(default_factory=list)
    ipl_diagnostics: list[str] = field(default_factory=list)
    missing_models: list[str] = field(default_factory=list)
    missing_models_by_source_file: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class StreamedPlacement:
    ipl_id: int
    linked_ipl_id: int | None
    world_id: int
    res_id: int
    sector_id: int
    pass_index: int
    source_kind: str
    visible: bool
    matrix: tuple[float, ...]
    placement_count: int = 1


@dataclass(slots=True)
class StreamedModelPlan:
    model_name: str
    output_name: str
    txd_name: str
    source_file: str
    placements: list[StreamedPlacement] = field(default_factory=list)
    alternate_placement_sets: list[list[StreamedPlacement]] = field(default_factory=list)
    unresolved_name: bool = False
    has_hidden_alternates: bool = False
    export_kind: str = "world_named"


@dataclass(slots=True)
class StreamedArchivePlan:
    archive_name: str
    model_exports: list[StreamedModelPlan] = field(default_factory=list)
    txd_exports: dict[str, list[int]] = field(default_factory=dict)
    summary: dict[str, int] = field(default_factory=dict)
    unresolved_names: list[str] = field(default_factory=list)
    preloaded_level: object | None = None
