# SPDX-License-Identifier: MIT

"""LOG-to-JSONL normalization helpers for conservative record-family outputs."""

from __future__ import annotations

from contextlib import ExitStack
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

from .format_datetime import format_utc_datetime, parse_source_datetime
from .format_record_filenames import (
    record_filenames,
    validate_instrument_serial,
    with_record_metadata,
)
from .models import OperationalLogEntry
from .parse_instrument_name import maybe_parse_instrument_name
from .source_provenance import source_provenance_fields

type _SourceLineKey = tuple[int, int]

# Keep docs/log_record_family_schemas.md in sync when LOG family
# filenames, fields, grouping, classifier rules, or exclusivity change.
BASE_OUTPUT_FILENAMES = {
    "acquisition": "log_acquisition_records.jsonl",
    "ascent_request": "log_ascent_request_records.jsonl",
    "gps": "log_gps_records.jsonl",
    "pressure_temperature": "log_pressure_temperature_records.jsonl",
    "battery": "log_battery_records.jsonl",
    "parameter": "log_parameter_records.jsonl",
    "testmode": "log_testmode_records.jsonl",
    "ctd": "log_ctd_records.jsonl",
    "iridium": "log_iridium_records.jsonl",
    "unclassified": "log_unclassified_records.jsonl",
}
OUTPUT_FILENAMES = BASE_OUTPUT_FILENAMES

_LOG_LINE_RE = re.compile(r"^(?P<time>.+?):\[(?P<tag>[^\]]+)\](?P<message>.*)$")
_EPOCH_SECONDS_RE = re.compile(r"^[+-]?\d+$")
# Shared structurally parsed LOG line family for wrapped source-literal
# severity prefixes such as timestamp:<WARN>[TAG]... and timestamp:<ERR>[TAG]...
# Keep this narrow and corpus-driven rather than treating arbitrary <PREFIX>
# forms as generic tagged LOG syntax.
_WRAPPED_TAGGED_LOG_LINE_RE = re.compile(
    r"^(?P<time>.+?):(?P<prefix><(?:ERR|WARN|WRN)>)\[(?P<tag>[^\]]+)\](?P<message>.*)$"
)
_TIMESTAMPED_LINE_RE = re.compile(r"^(?P<time>.+?):(?P<content>.*)$")
_ROLLOVER_BANNER_RE = re.compile(
    r"^\*\*\*\s+switching to\s+(?P<target>.+?)\s+\*\*\*$",
    re.IGNORECASE,
)
_PARAMETER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"bypass(?:\s|$)|"
    r"valve(?:\s|$)|"
    r"pump(?:\s|$)|"
    r"rate(?:\s|$)|"
    r"surface(?:\s|$)|"
    r"near(?:\s|$)|"
    r"far(?:\s|$)|"
    r"ascent(?:\s|$)|"
    r"dead(?:\s|$)|"
    r"coeff(?:\s|$)|"
    r"stab(?:\s|$)|"
    r"delay(?:\s|$)|"
    r"mmtime(?:\s|$)|"
    r"p2t37:|"
    r"stage\[0\](?:\s|$)|"
    r"stage\[1\](?:\s|$)"
    r")",
    re.IGNORECASE,
)

_ARTIFACT_PATH_PATTERN = r"(?P<artifact>\d{2,4}/[A-Za-z0-9]+\.(?:MER|LOG|BIN))"
_UPLOADED_ARTIFACT_RE = re.compile(
    rf'"{_ARTIFACT_PATH_PATTERN}" uploaded at (?P<rate>\d+)bytes/s',
    re.IGNORECASE,
)
_UPLOAD_PROGRESS_ARTIFACT_RE = re.compile(
    rf"(?P<byte_count>\d+) bytes in {_ARTIFACT_PATH_PATTERN}",
    re.IGNORECASE,
)
_UPLOAD_RESUME_RE = re.compile(
    rf"peer ask to resume {_ARTIFACT_PATH_PATTERN}"
    rf"(?: \((?P<artifact_size_bytes>\d+)bytes\))?"
    rf" from byte (?P<byte_offset>\d+)",
    re.IGNORECASE,
)
_UPLOAD_ERROR_ARTIFACT_RE = re.compile(
    rf"<ERR>\s*upload\b.*?{_ARTIFACT_PATH_PATTERN}",
    re.IGNORECASE,
)
_UPLOAD_RETRY_RE = re.compile(
    r"transfer interrupted\s*,?\s*retry(?: now)?",
    re.IGNORECASE,
)
_UPLOAD_SESSION_SUMMARY_RE = re.compile(
    r"(?P<uploaded_file_count>\d+) file(?:\(s\)|s)? uploaded",
    re.IGNORECASE,
)
_UPLOAD_DISCONNECT_RE = re.compile(
    r"disconnected after (?P<disconnect_duration_s>\d+)s",
    re.IGNORECASE,
)
_UPLOAD_BATCH_RE = re.compile(r"^Upload data files\.\.\.$", re.IGNORECASE)
_IRIDIUM_START_RE = re.compile(r"^Iridium\.\.\.$", re.IGNORECASE)
_IRIDIUM_CONNECT_RE = re.compile(
    r"connected in (?P<connection_duration_s>\d+)s,\s*"
    r"signal quality (?P<signal_quality>[+-]?\d+)",
    re.IGNORECASE,
)
_IRIDIUM_CONNECT_FAILURE_RE = re.compile(
    r"failed to connect #(?P<connect_attempt>\d+),\s*"
    r"code (?P<failure_code>[+-]?\d+),\s*"
    r"net (?P<network>[+-]?\d+),\s*"
    r"qual (?P<signal_quality>[+-]?\d+),\s*"
    r"dial (?P<dial_attempt>[+-]?\d+)",
    re.IGNORECASE,
)
_IRIDIUM_NO_CONNECTION_RE = re.compile(
    r"no connection after (?P<connection_duration_s>\d+)s",
    re.IGNORECASE,
)
_IRIDIUM_COMMAND_RE = re.compile(
    r"^\$(?P<command_name>[A-Za-z0-9_]+)(?::(?P<command_payload>[^;]*))?;$"
)
_IRIDIUM_COMMAND_SUMMARY_RE = re.compile(
    r"(?P<received_command_count>\d+) cmd(?:\(s\)|s)? received",
    re.IGNORECASE,
)
_IRIDIUM_REMOTE_COMMAND_END_RE = re.compile(
    r"prompt received,\s*remote cmd end",
    re.IGNORECASE,
)
_UPLOAD_FAILED_RE = re.compile(r"<ERR>\s*uploading failed", re.IGNORECASE)
_PRESS_TEMP_RE = re.compile(
    r"\bP\s*(?P<pressure_mbar>[+-]?\d+)mbar,\s*T\s*(?P<temperature_mdegc>[+-]?\d+)mdegC\b"
)
_STANDALONE_PRESSURE_MBAR_RE = re.compile(r"^P\s*(?P<pressure_mbar>[+-]?\d+)mbar$")
_DBAR_DEGC_RE = re.compile(
    r"\b(?P<pressure_dbar>[+-]?\d+)dbar,\s*(?P<temperature_degc>[+-]?\d+)degC\b"
)
_INTERNAL_PRESSURE_RE = re.compile(
    r"\binternal pressure\s+(?P<internal_pressure_pa>[+-]?\d+)Pa\b"
)
_PINT_RE = re.compile(r"\bPint\s+(?P<internal_pressure_pa>[+-]?\d+)Pa\b")
_PEXT_RE = re.compile(
    r"\bPext\s+(?P<external_pressure_mbar>[+-]?\d+)mbar\s+"
    r"\(rng\s+(?P<external_pressure_range_mbar>[+-]?\d+)mbar\)"
)
_BATTERY_RE = re.compile(
    r"\bbattery\s+(?P<mv>[+-]?\d+)mV,\s+(?P<ua>[+-]?\d+)uA\b",
    re.IGNORECASE,
)
_VBAT_RE = re.compile(
    r"\bVbat\s+(?P<mv>[+-]?\d+)mV\s+\(min\s+(?P<minimum_mv>[+-]?\d+)mV\)",
    re.IGNORECASE,
)
_GPS_POSITION_RE = re.compile(
    r"(?:Latitude\s*:\s*)?"
    r"(?P<latitude>[NS]\d+deg\d+(?:\.\d+)?mn)\s*,\s*"
    r"(?:Longitude\s*:\s*)?"
    r"(?P<longitude>[EW]\d+deg\d+(?:\.\d+)?mn)",
    re.IGNORECASE,
)
_HDOP_RE = re.compile(r"\bhdop\s+(?P<hdop>[+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_VDOP_RE = re.compile(r"\bvdop\s+(?P<vdop>[+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_GPSACK_RE = re.compile(r"\$?GPSACK:(?P<payload>[^;]+)")
_GPSOFF_RE = re.compile(r"\$?GPSOFF:(?P<offset>[+-]?\d+)")
_CTD_SAMPLE_RE = re.compile(
    r"\bP\s*(?P<pressure>[+-]?\d+)\s*,\s*"
    r"T\s*(?P<temperature>[+-]?\d+)\s*,\s*"
    r"S\s*(?P<salinity>[+-]?\d+)\b"
)


@dataclass(slots=True)
class LogJsonlSummary:
    """Summary of generated LOG-derived JSONL streams."""

    total_records: int
    acquisition_records: int
    ascent_request_records: int
    gps_records: int
    pressure_temperature_records: int
    battery_records: int
    parameter_records: int
    testmode_records: int
    ctd_records: int
    iridium_records: int
    unclassified_records: int
    ordinary_log_lines_examined: int
    source_line_assignments: int
    duplicate_assignments: int
    missing_assignments: int
    family_record_counts: dict[str, int]
    family_source_line_counts: dict[str, int]
    acquisition_state_counts: dict[str, int]
    acquisition_evidence_kind_counts: dict[str, int]
    acquisition_examples: dict[str, dict[str, object]]
    ascent_request_state_counts: dict[str, int]
    ascent_request_examples: dict[str, dict[str, object]]
    gps_record_kind_counts: dict[str, int]
    gps_examples: dict[str, dict[str, object]]
    parameter_examples: list[dict[str, object]]
    testmode_examples: list[dict[str, object]]
    ctd_examples: list[dict[str, object]]
    iridium_examples: list[dict[str, object]]
    pressure_temperature_examples: list[dict[str, object]]
    battery_examples: list[dict[str, object]]
    unclassified_examples: list[dict[str, object]]
    common_unclassified_patterns: list[dict[str, object]]

    @property
    def transmission_records(self) -> int:
        """Backward-compatible alias for the renamed Iridium family count."""

        return self.iridium_records

    @property
    def transmission_examples(self) -> list[dict[str, object]]:
        """Backward-compatible alias for the renamed Iridium family examples."""

        return self.iridium_examples


@dataclass(slots=True)
class _GroupedEpisodeLine:
    line_number: int
    raw_line: str
    time: datetime | None
    log_epoch_time: str | None


@dataclass(slots=True)
class _GroupedEpisode:
    family: str
    episode_index: int
    lines: list[_GroupedEpisodeLine]
    group_kind: str | None = None


@dataclass(slots=True)
class _ParsedTaggedLogLine:
    time_text: str
    subsystem: str
    code: str | None
    message: str


@dataclass(slots=True)
class _FamilyAssignment:
    family: str
    record: dict[str, object]


@dataclass(slots=True)
class _DerivedFamilyMatch:
    family: str
    record: dict[str, object]


def output_filenames(instrument_serial: str) -> dict[str, str]:
    """Return LOG-derived output filenames for one instrument serial."""

    return record_filenames(BASE_OUTPUT_FILENAMES, instrument_serial)


def _common_log_record_fields(
    entry: OperationalLogEntry,
    *,
    instrument_id: str,
) -> dict[str, object]:
    """Return shared provenance and source fields for LOG-derived records."""

    fields: dict[str, object] = {
        "instrument_id": instrument_id,
        **source_provenance_fields(entry.source_file),
        "source_container": "log",
        "record_time": format_utc_datetime(entry.time),
        "log_epoch_time": _log_epoch_time(entry),
        "subsystem": entry.subsystem,
        "code": entry.code,
        "message": entry.message,
    }
    if entry.line_number is not None:
        fields["source_line_number"] = entry.line_number
    return fields


def write_log_jsonl_families(
    log_paths: Iterable[Path],
    output_dir: Path,
    *,
    instrument_id: str | None = None,
    instrument_serial: str | None = None,
    authoritative_source_files: Mapping[Path, Path] | None = None,
    run_id: str | None = None,
    fail_on_malformed: bool = False,
    malformed_log_lines: list[dict[str, object]] | None = None,
    skipped_log_files: list[dict[str, object]] | None = None,
) -> LogJsonlSummary:
    """Write conservative LOG-derived JSONL streams.

    ``log_paths`` identifies the LOG content to read. An optional authoritative
    source mapping keeps emitted provenance tied to an original BIN when the
    readable LOG is a temporary decoder artifact.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    sorted_paths = sorted(Path(path) for path in log_paths)
    output_instrument_serial = _resolve_output_instrument_serial(
        sorted_paths,
        instrument_serial=instrument_serial,
    )
    rendered_output_filenames = output_filenames(output_instrument_serial)
    output_paths = {
        name: output_dir / filename
        for name, filename in rendered_output_filenames.items()
    }

    total_records = 0
    acquisition_count = 0
    ascent_request_count = 0
    gps_count = 0
    parameter_count = 0
    testmode_count = 0
    ctd_count = 0
    iridium_count = 0
    pressure_temperature_count = 0
    battery_count = 0
    unclassified_count = 0
    source_file_indexes = {path: index for index, path in enumerate(sorted_paths)}
    ordinary_line_keys: set[_SourceLineKey] = set()
    assignment_counts: Counter[_SourceLineKey] = Counter()
    family_source_line_counter: Counter[str] = Counter()
    acquisition_state_counter: Counter[str] = Counter()
    acquisition_evidence_kind_counter: Counter[str] = Counter()
    acquisition_examples: dict[str, dict[str, object]] = {}
    ascent_request_state_counter: Counter[str] = Counter()
    ascent_request_examples: dict[str, dict[str, object]] = {}
    gps_record_kind_counter: Counter[str] = Counter()
    gps_examples: dict[str, dict[str, object]] = {}
    parameter_examples: list[dict[str, object]] = []
    testmode_examples: list[dict[str, object]] = []
    ctd_examples: list[dict[str, object]] = []
    iridium_examples: list[dict[str, object]] = []
    pressure_temperature_examples: list[dict[str, object]] = []
    battery_examples: list[dict[str, object]] = []
    unclassified_examples: list[dict[str, object]] = []
    unclassified_patterns: Counter[tuple[str, str | None, str]] = Counter()

    with ExitStack() as stack:
        handles = {
            family: stack.enter_context(path.open("w", encoding="utf-8"))
            for family, path in output_paths.items()
        }
        for path in sorted_paths:
            authoritative_source = (
                authoritative_source_files.get(path, path)
                if authoritative_source_files is not None
                else path
            )
            path_instrument_id = instrument_id or _fallback_instrument_id(
                authoritative_source
            )
            path_instrument_serial = output_instrument_serial

            def _record_malformed_line(
                line_number: int,
                raw_line: str,
                error: str,
            ) -> None:
                if fail_on_malformed:
                    raise ValueError(
                        f"Malformed LOG line {line_number} in {authoritative_source}: {error}"
                    )
                if malformed_log_lines is None or run_id is None:
                    return
                malformed_log_lines.append(
                    {
                        "run_id": run_id,
                        "instrument_id": path_instrument_id,
                        "instrument_serial": path_instrument_serial,
                        "source_file": authoritative_source.as_posix(),
                        "line_number": line_number,
                        "raw_line": raw_line,
                        "error": error,
                    }
                )
            try:
                for item in _iter_log_source_units(
                    path,
                    on_malformed_line=_record_malformed_line,
                ):
                    if isinstance(item, OperationalLogEntry):
                        total_records += 1
                        entry = item
                        source_keys = _source_line_keys_for_entry(
                            entry,
                            source_file_indexes=source_file_indexes,
                        )
                        entry.source_file = authoritative_source
                        ordinary_line_keys.update(source_keys)
                        assignment = _classify_log_line(
                            entry,
                            instrument_id=path_instrument_id,
                        )
                        record = with_record_metadata(
                            assignment.record,
                            path_instrument_serial,
                        )
                        _write_jsonl_line(handles[assignment.family], record)
                        _record_source_line_assignments(
                            assignment_counts,
                            family_source_line_counter,
                            family=assignment.family,
                            source_keys=source_keys,
                        )

                        if assignment.family == "acquisition":
                            acquisition_count += 1
                            acquisition_state_counter[
                                record["acquisition_state"]
                            ] += 1
                            acquisition_evidence_kind_counter[
                                record["acquisition_evidence_kind"]
                            ] += 1
                            example_key = (
                                f"{record['acquisition_state']}:"
                                f"{record['acquisition_evidence_kind']}"
                            )
                            acquisition_examples.setdefault(example_key, record)

                        elif assignment.family == "ascent_request":
                            ascent_request_count += 1
                            ascent_request_state_counter[
                                record["ascent_request_state"]
                            ] += 1
                            ascent_request_examples.setdefault(
                                record["ascent_request_state"],
                                record,
                            )

                        elif assignment.family == "gps":
                            gps_count += 1
                            gps_record_kind_counter[record["gps_record_kind"]] += 1
                            gps_examples.setdefault(record["gps_record_kind"], record)

                        elif assignment.family == "iridium":
                            iridium_count += 1
                            if len(iridium_examples) < 3:
                                iridium_examples.append(record)

                        elif assignment.family == "pressure_temperature":
                            pressure_temperature_count += 1
                            if len(pressure_temperature_examples) < 3:
                                pressure_temperature_examples.append(record)

                        elif assignment.family == "battery":
                            battery_count += 1
                            if len(battery_examples) < 3:
                                battery_examples.append(record)

                        elif assignment.family == "unclassified":
                            unclassified_count += 1
                            if len(unclassified_examples) < 3:
                                unclassified_examples.append(record)
                            unclassified_patterns[
                                (entry.subsystem, entry.code, entry.message)
                            ] += 1
                        continue

                    total_records += 1
                    source_keys = _source_line_keys_for_episode(
                        item,
                        source_file=path,
                        source_file_indexes=source_file_indexes,
                    )
                    ordinary_line_keys.update(source_keys)
                    episode_record = _build_grouped_episode_record(
                        item,
                        instrument_id=path_instrument_id,
                        source_file=authoritative_source,
                    )
                    episode_record = with_record_metadata(
                        episode_record,
                        path_instrument_serial,
                    )
                    if item.family == "parameter":
                        _write_jsonl_line(handles["parameter"], episode_record)
                        parameter_count += 1
                        _record_source_line_assignments(
                            assignment_counts,
                            family_source_line_counter,
                            family="parameter",
                            source_keys=source_keys,
                        )
                        if len(parameter_examples) < 3:
                            parameter_examples.append(episode_record)
                    elif item.family == "testmode":
                        _write_jsonl_line(handles["testmode"], episode_record)
                        testmode_count += 1
                        _record_source_line_assignments(
                            assignment_counts,
                            family_source_line_counter,
                            family="testmode",
                            source_keys=source_keys,
                        )
                        if len(testmode_examples) < 3:
                            testmode_examples.append(episode_record)
                    elif item.family == "ctd":
                        _write_jsonl_line(handles["ctd"], episode_record)
                        ctd_count += 1
                        _record_source_line_assignments(
                            assignment_counts,
                            family_source_line_counter,
                            family="ctd",
                            source_keys=source_keys,
                        )
                        if len(ctd_examples) < 3:
                            ctd_examples.append(episode_record)
                    else:
                        _write_jsonl_line(handles["iridium"], episode_record)
                        iridium_count += 1
                        _record_source_line_assignments(
                            assignment_counts,
                            family_source_line_counter,
                            family="iridium",
                            source_keys=source_keys,
                        )
                        if len(iridium_examples) < 3:
                            iridium_examples.append(episode_record)
            except OSError as exc:
                if skipped_log_files is None or run_id is None:
                    raise
                skipped_log_files.append(
                    {
                        "run_id": run_id,
                        "instrument_id": path_instrument_id,
                        "instrument_serial": path_instrument_serial,
                        "source_file": authoritative_source.as_posix(),
                        "error": str(exc),
                        "skipped_at": _iso_now(),
                    }
                )
                continue
            except Exception as exc:
                raise ValueError(f"Error while normalizing LOG file {path}: {exc}") from exc

    common_patterns = [
        {
            "subsystem": subsystem,
            "code": code,
            "message": message,
            "count": count,
        }
        for (subsystem, code, message), count in unclassified_patterns.most_common(10)
    ]
    duplicate_assignments = sum(
        count - 1 for count in assignment_counts.values() if count > 1
    )
    missing_assignments = sum(
        1 for key in ordinary_line_keys if assignment_counts.get(key, 0) == 0
    )
    if duplicate_assignments or missing_assignments:
        duplicate_examples = [
            f"{_source_line_key_example(source_key, source_paths=sorted_paths)} -> {count}"
            for source_key, count in assignment_counts.items()
            if count > 1
        ][:3]
        missing_examples = [
            _source_line_key_example(source_key, source_paths=sorted_paths)
            for source_key in ordinary_line_keys
            if assignment_counts.get(source_key, 0) == 0
        ][:3]
        example_parts = []
        if duplicate_examples:
            example_parts.append(f"duplicate_examples={duplicate_examples!r}")
        if missing_examples:
            example_parts.append(f"missing_examples={missing_examples!r}")
        examples_text = " " + " ".join(example_parts) if example_parts else ""
        raise ValueError(
            "LOG source-line assignment invariant failed: "
            f"ordinary_lines={len(ordinary_line_keys)} "
            f"assignments={sum(assignment_counts.values())} "
            f"duplicates={duplicate_assignments} missing={missing_assignments}"
            f"{examples_text}"
        )
    family_record_counts_by_family = {
        "acquisition": acquisition_count,
        "ascent_request": ascent_request_count,
        "gps": gps_count,
        "pressure_temperature": pressure_temperature_count,
        "battery": battery_count,
        "parameter": parameter_count,
        "testmode": testmode_count,
        "ctd": ctd_count,
        "iridium": iridium_count,
        "unclassified": unclassified_count,
    }
    family_record_counts = {
        rendered_output_filenames[family]: count
        for family, count in family_record_counts_by_family.items()
    }
    family_source_line_counts = {
        rendered_output_filenames[family]: family_source_line_counter[family]
        for family in BASE_OUTPUT_FILENAMES
    }

    return LogJsonlSummary(
        total_records=total_records,
        acquisition_records=acquisition_count,
        ascent_request_records=ascent_request_count,
        gps_records=gps_count,
        pressure_temperature_records=pressure_temperature_count,
        battery_records=battery_count,
        parameter_records=parameter_count,
        testmode_records=testmode_count,
        ctd_records=ctd_count,
        iridium_records=iridium_count,
        unclassified_records=unclassified_count,
        ordinary_log_lines_examined=len(ordinary_line_keys),
        source_line_assignments=sum(assignment_counts.values()),
        duplicate_assignments=duplicate_assignments,
        missing_assignments=missing_assignments,
        family_record_counts=family_record_counts,
        family_source_line_counts=family_source_line_counts,
        acquisition_state_counts=dict(acquisition_state_counter),
        acquisition_evidence_kind_counts=dict(acquisition_evidence_kind_counter),
        acquisition_examples=acquisition_examples,
        ascent_request_state_counts=dict(ascent_request_state_counter),
        ascent_request_examples=ascent_request_examples,
        gps_record_kind_counts=dict(gps_record_kind_counter),
        gps_examples=gps_examples,
        parameter_examples=parameter_examples,
        testmode_examples=testmode_examples,
        ctd_examples=ctd_examples,
        iridium_examples=iridium_examples,
        pressure_temperature_examples=pressure_temperature_examples,
        battery_examples=battery_examples,
        unclassified_examples=unclassified_examples,
        common_unclassified_patterns=common_patterns,
    )


def _iter_log_source_units(
    path: Path,
    *,
    on_malformed_line,
) -> Iterable[OperationalLogEntry | _GroupedEpisode]:
    _validate_log_path(path)
    current_episode: _GroupedEpisode | None = None
    episode_indexes = {"parameter": 0, "testmode": 0, "ctd": 0, "iridium": 0}

    def _start_episode(family: str, *, group_kind: str | None = None) -> None:
        nonlocal current_episode
        current_episode = _GroupedEpisode(
            family=family,
            episode_index=episode_indexes[family],
            lines=[],
            group_kind=group_kind,
        )
        episode_indexes[family] += 1

    def _flush_episode() -> _GroupedEpisode | None:
        nonlocal current_episode
        if current_episode is None or not current_episode.lines:
            return None
        episode = current_episode
        current_episode = None
        return episode

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                if current_episode is not None and current_episode.family == "testmode":
                    current_episode.lines.append(_grouped_line(line_number=line_number, raw_line=line))
                    continue
                episode = _flush_episode()
                if episode is not None:
                    yield episode
                continue

            tagged_line = _parse_tagged_log_line(line)
            if current_episode is not None and current_episode.family == "testmode":
                current_episode.lines.append(
                    _grouped_line(
                        line_number=line_number,
                        raw_line=line,
                        tagged_line=tagged_line,
                    )
                )
                if tagged_line is not None and _is_testmode_exit_line(tagged_line):
                    episode = _flush_episode()
                    if episode is not None:
                        yield episode
                continue

            if tagged_line is not None:
                if current_episode is not None and current_episode.family == "iridium":
                    if _is_iridium_start_line(tagged_line):
                        episode = _flush_episode()
                        if episode is not None:
                            yield episode
                        _start_episode("iridium", group_kind="explicit_session")
                        assert current_episode is not None
                        current_episode.lines.append(
                            _grouped_line(
                                line_number=line_number,
                                raw_line=line,
                                tagged_line=tagged_line,
                            )
                        )
                        continue
                    if _is_iridium_session_line(tagged_line):
                        current_episode.lines.append(
                            _grouped_line(
                                line_number=line_number,
                                raw_line=line,
                                tagged_line=tagged_line,
                            )
                        )
                        if _is_iridium_end_line(tagged_line):
                            episode = _flush_episode()
                            if episode is not None:
                                yield episode
                        continue
                    episode = _flush_episode()
                    if episode is not None:
                        yield episode

                if _is_testmode_start_line(tagged_line):
                    episode = _flush_episode()
                    if episode is not None:
                        yield episode
                    _start_episode("testmode")
                    assert current_episode is not None
                    current_episode.lines.append(
                        _grouped_line(
                            line_number=line_number,
                            raw_line=line,
                            tagged_line=tagged_line,
                        )
                    )
                    if _is_testmode_exit_line(tagged_line):
                        episode = _flush_episode()
                        if episode is not None:
                            yield episode
                    continue

                if _is_ctd_start_or_continue_line(tagged_line, active_episode=current_episode):
                    if current_episode is None or current_episode.family != "ctd":
                        episode = _flush_episode()
                        if episode is not None:
                            yield episode
                        _start_episode("ctd")
                    assert current_episode is not None
                    current_episode.lines.append(
                        _grouped_line(
                            line_number=line_number,
                            raw_line=line,
                            tagged_line=tagged_line,
                        )
                    )
                    continue

                if _is_iridium_start_line(tagged_line):
                    episode = _flush_episode()
                    if episode is not None:
                        yield episode
                    _start_episode("iridium", group_kind="explicit_session")
                    assert current_episode is not None
                    current_episode.lines.append(
                        _grouped_line(
                            line_number=line_number,
                            raw_line=line,
                            tagged_line=tagged_line,
                        )
                    )
                    continue

                if _is_iridium_event_line(tagged_line):
                    episode = _flush_episode()
                    if episode is not None:
                        yield episode
                    _start_episode("iridium", group_kind="event_sequence")
                    assert current_episode is not None
                    current_episode.lines.append(
                        _grouped_line(
                            line_number=line_number,
                            raw_line=line,
                            tagged_line=tagged_line,
                        )
                    )
                    if _is_iridium_end_line(tagged_line):
                        episode = _flush_episode()
                        if episode is not None:
                            yield episode
                    continue

                episode = _flush_episode()
                if episode is not None:
                    yield episode
                try:
                    yield OperationalLogEntry(
                        time=_parse_time_text(tagged_line.time_text),
                        subsystem=tagged_line.subsystem,
                        code=tagged_line.code,
                        message=tagged_line.message,
                        source_kind="log",
                        raw_line=line,
                        source_file=path,
                        line_number=line_number,
                    )
                except Exception as exc:
                    _report_malformed_line(
                        on_malformed_line,
                        line_number=line_number,
                        raw_line=line,
                        error=str(exc),
                    )
                continue

            parameter_line = _parse_parameter_episode_line(line_number=line_number, line=line)
            if parameter_line is not None:
                if current_episode is None or current_episode.family != "parameter":
                    episode = _flush_episode()
                    if episode is not None:
                        yield episode
                    _start_episode("parameter")
                assert current_episode is not None
                current_episode.lines.append(parameter_line)
                continue

            episode = _flush_episode()
            if episode is not None:
                yield episode
            rollover_entry = _parse_rollover_banner(
                path=path,
                line=line,
                line_number=line_number,
            )
            if rollover_entry is not None:
                yield rollover_entry
                continue
            _report_malformed_line(
                on_malformed_line,
                line_number=line_number,
                raw_line=line,
                error="line does not match expected LOG pattern",
            )

    episode = _flush_episode()
    if episode is not None:
        yield episode


def _parse_parameter_episode_line(
    *,
    line_number: int,
    line: str,
) -> _GroupedEpisodeLine | None:
    match = _TIMESTAMPED_LINE_RE.match(line)
    if match is None:
        return None
    content = match.group("content")
    if _PARAMETER_PREFIX_RE.match(content) is None:
        return None
    return _grouped_line(
        line_number=line_number,
        raw_line=line,
        tagged_line=None,
    )


def _grouped_line(
    *,
    line_number: int,
    raw_line: str,
    tagged_line: _ParsedTaggedLogLine | None = None,
) -> _GroupedEpisodeLine:
    raw_time: str | None
    if tagged_line is None:
        timestamp_match = _TIMESTAMPED_LINE_RE.match(raw_line)
        if timestamp_match is None:
            return _GroupedEpisodeLine(
                line_number=line_number,
                raw_line=raw_line,
                time=None,
                log_epoch_time=None,
            )
        raw_time = timestamp_match.group("time")
    else:
        raw_time = tagged_line.time_text
    try:
        parsed_time = _parse_time_text(raw_time)
    except ValueError:
        return _GroupedEpisodeLine(
            line_number=line_number,
            raw_line=raw_line,
            time=None,
            log_epoch_time=None,
        )
    return _GroupedEpisodeLine(
        line_number=line_number,
        raw_line=raw_line,
        time=parsed_time,
        log_epoch_time=raw_time,
    )


def _build_grouped_episode_record(
    episode: _GroupedEpisode,
    *,
    instrument_id: str,
    source_file: Path,
) -> dict[str, object]:
    if episode.family == "iridium":
        return _build_iridium_session_record(
            episode,
            instrument_id=instrument_id,
            source_file=source_file,
        )

    timestamped_lines = [line for line in episode.lines if line.time is not None and line.log_epoch_time is not None]
    if not timestamped_lines:
        raise ValueError(f"{episode.family} episode has no timestamped lines")
    first_line = timestamped_lines[0]
    last_line = timestamped_lines[-1]
    record: dict[str, object] = {
        "instrument_id": instrument_id,
        **source_provenance_fields(source_file),
        "episode_index": episode.episode_index,
        "line_start_index": first_line.line_number,
        "line_end_index": last_line.line_number,
        "source_line_numbers": [line.line_number for line in episode.lines],
        "start_record_time": format_utc_datetime(first_line.time),
        "end_record_time": format_utc_datetime(last_line.time),
        "start_log_epoch_time": first_line.log_epoch_time,
        "end_log_epoch_time": last_line.log_epoch_time,
        "raw_lines": [line.raw_line for line in episode.lines],
    }
    if episode.family == "ctd":
        ctd_samples = _ctd_samples_for_episode(episode)
        record["ctd_sample_count"] = len(ctd_samples)
        record["ctd_samples"] = ctd_samples
    return record


def _build_iridium_session_record(
    episode: _GroupedEpisode,
    *,
    instrument_id: str,
    source_file: Path,
) -> dict[str, object]:
    timestamped_lines = [
        line
        for line in episode.lines
        if line.time is not None and line.log_epoch_time is not None
    ]
    if not timestamped_lines:
        raise ValueError("iridium episode has no timestamped lines")
    first_line = timestamped_lines[0]
    last_line = timestamped_lines[-1]
    events = _iridium_events_for_episode(episode)
    return {
        "instrument_id": instrument_id,
        **source_provenance_fields(source_file),
        "source_container": "log",
        "session_index": episode.episode_index,
        "session_kind": episode.group_kind or "event_sequence",
        "line_start_index": first_line.line_number,
        "line_end_index": last_line.line_number,
        "source_line_numbers": [line.line_number for line in episode.lines],
        "start_record_time": format_utc_datetime(first_line.time),
        "end_record_time": format_utc_datetime(last_line.time),
        "start_log_epoch_time": first_line.log_epoch_time,
        "end_log_epoch_time": last_line.log_epoch_time,
        "iridium_event_count": len(events),
        "iridium_events": events,
        "raw_lines": [line.raw_line for line in episode.lines],
    }


def _iridium_events_for_episode(episode: _GroupedEpisode) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in episode.lines:
        tagged_line = _parse_tagged_log_line(line.raw_line)
        if tagged_line is None:
            continue
        event: dict[str, object] = {
            "source_line_number": line.line_number,
            "record_time": (
                format_utc_datetime(line.time) if line.time is not None else None
            ),
            "log_epoch_time": line.log_epoch_time,
            "subsystem": tagged_line.subsystem,
            "code": tagged_line.code,
            "message": tagged_line.message,
            **_iridium_event_payload(tagged_line.message),
            "raw_line": line.raw_line,
        }
        events.append(event)
    return events


def _ctd_samples_for_episode(episode: _GroupedEpisode) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for line in episode.lines:
        tagged_line = _parse_tagged_log_line(line.raw_line)
        if tagged_line is None:
            continue
        match = _CTD_SAMPLE_RE.search(tagged_line.message)
        if match is None:
            continue
        pressure = match.group("pressure")
        temperature = match.group("temperature")
        salinity = match.group("salinity")
        samples.append(
            {
                "source_line_number": line.line_number,
                "raw_values": {
                    "P": pressure,
                    "T": temperature,
                    "S": salinity,
                },
                "pressure_cbar_tenths": int(pressure),
                "temperature_mdegc_tenths": int(temperature),
                "salinity_psu_thousandths": int(salinity),
            }
        )
    return samples


def _source_line_keys_for_entry(
    entry: OperationalLogEntry,
    *,
    source_file_indexes: dict[Path, int],
) -> set[_SourceLineKey]:
    if entry.line_number is None:
        raise ValueError(f"Parsed LOG entry is missing source line number: {entry.raw_line!r}")
    return {(source_file_indexes[entry.source_file], entry.line_number)}


def _source_line_keys_for_episode(
    episode: _GroupedEpisode,
    *,
    source_file: Path,
    source_file_indexes: dict[Path, int],
) -> set[_SourceLineKey]:
    return {
        (source_file_indexes[source_file], line.line_number)
        for line in episode.lines
        if line.raw_line.strip()
    }


def _record_source_line_assignments(
    assignment_counts: Counter[_SourceLineKey],
    family_source_line_counter: Counter[str],
    *,
    family: str,
    source_keys: set[_SourceLineKey],
) -> None:
    for source_key in source_keys:
        assignment_counts[source_key] += 1
        family_source_line_counter[family] += 1


def _source_line_key_example(
    source_key: _SourceLineKey,
    *,
    source_paths: list[Path],
) -> str:
    source_file_index, line_number = source_key
    source_path = source_paths[source_file_index]
    raw_line = _read_source_line(source_path, line_number)
    return f"{source_path.as_posix()}:{line_number} {raw_line!r}"


def _read_source_line(source_path: Path, line_number: int) -> str:
    with source_path.open("r", encoding="utf-8", errors="replace") as handle:
        for current_line_number, raw_line in enumerate(handle, start=1):
            if current_line_number == line_number:
                return raw_line.rstrip("\r\n")
    return "<line unavailable>"


def _is_testmode_start_line(tagged_line: _ParsedTaggedLogLine) -> bool:
    return tagged_line.subsystem == "TESTMD"


def _is_testmode_exit_line(tagged_line: _ParsedTaggedLogLine) -> bool:
    message = tagged_line.message.strip().lower()
    if tagged_line.subsystem == "TESTMD" and message in {'"q"', '"quit"'}:
        return True
    return message in {
        "end of test mode",
        "reboot mermaid board",
        "reboot float",
    } or message.startswith("rebooting with code")


def _is_ctd_start_or_continue_line(
    tagged_line: _ParsedTaggedLogLine,
    *,
    active_episode: _GroupedEpisode | None,
) -> bool:
    if tagged_line.subsystem in {"SBE", "SBE41", "SBE61", "PROFIL"}:
        return True
    if tagged_line.subsystem == "STAGE":
        if "SBE41" in tagged_line.message or "SBE61" in tagged_line.message:
            return True
        return active_episode is not None and active_episode.family == "ctd"
    return False


def _parse_tag(tag: str) -> tuple[str, str | None]:
    if "," not in tag:
        return tag.strip(), None
    subsystem, code = tag.split(",", maxsplit=1)
    return subsystem.strip(), code.strip() or None


def _parse_tagged_log_line(line: str) -> _ParsedTaggedLogLine | None:
    """Parse shared tagged LOG line families.

    Supports both the standard timestamp:[TAG]message form and the wrapped
    source-literal severity-prefix form used in corpus lines such as
    timestamp:<WARN>[TAG]message and timestamp:<ERR>[TAG]message.
    """

    standard_match = _LOG_LINE_RE.match(line)
    if standard_match is not None:
        subsystem, code = _parse_tag(standard_match.group("tag"))
        return _ParsedTaggedLogLine(
            time_text=standard_match.group("time"),
            subsystem=subsystem,
            code=code,
            message=standard_match.group("message"),
        )

    wrapped_match = _WRAPPED_TAGGED_LOG_LINE_RE.match(line)
    if wrapped_match is None:
        return None

    subsystem, code = _parse_tag(wrapped_match.group("tag"))
    return _ParsedTaggedLogLine(
        time_text=wrapped_match.group("time"),
        subsystem=subsystem,
        code=code,
        message=f"{wrapped_match.group('prefix')}{wrapped_match.group('message')}",
    )


def _validate_log_path(path: Path) -> None:
    if path.suffix.upper() != ".LOG":
        raise ValueError(f"Unsupported operational log source: {path}")


def _parse_time_text(text: str) -> datetime:
    if _EPOCH_SECONDS_RE.fullmatch(text) is not None:
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    return parse_source_datetime(text)


def _report_malformed_line(
    callback,
    *,
    line_number: int,
    raw_line: str,
    error: str,
) -> None:
    if callback is not None:
        callback(line_number, raw_line, error)


def _severity(message: str) -> str | None:
    if "<ERR>" in message:
        return "err"
    if "<WARN>" in message or "<WRN>" in message:
        return "warn"
    return None


def _classify_log_line(
    entry: OperationalLogEntry,
    *,
    instrument_id: str,
) -> _FamilyAssignment:
    match = _single_specific_family_match(entry, instrument_id=instrument_id)
    if match is not None:
        return _FamilyAssignment(family=match.family, record=match.record)

    return _FamilyAssignment(
        family="unclassified",
        record={
            **_common_log_record_fields(entry, instrument_id=instrument_id),
            **_rollover_fields(entry),
            "severity": _severity(entry.message),
            "unclassified_reason": "no_family_match",
            "raw_line": entry.raw_line,
        },
    )


def _collect_specific_family_matches(
    entry: OperationalLogEntry,
    *,
    instrument_id: str,
) -> list[_DerivedFamilyMatch]:
    matches: list[_DerivedFamilyMatch] = []
    for family, record in (
        ("acquisition", _classify_acquisition(entry, instrument_id=instrument_id)),
        ("ascent_request", _classify_ascent_request(entry, instrument_id=instrument_id)),
        ("gps", _classify_gps(entry, instrument_id=instrument_id)),
        (
            "pressure_temperature",
            _classify_pressure_temperature(entry, instrument_id=instrument_id),
        ),
        ("battery", _classify_battery(entry, instrument_id=instrument_id)),
    ):
        if record is not None:
            matches.append(_DerivedFamilyMatch(family=family, record=record))
    return matches


def _single_specific_family_match(
    entry: OperationalLogEntry,
    *,
    instrument_id: str,
) -> _DerivedFamilyMatch | None:
    matches = _collect_specific_family_matches(entry, instrument_id=instrument_id)
    if len(matches) <= 1:
        return matches[0] if matches else None

    families = ", ".join(match.family for match in matches)
    raise ValueError(
        "Operational derived-family multi-match: "
        f"{families} for line {entry.raw_line!r}"
    )


def _classify_acquisition(
    entry: OperationalLogEntry,
    *,
    instrument_id: str,
) -> dict[str, object] | None:
    normalized_message = " ".join(entry.message.lower().split())
    mapping = {
        "acq started": ("started", "transition"),
        "acq stopped": ("stopped", "transition"),
        "acq already started": ("started", "assertion"),
        "acq already stopped": ("stopped", "assertion"),
    }
    details = mapping.get(normalized_message)
    if details is None:
        return None

    acquisition_state, acquisition_evidence_kind = details
    return {
        **_common_log_record_fields(entry, instrument_id=instrument_id),
        "acquisition_state": acquisition_state,
        "acquisition_evidence_kind": acquisition_evidence_kind,
        "raw_line": entry.raw_line,
    }


def _classify_ascent_request(
    entry: OperationalLogEntry,
    *,
    instrument_id: str,
) -> dict[str, object] | None:
    normalized_message = " ".join(entry.message.lower().split())
    mapping = {
        "ascent request accepted": "accepted",
        "ascent request rejected": "rejected",
    }
    ascent_request_state = mapping.get(normalized_message)
    if ascent_request_state is None:
        return None

    return {
        **_common_log_record_fields(entry, instrument_id=instrument_id),
        "ascent_request_state": ascent_request_state,
        "raw_line": entry.raw_line,
    }


def _classify_gps(entry: OperationalLogEntry, *, instrument_id: str) -> dict[str, object] | None:
    message = entry.message.strip()
    gps_record_kind: str | None = None
    raw_values: dict[str, str] | None = None

    if "GPS fix..." in message:
        gps_record_kind = "fix_attempt"
    else:
        position_match = _GPS_POSITION_RE.search(message)
        hdop_match = _HDOP_RE.search(message)
        vdop_match = _VDOP_RE.search(message)
        gpsack_match = _GPSACK_RE.search(message)
        gpsoff_match = _GPSOFF_RE.search(message)

        if position_match is not None:
            gps_record_kind = "fix_position"
            raw_values = {
                "latitude": position_match.group("latitude"),
                "longitude": position_match.group("longitude"),
            }
        elif hdop_match is not None or vdop_match is not None:
            gps_record_kind = "dop"
            raw_values = {}
            if hdop_match is not None:
                raw_values["hdop"] = hdop_match.group("hdop")
            if vdop_match is not None:
                raw_values["vdop"] = vdop_match.group("vdop")
        elif gpsack_match is not None:
            gps_record_kind = "gps_ack"
            raw_values = {"gpsack": gpsack_match.group("payload")}
        elif gpsoff_match is not None:
            gps_record_kind = "gps_off"
            raw_values = {"gpsoff": gpsoff_match.group("offset")}

    if gps_record_kind is None:
        return None

    return {
        **_common_log_record_fields(entry, instrument_id=instrument_id),
        "gps_record_kind": gps_record_kind,
        "raw_values": raw_values,
        "raw_line": entry.raw_line,
    }


def _is_iridium_start_line(tagged_line: _ParsedTaggedLogLine) -> bool:
    return _IRIDIUM_START_RE.search(tagged_line.message.strip()) is not None


def _is_iridium_end_line(tagged_line: _ParsedTaggedLogLine) -> bool:
    message = tagged_line.message.strip()
    return (
        _UPLOAD_DISCONNECT_RE.search(message) is not None
        or _IRIDIUM_NO_CONNECTION_RE.search(message) is not None
    )


def _is_iridium_event_line(tagged_line: _ParsedTaggedLogLine) -> bool:
    return (
        _iridium_event_payload(tagged_line.message)["iridium_event_kind"]
        != "session_line"
    )


def _is_iridium_session_line(tagged_line: _ParsedTaggedLogLine) -> bool:
    """Return whether a tagged line can remain inside an explicit session."""

    if _is_iridium_event_line(tagged_line):
        return True
    return _IRIDIUM_COMMAND_RE.search(tagged_line.message.strip()) is not None


def _iridium_event_payload(message: str) -> dict[str, object]:
    message = message.strip()

    if _IRIDIUM_START_RE.search(message):
        return {"iridium_event_kind": "session_start"}

    connect_match = _IRIDIUM_CONNECT_RE.search(message)
    if connect_match is not None:
        return {
            "iridium_event_kind": "connection",
            "connection_duration_s": int(connect_match.group("connection_duration_s")),
            "signal_quality": int(connect_match.group("signal_quality")),
        }

    connection_failure_match = _IRIDIUM_CONNECT_FAILURE_RE.search(message)
    if connection_failure_match is not None:
        return {
            "iridium_event_kind": "connection_failure",
            "connect_attempt": int(connection_failure_match.group("connect_attempt")),
            "failure_code": int(connection_failure_match.group("failure_code")),
            "network": int(connection_failure_match.group("network")),
            "signal_quality": int(connection_failure_match.group("signal_quality")),
            "dial_attempt": int(connection_failure_match.group("dial_attempt")),
        }

    no_connection_match = _IRIDIUM_NO_CONNECTION_RE.search(message)
    if no_connection_match is not None:
        return {
            "iridium_event_kind": "no_connection",
            "connection_duration_s": int(
                no_connection_match.group("connection_duration_s")
            ),
        }

    command_match = _IRIDIUM_COMMAND_RE.search(message)
    if command_match is not None:
        command_name = command_match.group("command_name")
        if command_name.upper() in {"GPSACK", "GPSOFF"}:
            return {"iridium_event_kind": "session_line"}
        return {
            "iridium_event_kind": "command",
            "command_name": command_name,
            "command_payload": command_match.group("command_payload"),
        }

    command_summary_match = _IRIDIUM_COMMAND_SUMMARY_RE.search(message)
    if command_summary_match is not None:
        return {
            "iridium_event_kind": "command_summary",
            "received_command_count": int(
                command_summary_match.group("received_command_count")
            ),
        }

    if _IRIDIUM_REMOTE_COMMAND_END_RE.search(message):
        return {"iridium_event_kind": "remote_command_end"}

    uploaded_match = _UPLOADED_ARTIFACT_RE.search(message)
    if uploaded_match is not None:
        return {
            "iridium_event_kind": "upload_artifact",
            "referenced_artifact": _normalize_parsed_artifact_reference(
                uploaded_match.group("artifact")
            ),
            "rate_bytes_per_s": int(uploaded_match.group("rate")),
        }

    resume_match = _UPLOAD_RESUME_RE.search(message)
    if resume_match is not None:
        artifact_size_bytes = resume_match.group("artifact_size_bytes")
        return {
            "iridium_event_kind": "upload_resume",
            "referenced_artifact": _normalize_parsed_artifact_reference(
                resume_match.group("artifact")
            ),
            "byte_offset": int(resume_match.group("byte_offset")),
            "artifact_size_bytes": (
                int(artifact_size_bytes) if artifact_size_bytes is not None else None
            ),
        }

    progress_match = _UPLOAD_PROGRESS_ARTIFACT_RE.search(message)
    if progress_match is not None:
        return {
            "iridium_event_kind": "upload_progress_artifact",
            "referenced_artifact": _normalize_parsed_artifact_reference(
                progress_match.group("artifact")
            ),
            "byte_count": int(progress_match.group("byte_count")),
        }

    error_match = _UPLOAD_ERROR_ARTIFACT_RE.search(message)
    if error_match is not None:
        return {
            "iridium_event_kind": "upload_error_artifact",
            "referenced_artifact": _normalize_parsed_artifact_reference(
                error_match.group("artifact")
            ),
        }

    if _UPLOAD_RETRY_RE.search(message):
        return {
            "iridium_event_kind": "upload_retry",
        }

    if _UPLOAD_FAILED_RE.search(message):
        return {
            "iridium_event_kind": "upload_failed",
        }

    session_summary_match = _UPLOAD_SESSION_SUMMARY_RE.search(message)
    if session_summary_match is not None:
        return {
            "iridium_event_kind": "upload_session_summary",
            "uploaded_file_count": int(
                session_summary_match.group("uploaded_file_count")
            ),
        }

    disconnect_match = _UPLOAD_DISCONNECT_RE.search(message)
    if disconnect_match is not None:
        return {
            "iridium_event_kind": "disconnect",
            "disconnect_duration_s": int(
                disconnect_match.group("disconnect_duration_s")
            ),
        }

    if _UPLOAD_BATCH_RE.search(message):
        return {
            "iridium_event_kind": "upload_batch",
        }

    return {"iridium_event_kind": "session_line"}


def _classify_pressure_temperature(
    entry: OperationalLogEntry,
    *,
    instrument_id: str,
) -> dict[str, object] | None:
    common_fields = _common_log_record_fields(entry, instrument_id=instrument_id)

    match = _PRESS_TEMP_RE.search(entry.message)
    if match is not None:
        return {
            **common_fields,
            "pressure_mbar": int(match.group("pressure_mbar")),
            "temperature_mdegc": int(match.group("temperature_mdegc")),
            "raw_line": entry.raw_line,
        }

    match = _STANDALONE_PRESSURE_MBAR_RE.search(entry.message)
    if match is not None:
        return {
            **common_fields,
            "pressure_mbar": int(match.group("pressure_mbar")),
            "raw_line": entry.raw_line,
        }

    match = _DBAR_DEGC_RE.search(entry.message)
    if match is not None:
        return {
            **common_fields,
            "pressure_dbar": int(match.group("pressure_dbar")),
            "temperature_degc": int(match.group("temperature_degc")),
            "raw_line": entry.raw_line,
        }

    for pressure_re in (_INTERNAL_PRESSURE_RE, _PINT_RE):
        match = pressure_re.search(entry.message)
        if match is not None:
            return {
                **common_fields,
                "internal_pressure_pa": int(match.group("internal_pressure_pa")),
                "raw_line": entry.raw_line,
            }

    match = _PEXT_RE.search(entry.message)
    if match is not None:
        return {
            **common_fields,
            "external_pressure_mbar": int(match.group("external_pressure_mbar")),
            "external_pressure_range_mbar": int(
                match.group("external_pressure_range_mbar")
            ),
            "raw_line": entry.raw_line,
        }

    return None


def _classify_battery(
    entry: OperationalLogEntry,
    *,
    instrument_id: str,
) -> dict[str, object] | None:
    match = _BATTERY_RE.search(entry.message)
    if match is not None:
        return {
            **_common_log_record_fields(entry, instrument_id=instrument_id),
            "battery_record_kind": "voltage_current",
            "voltage_mv": int(match.group("mv")),
            "current_ua": int(match.group("ua")),
            "minimum_voltage_mv": None,
            "raw_line": entry.raw_line,
        }

    match = _VBAT_RE.search(entry.message)
    if match is not None:
        return {
            **_common_log_record_fields(entry, instrument_id=instrument_id),
            "battery_record_kind": "vbat_summary",
            "voltage_mv": int(match.group("mv")),
            "current_ua": None,
            "minimum_voltage_mv": int(match.group("minimum_mv")),
            "raw_line": entry.raw_line,
        }

    return None


def _fallback_instrument_id(path: Path) -> str:
    for candidate in (path.parent.name, path.stem):
        parsed = maybe_parse_instrument_name(candidate)
        if parsed is not None:
            return parsed.instrument_id
    return path.stem.split("_", maxsplit=1)[0]


def _resolve_output_instrument_serial(
    paths: list[Path],
    *,
    instrument_serial: str | None,
) -> str:
    if instrument_serial is not None:
        return validate_instrument_serial(instrument_serial)
    if not paths:
        raise ValueError("instrument_serial is required when no LOG paths are supplied")
    return validate_instrument_serial(_fallback_instrument_serial(paths[0]))


def _fallback_instrument_serial(path: Path) -> str:
    for ancestor in (path.parent, *path.parents):
        parsed = maybe_parse_instrument_name(ancestor.name)
        if parsed is not None:
            return parsed.serial
    parsed = maybe_parse_instrument_name(path.stem)
    if parsed is not None:
        return parsed.serial
    return path.stem.split("_", maxsplit=1)[0]


def _parse_rollover_banner(
    *,
    path: Path,
    line: str,
    line_number: int,
) -> OperationalLogEntry | None:
    match = _TIMESTAMPED_LINE_RE.match(line)
    if match is None:
        return None
    content = match.group("content")
    banner_match = _ROLLOVER_BANNER_RE.match(content)
    if banner_match is None:
        return None
    return OperationalLogEntry(
        time=_parse_time_text(match.group("time")),
        subsystem="ROLLOVER",
        code=None,
        message=content,
        source_kind="log",
        raw_line=line,
        source_file=path,
        line_number=line_number,
    )


def _rollover_fields(entry: OperationalLogEntry) -> dict[str, object]:
    banner_match = _ROLLOVER_BANNER_RE.match(entry.message)
    if banner_match is None:
        return {}
    return {
        "switched_to_log_file": _normalize_parsed_artifact_reference(
            banner_match.group("target"),
            default_suffix=".LOG",
        )
    }


def _normalize_parsed_artifact_reference(
    reference: str,
    *,
    default_suffix: str | None = None,
) -> str:
    normalized = reference.replace("/", "_")
    if default_suffix is not None and "." not in Path(normalized).name:
        normalized = f"{normalized}{default_suffix}"
    return normalized


def _log_epoch_time(entry: OperationalLogEntry) -> str:
    return entry.raw_line.split(":", maxsplit=1)[0]


def _write_jsonl_line(handle, record: dict[str, object]) -> None:
    handle.write(json.dumps(record, allow_nan=False))
    handle.write("\n")


def _iso_now() -> str:
    return format_utc_datetime(datetime.now(timezone.utc))
