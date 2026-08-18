# SPDX-License-Identifier: MIT

"""End-to-end normalization pipeline helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Callable

from .bin2log import Bin2LogConfig, decode_workspace_logs, prepare_decode_workspace
from .discovery import iter_bin_files, iter_log_files, iter_mer_files
from .format_record_filenames import validate_instrument_serial
from .manifest import (
    begin_instrument_run,
    build_outputs_manifest,
    build_source_state,
    finalize_instrument_run,
    latest_outputs_manifest,
    latest_source_state,
    output_dir_contains_manifests,
    record_pruned_sources,
    write_normalization_manifest,
)
from .normalize_log import output_filenames as log_output_filenames
from .normalize_log import LogJsonlSummary
from .normalize_log import write_log_jsonl_families
from .normalize_mer import output_filenames as mer_output_filenames
from .normalize_mer import write_mer_jsonl_families
from .parse_instrument_name import (
    InstrumentName,
    instrument_name_from_vit_path,
    maybe_parse_instrument_name,
    parse_instrument_name,
)

type ExecutionMode = str
type ProgressCallback = Callable[[str], None]


@dataclass(slots=True)
class InstrumentRunSummary:
    """Per-instrument execution summary for one normalization run."""

    instrument_id: str
    instrument_serial: str
    mode: str
    output_dir: str
    bin_count: int
    log_count: int
    mer_count: int
    log_action: str
    mer_action: str
    decoder_state_invalidated: bool


@dataclass(slots=True)
class PlannedInstrumentRun:
    """Shared per-instrument plan used for real runs and dry runs."""

    summary: InstrumentRunSummary
    current_sources: list[Path]
    instrument_output_dir: Path
    log_diff: dict[str, list[dict[str, object]]]
    mer_diff: dict[str, list[dict[str, object]]]
    input_file_diffs: list[dict[str, object]]


@dataclass(slots=True)
class NormalizationPipelineSummary:
    """Aggregate summary for one normalization pipeline run."""

    mode: str
    input_root: str | None
    input_files: list[str]
    output_dir: str
    processed_instruments: list[InstrumentRunSummary]
    metrics: RunMetrics

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable summary payload."""

        return asdict(self)


@dataclass(slots=True)
class DryRunSummary:
    """Aggregate dry-run planning summary."""

    mode: str
    input_root: str | None
    input_files: list[str]
    output_dir: str
    instruments: list[dict[str, object]]
    metrics: RunMetrics

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable summary payload."""

        return asdict(self)


@dataclass(slots=True)
class RunMetrics:
    """Aggregate counts for CLI end-of-run summaries."""

    raw_files_processed: int = 0
    raw_files_new: int = 0
    raw_files_changed: int = 0
    raw_files_removed: int = 0
    instruments_append: int = 0
    instruments_rewrite: int = 0
    instruments_noop: int = 0
    log_instruments_append: int = 0
    log_instruments_rewrite: int = 0
    log_instruments_noop: int = 0
    mer_instruments_append: int = 0
    mer_instruments_rewrite: int = 0
    mer_instruments_noop: int = 0
    log_records_written: int = 0
    log_records_removed: int = 0
    mer_records_written: int = 0
    mer_records_removed: int = 0
    malformed_log_lines: int = 0
    skipped_log_files: int = 0
    malformed_mer_blocks: int = 0
    skipped_mer_files: int = 0
    bin_files_decoded: int = 0
    preflight_mode: str | None = None
    log_family_source_line_counts: dict[str, int] = field(default_factory=dict)
    log_ordinary_lines_examined: int = 0
    log_source_line_assignments: int = 0
    log_duplicate_assignments: int = 0
    log_missing_assignments: int = 0


def instrument_has_bin_inputs(
    input_root: Path,
    *,
    instrument_serial: str,
) -> bool:
    """Return whether one selected instrument has authoritative BIN inputs."""

    target = parse_instrument_name(instrument_serial)
    serial_map = _instrument_names_from_vit(input_root)
    selected = _select_target_instrument_sources(
        list(iter_bin_files(input_root)),
        target=target,
        input_root=input_root,
        serial_map=serial_map,
    )
    return bool(selected)


def run_normalization_pipeline(
    input_root: Path | None = None,
    *,
    output_dir: Path,
    config: Bin2LogConfig | None = None,
    input_files: list[Path] | None = None,
    instrument_serial: str | None = None,
    dry_run: bool = False,
    force_rewrite: bool = False,
    progress: ProgressCallback | None = None,
    generation_command: str | None = None,
) -> NormalizationPipelineSummary | DryRunSummary:
    """Run the normalization pipeline in stateful or stateless mode."""

    _emit_progress(progress, "Starting normalization")
    mode = _detect_mode(input_root=input_root, input_files=input_files)
    if instrument_serial is not None and mode != "stateful":
        raise ValueError("instrument_serial requires stateful input_root mode")
    target_instrument = (
        parse_instrument_name(instrument_serial)
        if instrument_serial is not None
        else None
    )
    if mode == "stateful":
        assert input_root is not None
        return _run_stateful(
            input_root=input_root,
            output_dir=output_dir,
            config=config,
            target_instrument=target_instrument,
            dry_run=dry_run,
            force_rewrite=force_rewrite,
            progress=progress,
            generation_command=generation_command,
        )
    assert input_files is not None
    return _run_stateless(
        input_files=input_files,
        output_dir=output_dir,
        config=config,
        dry_run=dry_run,
        force_rewrite=force_rewrite,
        progress=progress,
        generation_command=generation_command,
    )


def _run_stateful(
    *,
    input_root: Path,
    output_dir: Path,
    config: Bin2LogConfig | None,
    target_instrument: InstrumentName | None,
    dry_run: bool,
    force_rewrite: bool,
    progress: ProgressCallback | None,
    generation_command: str | None,
) -> NormalizationPipelineSummary | DryRunSummary:
    _emit_progress(progress, f"Discovering inputs under {input_root}")
    serial_map = _instrument_names_from_vit(input_root)
    discovered_paths = [
        *sorted(iter_bin_files(input_root)),
        *sorted(iter_log_files(input_root)),
        *sorted(iter_mer_files(input_root)),
    ]
    if target_instrument is not None:
        _emit_progress(
            progress,
            f"Selecting instrument serial {target_instrument.serial}",
        )
        discovered_paths = _select_target_instrument_sources(
            discovered_paths,
            target=target_instrument,
            input_root=input_root,
            serial_map=serial_map,
        )
        serial_map[target_instrument.raw_file_prefix] = target_instrument
    discovered_sources = _group_paths(discovered_paths)
    grouped_sources = {
        group_key: _select_authoritative_sources(paths)
        for group_key, paths in discovered_sources.items()
    }
    if not grouped_sources and target_instrument is None:
        _emit_progress(
            progress,
            f"WARNING: no expected source files found under {input_root} (expected .BIN, .LOG, or .MER)",
        )
    previous_outputs = _previous_outputs_by_instrument_id(output_dir) if output_dir.exists() else {}
    if target_instrument is not None:
        canonical_output = output_dir / target_instrument.serial
        previous_output = (
            canonical_output
            if latest_source_state(canonical_output) is not None
            else previous_outputs.get(target_instrument.raw_file_prefix)
        )
        if previous_output is not None and previous_output.name != target_instrument.serial:
            previous_name = maybe_parse_instrument_name(previous_output.name)
            if previous_name is not None and previous_name.serial != target_instrument.serial:
                previous_output = None
        previous_outputs = (
            {target_instrument.raw_file_prefix: previous_output}
            if previous_output is not None
            else {}
        )
        if not grouped_sources and not previous_outputs:
            raise ValueError(
                f"Instrument serial not found under input root: {target_instrument.serial}"
            )
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    all_group_keys = sorted(set(grouped_sources) | set(previous_outputs))
    normalization_version = _normalization_version()
    planned_instruments: list[PlannedInstrumentRun] = []
    metrics = RunMetrics(preflight_mode=config.preflight_mode if config is not None else None)

    for group_key in all_group_keys:
        current_sources = grouped_sources.get(group_key, [])
        if any(path.suffix.upper() == ".BIN" for path in current_sources) and config is None:
            raise ValueError("decoder config is required when BIN inputs are present")

        previous_output_dir = previous_outputs.get(group_key)
        instrument_name = _resolve_instrument_name(
            group_key=group_key,
            paths=current_sources,
            input_root=input_root,
            previous_output_dir=previous_output_dir,
            serial_map=serial_map,
        )
        canonical_instrument_id = _canonical_instrument_id(group_key=group_key, instrument_name=instrument_name)
        instrument_output_name = _instrument_output_name(
            group_key=group_key,
            previous_output_dir=previous_output_dir,
            instrument_name=instrument_name,
        )
        instrument_serial = _derive_instrument_serial(
            instrument_name=instrument_name,
            fallback=instrument_output_name,
        )
        instrument_output_dir = output_dir / instrument_output_name
        if not dry_run:
            _migrate_output_dir(previous_output_dir, instrument_output_dir)
        previous_state = latest_source_state(instrument_output_dir)
        current_state = build_source_state(
            raw_source_paths=current_sources,
            config=config if _has_kind(current_sources, "bin") else None,
            input_root=input_root,
            normalization_version=normalization_version,
            instrument_serial=instrument_serial,
        )
        decoder_state_invalidated = _decoder_state_invalidated(previous_state, current_state)
        input_file_diffs = _diff_sources(
            previous_state,
            current_state,
            {"bin", "log", "mer"},
            instrument_id=canonical_instrument_id,
            instrument_serial=instrument_serial,
            run_id=None,
            decoder_state_changed=decoder_state_invalidated,
        )
        log_diff = _select_diff_rows(input_file_diffs, {"bin", "log"})
        mer_diff = _select_diff_rows(input_file_diffs, {"mer"})
        summary = InstrumentRunSummary(
            instrument_id=canonical_instrument_id,
            instrument_serial=instrument_serial,
            mode="stateful",
            output_dir=instrument_output_dir.as_posix(),
            bin_count=_count_kind(current_sources, "bin"),
            log_count=_count_kind(current_sources, "log"),
            mer_count=_count_kind(current_sources, "mer"),
            log_action=_determine_action(
                diff=log_diff,
                invalidate=_general_invalidation(previous_state, current_state) or (
                    decoder_state_invalidated and _bin_dependent(previous_state, current_state)
                ),
                force_rewrite=force_rewrite,
            ),
            mer_action=_determine_action(
                diff=mer_diff,
                invalidate=_general_invalidation(previous_state, current_state),
                force_rewrite=force_rewrite,
            ),
            decoder_state_invalidated=decoder_state_invalidated,
        )
        planned_instruments.append(
            PlannedInstrumentRun(
                summary=summary,
                current_sources=current_sources,
                instrument_output_dir=instrument_output_dir,
                log_diff=log_diff,
                mer_diff=mer_diff,
                input_file_diffs=input_file_diffs,
            )
        )
        _accumulate_diff_metrics(metrics, input_file_diffs)
        _accumulate_action_metrics(metrics, summary)

    if dry_run:
        metrics.bin_files_decoded = sum(
            len(_rewrite_paths(plan.summary.log_action, plan.current_sources, plan.log_diff, "bin"))
            for plan in planned_instruments
        )
        _emit_progress(progress, "Dry-run planning complete")
        return DryRunSummary(
            mode="stateful",
            input_root=input_root.as_posix(),
            input_files=[],
            output_dir=output_dir.as_posix(),
            instruments=[_dry_run_instrument_payload(plan) for plan in planned_instruments],
            metrics=metrics,
        )

    processed_instruments: list[InstrumentRunSummary] = []
    for plan in planned_instruments:
        current_sources = plan.current_sources
        summary = plan.summary
        _emit_progress(progress, f"Processing instrument {summary.instrument_id}")
        previous_outputs = latest_outputs_manifest(plan.instrument_output_dir)
        if _overall_instrument_action(summary) == "rewrite":
            _remove_package_owned_outputs(plan.instrument_output_dir)
        _ensure_canonical_instrument_layout(
            instrument_output_dir=plan.instrument_output_dir,
            include_state_files=True,
            instrument_serial=summary.instrument_serial,
        )
        _reset_preflight_status(plan.instrument_output_dir)

        record_pruned_sources(
            instrument_output_dir=plan.instrument_output_dir,
            instrument_id=summary.instrument_id,
            instrument_serial=summary.instrument_serial,
            removed_sources=plan.log_diff["removed"] + plan.mer_diff["removed"],
        )

        run_context = begin_instrument_run(
            instrument_output_dir=plan.instrument_output_dir,
            input_root=input_root,
            raw_source_paths=current_sources,
            config=config if _has_kind(current_sources, "bin") else None,
            normalization_version=normalization_version,
            instrument_serial=summary.instrument_serial,
        )
        run_id = str(run_context["run_id"])
        input_file_diffs = [{**row, "run_id": run_id} for row in plan.input_file_diffs]
        malformed_log_lines: list[dict[str, object]] = []
        skipped_log_files: list[dict[str, object]] = []
        malformed_mer_blocks: list[dict[str, object]] = []
        skipped_mer_files: list[dict[str, object]] = []
        log_paths = _rewrite_paths(summary.log_action, current_sources, plan.log_diff, "log")
        bin_paths = _rewrite_paths(summary.log_action, current_sources, plan.log_diff, "bin")
        mer_paths = _rewrite_paths(summary.mer_action, current_sources, plan.mer_diff, "mer")

        error: BaseException | None = None
        try:
            log_summary = _execute_log_family(
                instrument_output_dir=plan.instrument_output_dir,
                action=summary.log_action,
                log_paths=log_paths,
                bin_paths=bin_paths,
                config=config,
                instrument_id=summary.instrument_id,
                instrument_serial=summary.instrument_serial,
                progress=progress,
                run_id=run_id,
                malformed_log_lines=malformed_log_lines,
                skipped_log_files=skipped_log_files,
            )
            _execute_mer_family(
                instrument_output_dir=plan.instrument_output_dir,
                action=summary.mer_action,
                mer_paths=mer_paths,
                instrument_id=summary.instrument_id,
                instrument_serial=summary.instrument_serial,
                progress=progress,
                run_id=run_id,
                malformed_mer_blocks=malformed_mer_blocks,
                skipped_mer_files=skipped_mer_files,
            )
        except BaseException as exc:
            error = exc
            raise
        finally:
            _ensure_canonical_instrument_layout(
                instrument_output_dir=plan.instrument_output_dir,
                include_state_files=True,
                instrument_serial=summary.instrument_serial,
            )
            _emit_progress(progress, f"Writing manifests for instrument {summary.instrument_id}")
            finalize_instrument_run(
                context=run_context,
                preflight_mode=config.preflight_mode if config is not None and _has_kind(current_sources, "bin") else None,
                error=error,
                input_file_diffs=_public_diff_rows(input_file_diffs),
                malformed_log_lines=malformed_log_lines,
                skipped_log_files=skipped_log_files,
                malformed_mer_blocks=malformed_mer_blocks,
                skipped_mer_files=skipped_mer_files,
            )

        _accumulate_issue_metrics(
            metrics,
            malformed_log_lines=malformed_log_lines,
            skipped_log_files=skipped_log_files,
            malformed_mer_blocks=malformed_mer_blocks,
            skipped_mer_files=skipped_mer_files,
        )
        metrics.bin_files_decoded += len(bin_paths) if summary.log_action != "noop" else 0
        _accumulate_output_metrics(
            metrics,
            previous_outputs=previous_outputs,
            current_outputs=build_outputs_manifest(plan.instrument_output_dir),
            log_action=summary.log_action,
            mer_action=summary.mer_action,
            log_summary=log_summary,
        )
        processed_instruments.append(summary)

    write_normalization_manifest(
        output_root=output_dir,
        input_root=input_root,
        generation_command=generation_command,
    )
    return NormalizationPipelineSummary(
        mode="stateful",
        input_root=input_root.as_posix(),
        input_files=[],
        output_dir=output_dir.as_posix(),
        processed_instruments=processed_instruments,
        metrics=metrics,
    )


def _run_stateless(
    *,
    input_files: list[Path],
    output_dir: Path,
    config: Bin2LogConfig | None,
    dry_run: bool,
    force_rewrite: bool,
    progress: ProgressCallback | None,
    generation_command: str | None,
) -> NormalizationPipelineSummary | DryRunSummary:
    if output_dir_contains_manifests(output_dir):
        raise ValueError(
            "stateless mode cannot write into an output directory that already contains manifests"
        )

    grouped_sources = {
        group_key: _select_authoritative_sources(paths)
        for group_key, paths in _group_paths(input_files).items()
    }
    _emit_progress(progress, f"Discovering explicit input files ({len(input_files)})")
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    normalization_version = _normalization_version()
    processed_instruments: list[InstrumentRunSummary] = []
    dry_run_instruments: list[dict[str, object]] = []
    metrics = RunMetrics(preflight_mode=config.preflight_mode if config is not None else None)

    for group_key, current_sources in sorted(grouped_sources.items()):
        if any(path.suffix.upper() == ".BIN" for path in current_sources) and config is None:
            raise ValueError("decoder config is required when BIN inputs are present")
        instrument_name = _resolve_instrument_name(
            group_key=group_key,
            paths=current_sources,
            input_root=None,
            previous_output_dir=None,
            serial_map={},
        )
        canonical_instrument_id = _canonical_instrument_id(group_key=group_key, instrument_name=instrument_name)
        instrument_output_name = _instrument_output_name(
            group_key=group_key,
            previous_output_dir=None,
            instrument_name=instrument_name,
        )
        instrument_serial = _derive_instrument_serial(
            instrument_name=instrument_name,
            fallback=instrument_output_name,
        )
        instrument_output_dir = output_dir / instrument_output_name
        input_file_diffs = _diff_sources(
            None,
            build_source_state(
                raw_source_paths=current_sources,
                config=config if _has_kind(current_sources, "bin") else None,
                input_root=Path("."),
                normalization_version=normalization_version,
                instrument_serial=instrument_serial,
            ),
            {"bin", "log", "mer"},
            instrument_id=canonical_instrument_id,
            instrument_serial=instrument_serial,
            run_id=None,
            decoder_state_changed=False,
        )
        summary = InstrumentRunSummary(
            instrument_id=canonical_instrument_id,
            instrument_serial=instrument_serial,
            mode="stateless",
            output_dir=instrument_output_dir.as_posix(),
            bin_count=_count_kind(current_sources, "bin"),
            log_count=_count_kind(current_sources, "log"),
            mer_count=_count_kind(current_sources, "mer"),
            log_action=(
                "rewrite"
                if _has_kind(current_sources, "bin") or _has_kind(current_sources, "log")
                else "noop"
            ),
            mer_action=(
                "rewrite"
                if _has_kind(current_sources, "mer")
                else "noop"
            ),
            decoder_state_invalidated=False,
        )
        _accumulate_diff_metrics(metrics, input_file_diffs)
        _accumulate_action_metrics(metrics, summary)
        if dry_run:
            metrics.bin_files_decoded += _count_kind(current_sources, "bin")
            dry_run_instruments.append(
                _dry_run_instrument_payload(
                    PlannedInstrumentRun(
                        summary=summary,
                        current_sources=current_sources,
                        instrument_output_dir=instrument_output_dir,
                        log_diff=_select_diff_rows(input_file_diffs, {"bin", "log"}),
                        mer_diff=_select_diff_rows(input_file_diffs, {"mer"}),
                        input_file_diffs=input_file_diffs,
                    )
                )
            )
            continue

        _emit_progress(progress, f"Processing instrument {summary.instrument_id}")
        if _overall_instrument_action(summary) == "rewrite":
            _remove_package_owned_outputs(instrument_output_dir)
        _ensure_canonical_instrument_layout(
            instrument_output_dir=instrument_output_dir,
            include_state_files=False,
            instrument_serial=summary.instrument_serial,
        )
        _reset_preflight_status(instrument_output_dir)
        malformed_log_lines: list[dict[str, object]] = []
        skipped_log_files: list[dict[str, object]] = []
        malformed_mer_blocks: list[dict[str, object]] = []
        skipped_mer_files: list[dict[str, object]] = []
        log_paths = _selected_paths(current_sources, {"log"})
        bin_paths = _selected_paths(current_sources, {"bin"})
        mer_paths = _selected_paths(current_sources, {"mer"})
        log_summary = _execute_log_family(
            instrument_output_dir=instrument_output_dir,
            action=summary.log_action,
            log_paths=log_paths,
            bin_paths=bin_paths,
            config=config,
            instrument_id=summary.instrument_id,
            instrument_serial=summary.instrument_serial,
            progress=progress,
            run_id=None,
            malformed_log_lines=None,
            skipped_log_files=None,
            fail_on_malformed=True,
        )
        _execute_mer_family(
            instrument_output_dir=instrument_output_dir,
            action=summary.mer_action,
            mer_paths=mer_paths,
            instrument_id=summary.instrument_id,
            instrument_serial=summary.instrument_serial,
            progress=progress,
            run_id=None,
            malformed_mer_blocks=None,
            skipped_mer_files=None,
            fail_on_malformed=True,
        )
        _ensure_canonical_instrument_layout(
            instrument_output_dir=instrument_output_dir,
            include_state_files=False,
            instrument_serial=summary.instrument_serial,
        )
        _accumulate_issue_metrics(
            metrics,
            malformed_log_lines=malformed_log_lines,
            skipped_log_files=skipped_log_files,
            malformed_mer_blocks=malformed_mer_blocks,
            skipped_mer_files=skipped_mer_files,
        )
        metrics.bin_files_decoded += len(bin_paths)
        _accumulate_output_metrics(
            metrics,
            previous_outputs=None,
            current_outputs=build_outputs_manifest(instrument_output_dir),
            log_action=summary.log_action,
            mer_action=summary.mer_action,
            log_summary=log_summary,
        )
        processed_instruments.append(summary)

    if dry_run:
        _emit_progress(progress, "Dry-run planning complete")
        return DryRunSummary(
            mode="stateless",
            input_root=None,
            input_files=[path.as_posix() for path in sorted(input_files)],
            output_dir=output_dir.as_posix(),
            instruments=dry_run_instruments,
            metrics=metrics,
        )

    write_normalization_manifest(
        output_root=output_dir,
        input_root=None,
        generation_command=generation_command,
    )
    return NormalizationPipelineSummary(
        mode="stateless",
        input_root=None,
        input_files=[path.as_posix() for path in sorted(input_files)],
        output_dir=output_dir.as_posix(),
        processed_instruments=processed_instruments,
        metrics=metrics,
    )


def _execute_log_family(
    *,
    instrument_output_dir: Path,
    action: str,
    log_paths: list[Path],
    bin_paths: list[Path],
    config: Bin2LogConfig | None,
    instrument_id: str,
    instrument_serial: str,
    progress: ProgressCallback | None,
    run_id: str | None,
    malformed_log_lines: list[dict[str, object]] | None,
    skipped_log_files: list[dict[str, object]] | None,
    fail_on_malformed: bool = False,
) -> LogJsonlSummary | None:
    output_filenames = log_output_filenames(instrument_serial)
    destinations = [instrument_output_dir / filename for filename in output_filenames.values()]
    if action == "noop":
        return None
    if action == "rewrite" and not log_paths and not bin_paths:
        _remove_paths(destinations)
        return None
    if not log_paths and not bin_paths:
        return None
    if bin_paths and config is None:
        raise ValueError("decoder config is required when BIN inputs are present")

    _emit_progress(progress, f"Normalizing LOG for instrument {instrument_id}")
    instrument_output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mermaid-log-family-") as tmpdir:
        temp_dir = Path(tmpdir)
        rendered_paths = list(log_paths)
        authoritative_source_files = {path: path for path in log_paths}
        if bin_paths:
            assert config is not None
            _emit_progress(progress, f"Running BIN decode for instrument {instrument_id}")
            decode_config = Bin2LogConfig(
                python_executable=config.python_executable,
                decoder_script=config.decoder_script,
                preflight_mode=config.preflight_mode,
                preflight_status_dir=instrument_output_dir,
                environment_fingerprint=config.environment_fingerprint,
            )
            workdir = temp_dir / "decoded"
            workdir.mkdir(parents=True, exist_ok=True)
            try:
                for path in bin_paths:
                    shutil.copy2(path, workdir / path.name)
                prepare_decode_workspace(workdir, config=decode_config, refresh_database=True)
                decoded_log_paths = decode_workspace_logs(workdir, config=decode_config)
                rendered_paths.extend(decoded_log_paths)
                authoritative_source_files.update(
                    _map_decoded_logs_to_bin_sources(
                        decoded_log_paths,
                        bin_paths=bin_paths,
                    )
                )
            except Exception as exc:
                paths_text = ", ".join(path.as_posix() for path in bin_paths)
                raise ValueError(
                    f"Error while decoding BIN source(s) {paths_text}: {exc}"
                ) from exc
        log_summary = write_log_jsonl_families(
            rendered_paths,
            temp_dir,
            instrument_id=instrument_id,
            instrument_serial=instrument_serial,
            authoritative_source_files=authoritative_source_files,
            run_id=run_id,
            malformed_log_lines=malformed_log_lines,
            skipped_log_files=skipped_log_files,
            fail_on_malformed=fail_on_malformed,
        )
        if action == "append":
            _append_rendered_outputs(
                temp_dir=temp_dir,
                destination_dir=instrument_output_dir,
                filenames=list(output_filenames.values()),
            )
        else:
            _replace_rendered_outputs(
                temp_dir=temp_dir,
                destination_dir=instrument_output_dir,
                filenames=list(output_filenames.values()),
            )
        return log_summary


def _execute_mer_family(
    *,
    instrument_output_dir: Path,
    action: str,
    mer_paths: list[Path],
    instrument_id: str,
    instrument_serial: str,
    progress: ProgressCallback | None,
    run_id: str | None,
    malformed_mer_blocks: list[dict[str, object]] | None,
    skipped_mer_files: list[dict[str, object]] | None,
    fail_on_malformed: bool = False,
) -> None:
    output_filenames = mer_output_filenames(instrument_serial)
    destinations = [instrument_output_dir / filename for filename in output_filenames.values()]
    if action == "noop":
        return
    if action == "rewrite" and not mer_paths:
        _remove_paths(destinations)
        return
    if not mer_paths:
        return

    _emit_progress(progress, f"Normalizing MER for instrument {instrument_id}")
    instrument_output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mermaid-mer-family-") as tmpdir:
        temp_dir = Path(tmpdir)
        write_mer_jsonl_families(
            mer_paths,
            temp_dir,
            instrument_id=instrument_id,
            instrument_serial=instrument_serial,
            run_id=run_id,
            malformed_mer_blocks=malformed_mer_blocks,
            skipped_mer_files=skipped_mer_files,
            fail_on_malformed=fail_on_malformed,
        )
        if action == "append":
            _append_rendered_outputs(
                temp_dir=temp_dir,
                destination_dir=instrument_output_dir,
                filenames=list(output_filenames.values()),
            )
        else:
            _replace_rendered_outputs(
                temp_dir=temp_dir,
                destination_dir=instrument_output_dir,
                filenames=list(output_filenames.values()),
            )


def _append_rendered_outputs(
    *,
    temp_dir: Path,
    destination_dir: Path,
    filenames: list[str],
) -> None:
    for filename in filenames:
        source = temp_dir / filename
        if not source.exists() or source.stat().st_size == 0:
            continue
        destination = destination_dir / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as dst_handle:
            dst_handle.write(source.read_text(encoding="utf-8"))


def _replace_rendered_outputs(
    *,
    temp_dir: Path,
    destination_dir: Path,
    filenames: list[str],
) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        source = temp_dir / filename
        destination = destination_dir / filename
        if not source.exists():
            if destination.exists():
                destination.unlink()
            continue
        shutil.copyfile(source, destination)


def _remove_paths(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def _reset_preflight_status(instrument_output_dir: Path) -> None:
    preflight_status_path = instrument_output_dir / "preflight_status.json"
    if preflight_status_path.exists():
        preflight_status_path.unlink()


def _remove_package_owned_outputs(instrument_output_dir: Path) -> None:
    if not instrument_output_dir.exists():
        return

    for path in sorted(instrument_output_dir.glob("log_*.jsonl")):
        if path.is_file():
            path.unlink()
    for path in sorted(instrument_output_dir.glob("mer_*.jsonl")):
        if path.is_file():
            path.unlink()

    for dirname in ("manifests", "state"):
        path = instrument_output_dir / dirname
        if path.is_dir():
            shutil.rmtree(path)


def _ensure_canonical_instrument_layout(
    *,
    instrument_output_dir: Path,
    include_state_files: bool,
    instrument_serial: str,
) -> None:
    instrument_output_dir.mkdir(parents=True, exist_ok=True)
    _ensure_files(
        instrument_output_dir / filename
        for filename in _canonical_instrument_jsonl_filenames(instrument_serial)
    )
    if include_state_files:
        _ensure_files([instrument_output_dir / "state" / "pruned_records.jsonl"])


def _ensure_files(paths) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)


def _detect_mode(
    *,
    input_root: Path | None,
    input_files: list[Path] | None,
) -> ExecutionMode:
    has_root = input_root is not None
    has_files = bool(input_files)
    if has_root == has_files:
        raise ValueError("provide exactly one of input_root or input_files")
    return "stateful" if has_root else "stateless"


def _group_paths(paths: list[Path]) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for path in sorted(paths):
        grouped.setdefault(_raw_file_prefix(path), []).append(path)
    return grouped


def _select_target_instrument_sources(
    paths: list[Path],
    *,
    target: InstrumentName,
    input_root: Path,
    serial_map: dict[str, InstrumentName],
) -> list[Path]:
    """Select raw sources belonging to one explicitly requested serial."""

    selected: list[Path] = []
    mapped_name = serial_map.get(target.raw_file_prefix)
    for path in paths:
        if _raw_file_prefix(path) != target.raw_file_prefix:
            continue

        contextual_name = _instrument_name_from_path_context(path, input_root=input_root)
        if contextual_name is not None:
            if contextual_name.serial == target.serial:
                selected.append(path)
            continue

        if path.suffix.upper() == ".LOG":
            log_serial = _serial_from_log(path)
            if log_serial is not None:
                if log_serial == target.serial:
                    selected.append(path)
                continue

        if mapped_name is None or mapped_name.serial == target.serial:
            selected.append(path)
    return selected


def _instrument_name_from_path_context(
    path: Path,
    *,
    input_root: Path,
) -> InstrumentName | None:
    """Return the nearest full instrument serial encoded in a source path."""

    for ancestor in path.parents:
        if ancestor == input_root.parent:
            break
        parsed = maybe_parse_instrument_name(ancestor.name)
        if parsed is not None:
            return parsed
    return None


def _select_authoritative_sources(paths: list[Path]) -> list[Path]:
    """Discard native LOG inputs shadowed by same-stem BIN inputs."""

    bin_stems = {
        path.stem.casefold()
        for path in paths
        if path.suffix.upper() == ".BIN"
    }
    return [
        path
        for path in paths
        if not (
            path.suffix.upper() == ".LOG"
            and path.stem.casefold() in bin_stems
        )
    ]


def _map_decoded_logs_to_bin_sources(
    decoded_log_paths: list[Path],
    *,
    bin_paths: list[Path],
) -> dict[Path, Path]:
    """Map readable decoder LOG artifacts to their authoritative BIN inputs."""

    bins_by_stem: dict[str, list[Path]] = {}
    for bin_path in bin_paths:
        bins_by_stem.setdefault(bin_path.stem.casefold(), []).append(bin_path)

    source_files: dict[Path, Path] = {}
    for log_path in decoded_log_paths:
        candidates = bins_by_stem.get(log_path.stem.casefold(), [])
        if len(candidates) == 1:
            source_files[log_path] = candidates[0]
            continue
        if not candidates and len(bin_paths) == 1:
            source_files[log_path] = bin_paths[0]
            continue
        candidate_names = ", ".join(path.name for path in candidates) or "none"
        raise ValueError(
            "Cannot resolve authoritative BIN source for decoded LOG "
            f"{log_path.name}; matching BIN inputs: {candidate_names}"
        )
    return source_files


def _raw_file_prefix(path: Path) -> str:
    return path.stem.split("_", maxsplit=1)[0]


def _instrument_output_name(
    *,
    group_key: str,
    previous_output_dir: Path | None,
    instrument_name: InstrumentName | None,
) -> str:
    if instrument_name is not None:
        return instrument_name.serial
    if previous_output_dir is not None and _looks_like_full_serial(previous_output_dir.name):
        return previous_output_dir.name
    if previous_output_dir is not None:
        return previous_output_dir.name
    return group_key


def _derive_instrument_serial(
    *,
    instrument_name: InstrumentName | None,
    fallback: str,
) -> str:
    if instrument_name is not None:
        return validate_instrument_serial(instrument_name.serial)
    return validate_instrument_serial(fallback)


def _canonical_instrument_jsonl_filenames(instrument_serial: str) -> tuple[str, ...]:
    return tuple(
        [
            *log_output_filenames(instrument_serial).values(),
            *mer_output_filenames(instrument_serial).values(),
        ]
    )


def _instrument_names_from_vit(input_root: Path) -> dict[str, InstrumentName]:
    serial_map: dict[str, InstrumentName] = {}
    for path in sorted(input_root.glob("*.vit")) + sorted(input_root.glob("*.VIT")):
        instrument_name = instrument_name_from_vit_path(path)
        if instrument_name is None:
            continue
        serial_map[instrument_name.raw_file_prefix] = instrument_name
    return serial_map


def _resolve_instrument_name(
    *,
    group_key: str,
    paths: list[Path],
    input_root: Path | None,
    previous_output_dir: Path | None,
    serial_map: dict[str, InstrumentName],
) -> InstrumentName | None:
    if group_key in serial_map:
        return serial_map[group_key]
    if input_root is not None:
        candidate = maybe_parse_instrument_name(input_root.name)
        if candidate is not None and candidate.raw_file_prefix == group_key:
            return candidate
    if previous_output_dir is not None:
        candidate = maybe_parse_instrument_name(previous_output_dir.name)
        if candidate is not None:
            return candidate
    for path in paths:
        for ancestor in path.parents:
            if input_root is not None and ancestor == input_root.parent:
                break
            candidate = maybe_parse_instrument_name(ancestor.name)
            if candidate is not None:
                return candidate
    for path in paths:
        serial = _serial_from_log(path)
        if serial is not None:
            candidate = maybe_parse_instrument_name(serial)
            if candidate is not None:
                return candidate
    return None


def _looks_like_full_serial(name: str) -> bool:
    return maybe_parse_instrument_name(name) is not None


def _canonical_instrument_id(*, group_key: str, instrument_name: InstrumentName | None) -> str:
    if instrument_name is not None:
        return instrument_name.instrument_id
    return group_key


def _serial_from_log(path: Path) -> str | None:
    if path.suffix.upper() != ".LOG" or not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(32):
                line = handle.readline()
                if not line:
                    break
                if "]buoy " in line:
                    candidate = line.split("]buoy ", maxsplit=1)[1].strip()
                    if _looks_like_full_serial(candidate):
                        return candidate
                if "]board " in line:
                    candidate = line.split("]board ", maxsplit=1)[1].strip()
                    if _looks_like_full_serial(candidate):
                        return candidate
    except OSError:
        return None
    return None


def _previous_outputs_by_instrument_id(output_dir: Path) -> dict[str, Path]:
    previous: dict[str, Path] = {}
    for latest_path in output_dir.glob("*/manifests/latest.json"):
        instrument_output_dir = latest_path.parent.parent
        state = latest_source_state(instrument_output_dir)
        if state is None:
            continue
        raw_sources = state.get("raw_sources", [])
        if not raw_sources:
            continue
        previous[_raw_file_prefix(Path(raw_sources[0]["source_file"]))] = instrument_output_dir
    return previous


def _migrate_output_dir(previous_output_dir: Path | None, instrument_output_dir: Path) -> None:
    if previous_output_dir is None or previous_output_dir == instrument_output_dir:
        return
    if not previous_output_dir.exists() or instrument_output_dir.exists():
        return
    instrument_output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(previous_output_dir.as_posix(), instrument_output_dir.as_posix())


def _selected_paths(paths: list[Path], kinds: set[str]) -> list[Path]:
    return [path for path in paths if _kind_for_path(path) in kinds]


def _kind_for_path(path: Path) -> str:
    suffix = path.suffix.upper()
    if suffix == ".BIN":
        return "bin"
    if suffix == ".LOG":
        return "log"
    if suffix == ".MER":
        return "mer"
    raise ValueError(f"Unsupported input file type for the current normalization contract: {path}")


def _has_kind(paths: list[Path], kind: str) -> bool:
    return any(_kind_for_path(path) == kind for path in paths)


def _count_kind(paths: list[Path], kind: str) -> int:
    return sum(1 for path in paths if _kind_for_path(path) == kind)


def _paths_from_sources(sources: list[dict[str, object]]) -> list[Path]:
    return [Path(str(source.get("_source_path", source["source_file"]))) for source in sources]


def _diff_sources(
    previous_state: dict[str, object] | None,
    current_state: dict[str, object],
    kinds: set[str],
    *,
    instrument_id: str,
    instrument_serial: str,
    run_id: str | None,
    decoder_state_changed: bool,
) -> list[dict[str, object]]:
    previous_sources = {
        item["source_file"]: item
        for item in (previous_state or {}).get("raw_sources", [])
        if item["source_kind"] in kinds
    }
    current_sources = {
        item["source_file"]: item
        for item in current_state["raw_sources"]
        if item["source_kind"] in kinds
    }
    rows: list[dict[str, object]] = []
    for source_file in sorted(set(previous_sources) | set(current_sources)):
        previous = previous_sources.get(source_file)
        current = current_sources.get(source_file)
        previous_exists = previous is not None
        current_exists = current is not None
        previous_size = int(previous["size_bytes"]) if previous is not None else 0
        current_size = int(current["size_bytes"]) if current is not None else 0
        previous_hash = str(previous["content_hash"]) if previous is not None else None
        current_hash = str(current["content_hash"]) if current is not None else None
        source = current or previous
        assert source is not None
        if not previous_exists:
            change_kind = "new"
        elif not current_exists:
            change_kind = "removed"
        elif previous_hash != current_hash:
            change_kind = "changed"
        else:
            change_kind = "unchanged"
        rows.append(
            {
                "source_file": Path(source_file).name,
                "_source_path": source_file,
                "source_kind": source["source_kind"],
                "instrument_id": instrument_id,
                "instrument_serial": instrument_serial,
                "previous_exists": previous_exists,
                "current_exists": current_exists,
                "previous_size_bytes": previous_size,
                "current_size_bytes": current_size,
                "previous_hash": previous_hash,
                "current_hash": current_hash,
                "change_kind": change_kind,
                "decoder_state_changed": bool(decoder_state_changed and source["source_kind"] == "bin"),
                "run_id": run_id,
            }
        )
    return rows


def _select_diff_rows(
    rows: list[dict[str, object]],
    kinds: set[str],
) -> dict[str, list[dict[str, object]]]:
    selected = [row for row in rows if row["source_kind"] in kinds]
    return {
        "added": [row for row in selected if row["change_kind"] == "new"],
        "removed": [row for row in selected if row["change_kind"] == "removed"],
        "changed": [row for row in selected if row["change_kind"] == "changed"],
    }


def _determine_action(
    *,
    diff: dict[str, list[dict[str, object]]],
    invalidate: bool,
    force_rewrite: bool,
) -> str:
    if force_rewrite and (diff["added"] or diff["removed"] or diff["changed"] or invalidate):
        return "rewrite"
    if force_rewrite:
        return "rewrite"
    if invalidate:
        return "rewrite"
    if diff["removed"] or diff["changed"]:
        return "rewrite"
    if diff["added"]:
        return "append"
    return "noop"


def _general_invalidation(
    previous_state: dict[str, object] | None,
    current_state: dict[str, object],
) -> bool:
    if previous_state is None:
        return False
    return (
        previous_state.get("input_root") != current_state.get("input_root")
        or previous_state.get("instrument_serial") != current_state.get("instrument_serial")
        or previous_state.get("normalization_version") != current_state.get("normalization_version")
    )


def _decoder_state_invalidated(
    previous_state: dict[str, object] | None,
    current_state: dict[str, object],
) -> bool:
    if previous_state is None:
        return False
    if _bin_dependent(previous_state, current_state):
        decoder_state = current_state.get("decoder_state")
        if not isinstance(decoder_state, dict) or not decoder_state.get(
            "decoder_environment_fingerprint"
        ):
            return True
    return previous_state.get("decoder_state") != current_state.get("decoder_state")


def _bin_dependent(
    previous_state: dict[str, object] | None,
    current_state: dict[str, object],
) -> bool:
    previous_has_bin = any(
        item["source_kind"] == "bin"
        for item in (previous_state or {}).get("raw_sources", [])
    )
    current_has_bin = any(item["source_kind"] == "bin" for item in current_state["raw_sources"])
    return previous_has_bin or current_has_bin


def _normalization_version() -> str:
    package = sys.modules.get("mermaid_records")
    version = getattr(package, "__version__", None)
    if not isinstance(version, str):
        raise RuntimeError("mermaid_records.__version__ is not available")
    return version


def _rewrite_paths(
    action: str,
    current_sources: list[Path],
    diff: dict[str, list[dict[str, object]]],
    kind: str,
) -> list[Path]:
    if action == "rewrite":
        return _selected_paths(current_sources, {kind})
    return _paths_from_sources([item for item in diff["added"] if item["source_kind"] == kind])


def _accumulate_diff_metrics(metrics: RunMetrics, rows: list[dict[str, object]]) -> None:
    metrics.raw_files_processed += len(rows)
    for row in rows:
        if row["change_kind"] == "new":
            metrics.raw_files_new += 1
        elif row["change_kind"] == "changed":
            metrics.raw_files_changed += 1
        elif row["change_kind"] == "removed":
            metrics.raw_files_removed += 1


def _accumulate_action_metrics(metrics: RunMetrics, summary: InstrumentRunSummary) -> None:
    overall_action = _overall_instrument_action(summary)
    if overall_action == "append":
        metrics.instruments_append += 1
    elif overall_action == "rewrite":
        metrics.instruments_rewrite += 1
    else:
        metrics.instruments_noop += 1

    if summary.log_action == "append":
        metrics.log_instruments_append += 1
    elif summary.log_action == "rewrite":
        metrics.log_instruments_rewrite += 1
    else:
        metrics.log_instruments_noop += 1

    if summary.mer_action == "append":
        metrics.mer_instruments_append += 1
    elif summary.mer_action == "rewrite":
        metrics.mer_instruments_rewrite += 1
    else:
        metrics.mer_instruments_noop += 1


def _accumulate_issue_metrics(
    metrics: RunMetrics,
    *,
    malformed_log_lines: list[dict[str, object]],
    skipped_log_files: list[dict[str, object]],
    malformed_mer_blocks: list[dict[str, object]],
    skipped_mer_files: list[dict[str, object]],
) -> None:
    metrics.malformed_log_lines += len(malformed_log_lines)
    metrics.skipped_log_files += len(skipped_log_files)
    metrics.malformed_mer_blocks += len(malformed_mer_blocks)
    metrics.skipped_mer_files += len(skipped_mer_files)


def _accumulate_output_metrics(
    metrics: RunMetrics,
    *,
    previous_outputs: dict[str, object] | None,
    current_outputs: dict[str, object],
    log_action: str,
    mer_action: str,
    log_summary: LogJsonlSummary | None,
) -> None:
    previous_counts = previous_outputs.get("counts", {}) if previous_outputs is not None else {}
    current_counts = current_outputs.get("counts", {})

    if log_action == "append":
        metrics.log_records_written += max(
            0,
            _sum_counts(current_counts, "log_") - _sum_counts(previous_counts, "log_"),
        )
    elif log_action == "rewrite":
        metrics.log_records_written += _sum_counts(current_counts, "log_")
        metrics.log_records_removed += _sum_counts(previous_counts, "log_")

    if mer_action == "append":
        metrics.mer_records_written += max(
            0,
            _sum_counts(current_counts, "mer_") - _sum_counts(previous_counts, "mer_"),
        )
    elif mer_action == "rewrite":
        metrics.mer_records_written += _sum_counts(current_counts, "mer_")
        metrics.mer_records_removed += _sum_counts(previous_counts, "mer_")

    _accumulate_log_assignment_metrics(metrics, log_summary)


def _sum_counts(counts: dict[str, object], prefix: str) -> int:
    return sum(int(value) for key, value in counts.items() if key.startswith(prefix))


def _accumulate_log_assignment_metrics(
    metrics: RunMetrics,
    log_summary: LogJsonlSummary | None,
) -> None:
    if log_summary is None:
        return
    for family, count in log_summary.family_source_line_counts.items():
        metrics.log_family_source_line_counts[family] = (
            metrics.log_family_source_line_counts.get(family, 0) + count
        )
    metrics.log_ordinary_lines_examined += log_summary.ordinary_log_lines_examined
    metrics.log_source_line_assignments += log_summary.source_line_assignments
    metrics.log_duplicate_assignments += log_summary.duplicate_assignments
    metrics.log_missing_assignments += log_summary.missing_assignments


def _overall_instrument_action(summary: InstrumentRunSummary) -> str:
    if "rewrite" in {summary.log_action, summary.mer_action}:
        return "rewrite"
    if "append" in {summary.log_action, summary.mer_action}:
        return "append"
    return "noop"


def _dry_run_instrument_payload(plan: PlannedInstrumentRun) -> dict[str, object]:
    counts = {
        "total": len(plan.input_file_diffs),
        "new": sum(1 for row in plan.input_file_diffs if row["change_kind"] == "new"),
        "changed": sum(1 for row in plan.input_file_diffs if row["change_kind"] == "changed"),
        "removed": sum(1 for row in plan.input_file_diffs if row["change_kind"] == "removed"),
        "unchanged": sum(1 for row in plan.input_file_diffs if row["change_kind"] == "unchanged"),
    }
    return {
        "instrument_id": plan.summary.instrument_id,
        "instrument_serial": plan.summary.instrument_serial,
        "output_dir": plan.summary.output_dir,
        "counts": counts,
        "families": {
            "log": {
                "action": plan.summary.log_action,
                "file_diffs": _changed_file_rows(plan.input_file_diffs, {"bin", "log"}),
                "decoder_invalidated": _decoder_invalidated_rows(plan.input_file_diffs, {"bin", "log"}),
            },
            "mer": {
                "action": plan.summary.mer_action,
                "file_diffs": _changed_file_rows(plan.input_file_diffs, {"mer"}),
                "decoder_invalidated": [],
            },
        },
    }


def _changed_file_rows(
    rows: list[dict[str, object]],
    kinds: set[str],
) -> list[dict[str, object]]:
    return [
        _public_diff_row(row)
        for row in rows
        if row["source_kind"] in kinds and row["change_kind"] in {"new", "changed", "removed"}
    ]


def _decoder_invalidated_rows(
    rows: list[dict[str, object]],
    kinds: set[str],
) -> list[dict[str, object]]:
    return [
        _public_diff_row(row)
        for row in rows
        if row["source_kind"] in kinds
        and row["decoder_state_changed"]
        and row["change_kind"] == "unchanged"
    ]


def _public_diff_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [_public_diff_row(row) for row in rows]


def _public_diff_row(row: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in row.items() if key != "_source_path"}


def _emit_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)
