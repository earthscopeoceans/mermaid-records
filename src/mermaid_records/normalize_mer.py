# SPDX-License-Identifier: MIT

"""MER-to-JSONL normalization helpers for provenance-preserving record-family outputs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import base64
import json
from pathlib import Path
import re
from typing import Iterable

from .format_datetime import format_source_datetime, format_utc_datetime
from .format_record_filenames import (
    record_filenames,
    validate_instrument_serial,
    with_record_metadata,
)
from .parse_mer import parse_mer_file, parse_mer_file_recoverable
from .parse_instrument_name import maybe_parse_instrument_name

BASE_OUTPUT_FILENAMES = {
    "environment": "mer_environment_records.jsonl",
    "parameter": "mer_parameter_records.jsonl",
    "event": "mer_event_records.jsonl",
}
OUTPUT_FILENAMES = BASE_OUTPUT_FILENAMES

_ATTR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_\[\]]*)=([^\s>]+)")
_BARE_TAG_RE = re.compile(r"^<(?P<tag>[A-Z0-9_]+)\s+(?P<value>[^>]+?)\s*/>$")

_ENVIRONMENT_KIND_MAP = {
    "BOARD": "board",
    "SOFTWARE": "software",
    "DIVE": "dive",
    "POOL": "pool",
    "GPSINFO": "gpsinfo",
    "DRIFT": "drift",
    "CLOCK": "clock",
    "SAMPLE": "sample",
    "TRUE_SAMPLE_FREQ": "true_sample_freq",
}

_PARAMETER_KIND_MAP = {
    "ADC": "adc",
    "INPUT_FILTER": "input_filter",
    "STALTA": "stalta",
    "EVENT_LEN": "event_len",
    "RATING": "rating",
    "CDF24": "cdf24",
    "MODEL": "model",
    "ASCEND_THRESH": "ascend_thresh",
    "STANFORD_PROCESS": "stanford_process",
    "MISC": "misc",
}

_INFO_FIELDS = [
    "DATE",
    "ROUNDS",
    "PRESSURE",
    "TEMPERATURE",
    "CRITERION",
    "SNR",
    "TRIG",
    "DETRIG",
    "FNAME",
    "SMP_OFFSET",
    "TRUE_FS",
]

_FORMAT_FIELDS = [
    "ENDIANNESS",
    "BYTES_PER_SAMPLE",
    "SAMPLING_RATE",
    "STAGES",
    "NORMALIZED",
    "LENGTH",
]


def _classify_mer_tag_kind(
    tag_name: str,
    *,
    stage_name: str,
) -> str:
    if stage_name == "environment":
        primary_map = _ENVIRONMENT_KIND_MAP
        forbidden_map = _PARAMETER_KIND_MAP
    elif stage_name == "parameter":
        primary_map = _PARAMETER_KIND_MAP
        forbidden_map = _ENVIRONMENT_KIND_MAP
    else:
        raise ValueError(f"Unsupported MER stage: {stage_name}")

    in_primary = tag_name in primary_map
    in_forbidden = tag_name in forbidden_map
    if in_primary and in_forbidden:
        raise ValueError(
            "MER derived-family multi-match: "
            f"tag {tag_name!r} is registered in both {stage_name} and the opposite stage"
        )
    if in_primary:
        return primary_map[tag_name]
    return "unknown"


@dataclass(slots=True)
class MerJsonlSummary:
    """Summary of generated MER-derived JSONL streams."""

    environment_records: int
    parameter_records: int
    event_records: int
    environment_kind_counts: dict[str, int]
    parameter_kind_counts: dict[str, int]
    total_mer_files: int
    zero_event_files: int
    total_event_blocks: int
    unknown_environment_tags: list[str]
    unknown_parameter_tags: list[str]
    unknown_info_keys: list[str]
    unknown_format_keys: list[str]
    example_gpsinfo_environment: dict[str, object] | None
    example_drift_environment: dict[str, object] | None
    example_adc_parameter: dict[str, object] | None
    example_model_parameter: dict[str, object] | None
    example_event_with_fname: dict[str, object] | None
    example_event_with_trigger_fields: dict[str, object] | None


def output_filenames(instrument_serial: str) -> dict[str, str]:
    """Return MER-derived output filenames for one instrument serial."""

    return record_filenames(BASE_OUTPUT_FILENAMES, instrument_serial)


def _common_mer_record_fields(instrument_id: str, path: Path) -> dict[str, object]:
    """Return shared provenance fields for MER-derived records."""

    return {
        "instrument_id": instrument_id,
        "source_file": path.name,
        "source_container": "mer",
    }


def write_mer_jsonl_families(
    mer_paths: Iterable[Path],
    output_dir: Path,
    *,
    instrument_id: str | None = None,
    instrument_serial: str | None = None,
    run_id: str | None = None,
    malformed_mer_blocks: list[dict[str, object]] | None = None,
    skipped_mer_files: list[dict[str, object]] | None = None,
) -> MerJsonlSummary:
    """Write conservative MER-derived JSONL streams."""

    output_dir.mkdir(parents=True, exist_ok=True)
    sorted_paths = sorted(Path(path) for path in mer_paths)
    output_instrument_serial = _resolve_output_instrument_serial(
        sorted_paths,
        instrument_serial=instrument_serial,
    )
    output_paths = {
        name: output_dir / filename
        for name, filename in output_filenames(output_instrument_serial).items()
    }

    environment_count = 0
    parameter_count = 0
    event_count = 0
    environment_kind_counter: Counter[str] = Counter()
    parameter_kind_counter: Counter[str] = Counter()
    total_mer_files = 0
    zero_event_files = 0
    unknown_environment_tags: set[str] = set()
    unknown_parameter_tags: set[str] = set()
    unknown_info_keys: set[str] = set()
    unknown_format_keys: set[str] = set()
    example_gpsinfo_environment: dict[str, object] | None = None
    example_drift_environment: dict[str, object] | None = None
    example_adc_parameter: dict[str, object] | None = None
    example_model_parameter: dict[str, object] | None = None
    example_event_with_fname: dict[str, object] | None = None
    example_event_with_trigger_fields: dict[str, object] | None = None

    with (
        output_paths["environment"].open("w", encoding="utf-8") as environment_handle,
        output_paths["parameter"].open("w", encoding="utf-8") as parameter_handle,
        output_paths["event"].open("w", encoding="utf-8") as event_handle,
    ):
        for path in sorted_paths:
            try:
                total_mer_files += 1
                path_instrument_id = instrument_id or _fallback_instrument_id(path)
                path_instrument_serial = output_instrument_serial

                def _record_malformed_block(
                    block_index: int | None,
                    block_kind: str,
                    raw_block: str,
                    error: str,
                ) -> None:
                    if malformed_mer_blocks is None or run_id is None:
                        return
                    malformed_mer_blocks.append(
                        {
                            "run_id": run_id,
                            "instrument_id": path_instrument_id,
                            "instrument_serial": path_instrument_serial,
                            "source_file": path.as_posix(),
                            "block_index": block_index,
                            "block_kind": block_kind,
                            "raw_block": raw_block,
                            "error": error,
                        }
                    )
                if malformed_mer_blocks is not None and run_id is not None:
                    metadata, blocks = parse_mer_file_recoverable(
                        path,
                        on_malformed_block=_record_malformed_block,
                    )
                else:
                    metadata, blocks = parse_mer_file(path)
                if not blocks:
                    zero_event_files += 1

                stanford_psd_processing = _stanford_psd_processing(metadata)

                file_unknown_info_keys: set[str] = set()
                file_unknown_format_keys: set[str] = set()

                for line in metadata.raw_environment_lines:
                    record, tag_name = _build_environment_record(
                        instrument_id=path_instrument_id,
                        path=path,
                        line=line,
                    )
                    record = with_record_metadata(record, path_instrument_serial)
                    _write_jsonl_line(environment_handle, record)
                    environment_count += 1
                    environment_kind_counter[record["environment_kind"]] += 1
                    if record["environment_kind"] == "unknown":
                        unknown_environment_tags.add(tag_name)
                    if (
                        example_gpsinfo_environment is None
                        and record["environment_kind"] == "gpsinfo"
                    ):
                        example_gpsinfo_environment = record
                    if (
                        example_drift_environment is None
                        and record["environment_kind"] == "drift"
                    ):
                        example_drift_environment = record

                for line in metadata.raw_parameter_lines:
                    record, tag_name = _build_parameter_record(
                        instrument_id=path_instrument_id,
                        path=path,
                        line=line,
                    )
                    record = with_record_metadata(record, path_instrument_serial)
                    _write_jsonl_line(parameter_handle, record)
                    parameter_count += 1
                    parameter_kind_counter[record["parameter_kind"]] += 1
                    if record["parameter_kind"] == "unknown":
                        unknown_parameter_tags.add(tag_name)
                    if example_adc_parameter is None and record["parameter_kind"] == "adc":
                        example_adc_parameter = record
                    if (
                        example_model_parameter is None
                        and record["parameter_kind"] == "model"
                    ):
                        example_model_parameter = record

                for block_index, block in enumerate(blocks):
                    record, block_unknown_info_keys, block_unknown_format_keys = (
                        _build_event_record(
                            instrument_id=path_instrument_id,
                            path=path,
                            block_index=block_index,
                            raw_info_line=block.raw_info_line,
                            raw_format_line=block.raw_format_line,
                            data_payload=block.data_payload,
                            stanford_psd_processing=stanford_psd_processing,
                        )
                    )
                    record = with_record_metadata(record, path_instrument_serial)
                    file_unknown_info_keys.update(block_unknown_info_keys)
                    file_unknown_format_keys.update(block_unknown_format_keys)
                    _write_jsonl_line(event_handle, record)
                    event_count += 1
                    if example_event_with_fname is None and record["fname"] is not None:
                        example_event_with_fname = record
                    if (
                        example_event_with_trigger_fields is None
                        and record["pressure"] is not None
                    ):
                        example_event_with_trigger_fields = record

                if file_unknown_info_keys or file_unknown_format_keys:
                    unknown_info_keys.update(file_unknown_info_keys)
                    unknown_format_keys.update(file_unknown_format_keys)
                    details: list[str] = []
                    if file_unknown_info_keys:
                        details.append("INFO keys: " + ", ".join(sorted(file_unknown_info_keys)))
                    if file_unknown_format_keys:
                        details.append("FORMAT keys: " + ", ".join(sorted(file_unknown_format_keys)))
                    raise ValueError("Unhandled MER event fields observed: " + "; ".join(details))
            except OSError as exc:
                if skipped_mer_files is None or run_id is None:
                    raise
                skipped_mer_files.append(
                    {
                        "run_id": run_id,
                        "instrument_id": path_instrument_id,
                        "instrument_serial": path_instrument_serial,
                        "source_file": path.as_posix(),
                        "error": str(exc),
                        "skipped_at": _iso_now(),
                    }
                )
                continue
            except Exception as exc:
                raise ValueError(f"Error while normalizing MER file {path}: {exc}") from exc

    return MerJsonlSummary(
        environment_records=environment_count,
        parameter_records=parameter_count,
        event_records=event_count,
        environment_kind_counts=dict(environment_kind_counter),
        parameter_kind_counts=dict(parameter_kind_counter),
        total_mer_files=total_mer_files,
        zero_event_files=zero_event_files,
        total_event_blocks=event_count,
        unknown_environment_tags=sorted(unknown_environment_tags),
        unknown_parameter_tags=sorted(unknown_parameter_tags),
        unknown_info_keys=sorted(unknown_info_keys),
        unknown_format_keys=sorted(unknown_format_keys),
        example_gpsinfo_environment=example_gpsinfo_environment,
        example_drift_environment=example_drift_environment,
        example_adc_parameter=example_adc_parameter,
        example_model_parameter=example_model_parameter,
        example_event_with_fname=example_event_with_fname,
        example_event_with_trigger_fields=example_event_with_trigger_fields,
    )


def _build_environment_record(
    *,
    instrument_id: str,
    path: Path,
    line: str,
) -> tuple[dict[str, object], str]:
    stripped_line = line.strip()
    tag_name = _tag_name(stripped_line)
    environment_kind = _classify_mer_tag_kind(tag_name, stage_name="environment")
    attrs = _parse_attributes(stripped_line)
    raw_values = _environment_raw_values(tag_name, stripped_line, attrs)
    return (
        {
            **_common_mer_record_fields(instrument_id, path),
            "environment_kind": environment_kind,
            "board": _parse_bare_tag_value(stripped_line, "BOARD") if tag_name == "BOARD" else None,
            "software": _parse_bare_tag_value(stripped_line, "SOFTWARE") if tag_name == "SOFTWARE" else None,
            "dive_id": _attr_int(attrs, "ID") if tag_name == "DIVE" else None,
            "dive_declared_event_count": (
                _attr_int(attrs, "EVENTS") if tag_name == "DIVE" else None
            ),
            "pool_declared_event_count": (
                _attr_int(attrs, "EVENTS") if tag_name == "POOL" else None
            ),
            "pool_declared_size_bytes": (
                _attr_int(attrs, "SIZE") if tag_name == "POOL" else None
            ),
            "sample_min": _attr_int(attrs, "MIN") if tag_name == "SAMPLE" else None,
            "sample_max": _attr_int(attrs, "MAX") if tag_name == "SAMPLE" else None,
            "true_sample_freq_hz": (
                _attr_float(attrs, "FS_Hz") if tag_name == "TRUE_SAMPLE_FREQ" else None
            ),
            "gpsinfo_date": (
                format_source_datetime(attrs.get("DATE")) if tag_name == "GPSINFO" else None
            ),
            "raw_values": raw_values,
            "line": line,
        },
        tag_name,
    )


def _build_parameter_record(
    *,
    instrument_id: str,
    path: Path,
    line: str,
) -> tuple[dict[str, object], str]:
    stripped_line = line.strip()
    tag_name = _tag_name(stripped_line)
    parameter_kind = _classify_mer_tag_kind(tag_name, stage_name="parameter")
    attrs = _parse_attributes(stripped_line)
    raw_values = {key.lower(): value for key, value in attrs.items()} or None
    return (
        {
            **_common_mer_record_fields(instrument_id, path),
            "parameter_kind": parameter_kind,
            "adc_gain": _attr_int(attrs, "GAIN") if tag_name == "ADC" else None,
            "adc_buffer": attrs.get("BUFFER") if tag_name == "ADC" else None,
            "stanford_process_duration_h": (
                _attr_int(attrs, "DURATION_h", "DURATION_H")
                if tag_name == "STANFORD_PROCESS"
                else None
            ),
            "stanford_process_period_h": (
                _attr_int(attrs, "PROCESS_PERIOD_h", "PROCESS_PERIOD_H")
                if tag_name == "STANFORD_PROCESS"
                else None
            ),
            "stanford_process_window_len": (
                _attr_int(attrs, "WINDOW_LEN") if tag_name == "STANFORD_PROCESS" else None
            ),
            "stanford_process_window_type": (
                attrs.get("WINDOW_TYPE") if tag_name == "STANFORD_PROCESS" else None
            ),
            "stanford_process_overlap_percent": (
                _attr_int(attrs, "OVERLAP_PERCENT")
                if tag_name == "STANFORD_PROCESS"
                else None
            ),
            "stanford_process_db_offset": (
                _attr_float(attrs, "dB_OFFSET", "DB_OFFSET")
                if tag_name == "STANFORD_PROCESS"
                else None
            ),
            "upload_max": attrs.get("UPLOAD_MAX") if tag_name == "MISC" else None,
            "raw_values": raw_values,
            "line": line,
        },
        tag_name,
    )


def _build_event_record(
    *,
    instrument_id: str,
    path: Path,
    block_index: int,
    raw_info_line: str | None,
    raw_format_line: str | None,
    data_payload: bytes | None,
    stanford_psd_processing: dict[str, object] | None,
) -> tuple[dict[str, object], set[str], set[str]]:
    info_attrs = _parse_attributes(raw_info_line)
    format_attrs = _parse_attributes(raw_format_line)
    unknown_info_keys = set(info_attrs) - set(_INFO_FIELDS)
    unknown_format_keys = set(format_attrs) - set(_FORMAT_FIELDS)
    actual_payload_nbytes = len(data_payload) if data_payload is not None else 0
    expected_payload_nbytes = _expected_payload_nbytes(format_attrs)
    if expected_payload_nbytes is None:
        payload_length_matches_expected = None
    else:
        payload_length_matches_expected = actual_payload_nbytes == expected_payload_nbytes

    record = {
        **_common_mer_record_fields(instrument_id, path),
        "block_index": block_index,
        "event_index": block_index,
        "event_info_date": format_source_datetime(info_attrs.get("DATE")),
        "event_rounds": info_attrs.get("ROUNDS"),
        "date": format_source_datetime(info_attrs.get("DATE")),
        "rounds": info_attrs.get("ROUNDS"),
        "pressure": info_attrs.get("PRESSURE"),
        "temperature": info_attrs.get("TEMPERATURE"),
        "criterion": info_attrs.get("CRITERION"),
        "snr": info_attrs.get("SNR"),
        "trig": info_attrs.get("TRIG"),
        "detrig": info_attrs.get("DETRIG"),
        "fname": info_attrs.get("FNAME"),
        "smp_offset": info_attrs.get("SMP_OFFSET"),
        "true_fs": info_attrs.get("TRUE_FS"),
        "endianness": format_attrs.get("ENDIANNESS"),
        "bytes_per_sample": format_attrs.get("BYTES_PER_SAMPLE"),
        "sampling_rate": format_attrs.get("SAMPLING_RATE"),
        "stages": format_attrs.get("STAGES"),
        "normalized": format_attrs.get("NORMALIZED"),
        "length": format_attrs.get("LENGTH"),
        "encoded_payload": (
            base64.b64encode(data_payload).decode("ascii")
            if data_payload is not None
            else None
        ),
        "encoded_payload_byte_count": actual_payload_nbytes,
        "data_payload_nbytes": actual_payload_nbytes,
        "expected_payload_nbytes": expected_payload_nbytes,
        "payload_length_matches_expected": payload_length_matches_expected,
        "raw_info_line": raw_info_line,
        "raw_format_line": raw_format_line,
    }
    if stanford_psd_processing is not None:
        record["stanford_psd_processing"] = stanford_psd_processing
    return record, unknown_info_keys, unknown_format_keys


def _stanford_psd_processing(metadata) -> dict[str, object] | None:
    """Return the sole file-level Stanford PSD parameter set, if unambiguous."""

    if metadata.software is None or "STANFORD" not in metadata.software.upper():
        return None
    process_lines = [
        line for line in metadata.raw_parameter_lines
        if line.strip().startswith("<STANFORD_PROCESS ")
    ]
    if len(process_lines) != 1:
        return None
    return {
        "duration_h": metadata.stanford_process_duration_h,
        "process_period_h": metadata.stanford_process_period_h,
        "window_len": metadata.stanford_process_window_len,
        "window_type": metadata.stanford_process_window_type,
        "overlap_percent": metadata.stanford_process_overlap_percent,
        "db_offset": metadata.stanford_process_db_offset,
        "raw_parameter_line": process_lines[0],
    }


def _expected_payload_nbytes(format_attrs: dict[str, str]) -> int | None:
    length = format_attrs.get("LENGTH")
    bytes_per_sample = format_attrs.get("BYTES_PER_SAMPLE")
    if length is None or bytes_per_sample is None:
        return None
    return int(length) * int(bytes_per_sample)


def _environment_raw_values(
    tag_name: str,
    line: str,
    attrs: dict[str, str],
) -> dict[str, str] | None:
    if tag_name == "BOARD":
        value = _parse_bare_tag_value(line, tag_name)
        return {"board": value} if value is not None else None
    if tag_name == "SOFTWARE":
        value = _parse_bare_tag_value(line, tag_name)
        return {"software": value} if value is not None else None
    if attrs:
        return {key.lower(): value for key, value in attrs.items()}
    value = _parse_bare_tag_value(line, tag_name)
    if value is None:
        return None
    return {tag_name.lower(): value}


def _tag_name(line: str) -> str:
    match = re.match(r"^<(?P<tag>[A-Z0-9_]+)\b", line)
    if match is None:
        return "UNKNOWN"
    return match.group("tag")


def _parse_attributes(line: str | None) -> dict[str, str]:
    if line is None:
        return {}
    return {key: value for key, value in _ATTR_RE.findall(line)}


def _attr_int(attrs: dict[str, str], *keys: str) -> int | None:
    for key in keys:
        value = attrs.get(key)
        if value is not None:
            return int(value)
    return None


def _attr_float(attrs: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        value = attrs.get(key)
        if value is not None:
            return float(value)
    return None


def _parse_bare_tag_value(line: str, tag_name: str) -> str | None:
    match = _BARE_TAG_RE.match(line)
    if match is None or match.group("tag") != tag_name:
        return None
    return match.group("value")


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
        raise ValueError("instrument_serial is required when no MER paths are supplied")
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


def _write_jsonl_line(handle, record: dict[str, object]) -> None:
    handle.write(json.dumps(record))
    handle.write("\n")


def _iso_now() -> str:
    from datetime import datetime, timezone

    return format_utc_datetime(datetime.now(timezone.utc))
