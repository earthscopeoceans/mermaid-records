# SPDX-License-Identifier: MIT

"""Format normalized record-family filenames."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from . import __version__


CANONICAL_BASE_FILENAMES = frozenset(
    {
        "log_acquisition_records.jsonl",
        "log_ascent_request_records.jsonl",
        "log_gps_records.jsonl",
        "log_pressure_temperature_records.jsonl",
        "log_battery_records.jsonl",
        "log_parameter_records.jsonl",
        "log_testmode_records.jsonl",
        "log_ctd_records.jsonl",
        "log_iridium_records.jsonl",
        "log_unclassified_records.jsonl",
        "mer_environment_records.jsonl",
        "mer_parameter_records.jsonl",
        "mer_event_records.jsonl",
    }
)


def validate_instrument_serial(instrument_serial: str) -> str:
    """Return a normalized non-empty serial suitable for JSONL filenames."""

    value = instrument_serial.strip()
    if not value:
        raise ValueError("instrument_serial must be non-empty")
    if "/" in value or "\\" in value:
        raise ValueError(f"instrument_serial must not contain path separators: {instrument_serial!r}")
    return value


def record_filename(base_filename: str, instrument_serial: str) -> str:
    """Return the serial-suffixed JSONL filename for one base family filename."""

    serial = validate_instrument_serial(instrument_serial)
    if not base_filename.endswith(".jsonl"):
        raise ValueError(f"Normalized record filename must end with .jsonl: {base_filename}")
    return f"{base_filename.removesuffix('.jsonl')}.{serial}.jsonl"


def record_filenames(
    base_filenames: Mapping[str, str],
    instrument_serial: str,
) -> dict[str, str]:
    """Return serial-suffixed JSONL filenames keyed by family name."""

    return {
        family: record_filename(base_filename, instrument_serial)
        for family, base_filename in base_filenames.items()
    }


def record_family_name(path: Path) -> str:
    """Return the family name portion of a normalized JSONL filename."""

    return path.name.removesuffix(".jsonl").split(".", maxsplit=1)[0]


def is_canonical_record_filename(filename: str) -> bool:
    """Return whether ``filename`` is one serial-suffixed record-family output."""

    for base_filename in CANONICAL_BASE_FILENAMES:
        stem = base_filename.removesuffix(".jsonl")
        if filename.startswith(f"{stem}.") and filename.endswith(".jsonl"):
            serial = filename[len(stem) + 1 : -len(".jsonl")]
            if serial:
                return True
    return False


def with_record_metadata(
    record: dict[str, object],
    instrument_serial: str,
) -> dict[str, object]:
    """Return a record with canonical package and instrument metadata."""

    serial = validate_instrument_serial(instrument_serial)
    if "instrument_id" not in record:
        return {
            "instrument_serial": serial,
            "mermaid_records_version": __version__,
            **record,
        }
    return {
        "instrument_id": record["instrument_id"],
        "instrument_serial": serial,
        "mermaid_records_version": __version__,
        **{
            key: value
            for key, value in record.items()
            if key
            not in {
                "instrument_id",
                "instrument_serial",
                "mermaid_records_version",
            }
        },
    }
