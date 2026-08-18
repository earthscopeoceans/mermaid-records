# SPDX-License-Identifier: MIT

"""Low-level interfaces for parsing raw MER files."""

from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
from typing import Callable, Iterator

from .format_datetime import parse_source_datetime
from .models import MerEventBlock, MerFileMetadata

_SECTION_RE = re.compile(
    rb"<(?P<name>ENVIRONMENT|PARAMETERS)>(?P<body>.*?)</(?P=name)>",
    re.DOTALL,
)
_INFO_RE = re.compile(rb"<INFO (?P<body>[^>]+)/>")
_FORMAT_RE = re.compile(rb"<FORMAT (?P<body>[^>]+)/>")
_ATTR_RE = re.compile(r'([A-Za-z_]+)=([^\s>]+)')

_DATA_OPEN_TAG = b"<DATA>"
_DATA_CLOSE_TAG = b"</DATA>"
_DATA_LEADING_FRAME = b"\n\r"
_DATA_TRAILING_FRAME = b"\n\r\t"

type MalformedMerBlockCallback = Callable[[int | None, str, str, str], None]


def parse_mer_file(path: Path) -> tuple[MerFileMetadata, list[MerEventBlock]]:
    """Parse a .MER file into conservative file metadata and raw event blocks."""

    data = path.read_bytes()
    metadata = _parse_metadata(path, data)
    blocks = list(iter_mer_event_blocks(path, data))
    return metadata, blocks


def parse_mer_file_recoverable(
    path: Path,
    *,
    on_malformed_block: MalformedMerBlockCallback | None = None,
) -> tuple[MerFileMetadata, list[MerEventBlock]]:
    """Parse a .MER file with recoverable malformed-structure callbacks."""

    data = path.read_bytes()
    metadata = _parse_metadata_recoverable(
        path,
        data,
        on_malformed_block=on_malformed_block,
    )
    blocks = list(
        iter_mer_event_blocks_recoverable(
            path,
            data,
            on_malformed_block=on_malformed_block,
        )
    )
    if (
        data.strip()
        and not metadata.raw_environment_lines
        and not metadata.raw_parameter_lines
        and not blocks
    ):
        raise OSError(
            "MER structure unreadable: no recoverable ENVIRONMENT, PARAMETERS, or EVENT content"
        )
    return metadata, blocks


def iter_mer_event_blocks(path: Path, data: bytes | None = None) -> Iterator[MerEventBlock]:
    """Yield conservative MER event blocks without interpreting waveform payloads."""

    raw_data = data if data is not None else path.read_bytes()
    for event_body in _iter_event_bodies(raw_data):
        info_line = _extract_tag_line(event_body, _INFO_RE)
        format_line = _extract_tag_line(event_body, _FORMAT_RE)
        payload = _extract_payload(event_body)

        info_attrs = _parse_attributes(info_line)
        format_attrs = _parse_attributes(format_line)

        yield MerEventBlock(
            date=_parse_datetime(info_attrs.get("DATE")),
            pressure_mbar=_parse_float(info_attrs.get("PRESSURE")),
            temperature_c=_parse_float(info_attrs.get("TEMPERATURE")),
            criterion=_parse_float(info_attrs.get("CRITERION")),
            snr=_parse_float(info_attrs.get("SNR")),
            trig=_parse_int(info_attrs.get("TRIG")),
            detrig=_parse_int(info_attrs.get("DETRIG")),
            endianness=format_attrs.get("ENDIANNESS"),
            bytes_per_sample=_parse_int(format_attrs.get("BYTES_PER_SAMPLE")),
            sampling_rate_hz=_parse_float(format_attrs.get("SAMPLING_RATE")),
            stages=_parse_int(format_attrs.get("STAGES")),
            normalized=_parse_bool(format_attrs.get("NORMALIZED")),
            length_samples=_parse_int(format_attrs.get("LENGTH")),
            raw_info_line=info_line,
            raw_format_line=format_line,
            data_payload=payload,
            source_file=path,
        )


def iter_mer_event_blocks_recoverable(
    path: Path,
    data: bytes | None = None,
    *,
    on_malformed_block: MalformedMerBlockCallback | None = None,
) -> Iterator[MerEventBlock]:
    """Yield conservative MER event blocks while reporting malformed structure."""

    raw_data = data if data is not None else path.read_bytes()
    event_starts = [
        match.start() for match in re.finditer(re.escape(b"<EVENT>"), raw_data)
    ]
    event_bodies: list[bytes] = []
    for block_index, event_start in enumerate(event_starts):
        next_start = (
            event_starts[block_index + 1]
            if block_index + 1 < len(event_starts)
            else len(raw_data)
        )
        fragment = raw_data[event_start:next_start]
        try:
            event_bodies.extend(_iter_event_bodies(fragment))
        except ValueError as exc:
            _report_malformed_block(
                on_malformed_block,
                block_index=block_index,
                block_kind="event_data",
                raw_block=fragment.decode("ascii", "backslashreplace"),
                error=str(exc),
            )

    for block_index, event_body in enumerate(event_bodies):
        raw_block = event_body.decode("ascii", "backslashreplace")
        info_line = _extract_tag_line(event_body, _INFO_RE)
        format_line = _extract_tag_line(event_body, _FORMAT_RE)
        try:
            payload = _extract_payload(event_body)
        except ValueError as exc:
            _report_malformed_block(
                on_malformed_block,
                block_index=block_index,
                block_kind="event_data",
                raw_block=raw_block,
                error=str(exc),
            )
            continue

        if info_line is None:
            _report_malformed_block(
                on_malformed_block,
                block_index=block_index,
                block_kind="event_info",
                raw_block=raw_block,
                error="missing INFO tag",
            )
            continue
        if payload is None:
            _report_malformed_block(
                on_malformed_block,
                block_index=block_index,
                block_kind="event_data",
                raw_block=raw_block,
                error="missing DATA tag",
            )
            continue

        info_attrs = _parse_attributes(info_line)
        format_attrs = _parse_attributes(format_line)
        try:
            date = _parse_datetime(info_attrs.get("DATE"))
            pressure_mbar = _parse_float(info_attrs.get("PRESSURE"))
            temperature_c = _parse_float(info_attrs.get("TEMPERATURE"))
            criterion = _parse_float(info_attrs.get("CRITERION"))
            snr = _parse_float(info_attrs.get("SNR"))
            trig = _parse_int(info_attrs.get("TRIG"))
            detrig = _parse_int(info_attrs.get("DETRIG"))
        except Exception as exc:
            _report_malformed_block(
                on_malformed_block,
                block_index=block_index,
                block_kind="event_info",
                raw_block=info_line,
                error=str(exc),
            )
            continue
        try:
            bytes_per_sample = _parse_int(format_attrs.get("BYTES_PER_SAMPLE"))
            sampling_rate_hz = _parse_float(format_attrs.get("SAMPLING_RATE"))
            stages = _parse_int(format_attrs.get("STAGES"))
            normalized = _parse_bool(format_attrs.get("NORMALIZED"))
            length_samples = _parse_int(format_attrs.get("LENGTH"))
        except Exception as exc:
            _report_malformed_block(
                on_malformed_block,
                block_index=block_index,
                block_kind="event_format",
                raw_block=format_line,
                error=str(exc),
            )
            continue

        yield MerEventBlock(
            date=date,
            pressure_mbar=pressure_mbar,
            temperature_c=temperature_c,
            criterion=criterion,
            snr=snr,
            trig=trig,
            detrig=detrig,
            endianness=format_attrs.get("ENDIANNESS"),
            bytes_per_sample=bytes_per_sample,
            sampling_rate_hz=sampling_rate_hz,
            stages=stages,
            normalized=normalized,
            length_samples=length_samples,
            raw_info_line=info_line,
            raw_format_line=format_line,
            data_payload=payload,
            source_file=path,
        )

def _parse_metadata(path: Path, data: bytes) -> MerFileMetadata:
    """Parse file-level metadata from ENVIRONMENT and PARAMETERS sections."""

    environment_lines = _split_tag_lines(
        b"\n".join(_extract_sections(data, b"ENVIRONMENT"))
    )
    parameter_lines = _split_tag_lines(
        b"\n".join(_extract_sections(data, b"PARAMETERS"))
    )

    metadata = MerFileMetadata(
        board=None,
        software_version=None,
        dive_id=None,
        dive_event_count=None,
        pool_event_count=None,
        pool_size_bytes=None,
        raw_environment_lines=environment_lines,
        raw_parameter_lines=parameter_lines,
        source_file=path,
    )

    for line in environment_lines:
        _apply_environment_metadata_line(metadata, line.strip())

    for line in parameter_lines:
        _apply_parameter_metadata_line(metadata, line.strip())

    return metadata


def _parse_metadata_recoverable(
    path: Path,
    data: bytes,
    *,
    on_malformed_block: MalformedMerBlockCallback | None = None,
) -> MerFileMetadata:
    """Parse file-level metadata while skipping malformed section lines."""

    environment_sections = _extract_sections(data, b"ENVIRONMENT")
    parameter_sections = _extract_sections(data, b"PARAMETERS")
    environment_lines = _split_tag_lines(b"\n".join(environment_sections))
    parameter_lines = _split_tag_lines(b"\n".join(parameter_sections))

    for section_name, sections in (
        ("ENVIRONMENT", environment_sections),
        ("PARAMETERS", parameter_sections),
    ):
        for section_index, section in enumerate(sections[1:], start=1):
            _report_malformed_block(
                on_malformed_block,
                block_index=section_index,
                block_kind="repeated_metadata_section",
                raw_block=section.decode("ascii", "backslashreplace"),
                error=(
                    f"repeated complete {section_name} section preserved in normalized records"
                ),
            )

    metadata = MerFileMetadata(
        board=None,
        software_version=None,
        dive_id=None,
        dive_event_count=None,
        pool_event_count=None,
        pool_size_bytes=None,
        raw_environment_lines=[],
        raw_parameter_lines=[],
        source_file=path,
    )

    for line in environment_lines:
        stripped_line = line.strip()
        if not _looks_like_tag_line(stripped_line):
            _report_malformed_block(
                on_malformed_block,
                block_index=None,
                block_kind="environment",
                raw_block=line,
                error="malformed ENVIRONMENT tag line",
            )
            continue
        try:
            _apply_environment_metadata_line(metadata, stripped_line)
            metadata.raw_environment_lines.append(line)
        except Exception as exc:
            _report_malformed_block(
                on_malformed_block,
                block_index=None,
                block_kind="environment",
                raw_block=line,
                error=str(exc),
            )

    for line in parameter_lines:
        stripped_line = line.strip()
        if not _looks_like_tag_line(stripped_line):
            _report_malformed_block(
                on_malformed_block,
                block_index=None,
                block_kind="parameter",
                raw_block=line,
                error="malformed PARAMETERS tag line",
            )
            continue
        try:
            _apply_parameter_metadata_line(metadata, stripped_line)
            metadata.raw_parameter_lines.append(line)
        except Exception as exc:
            _report_malformed_block(
                on_malformed_block,
                block_index=None,
                block_kind="parameter",
                raw_block=line,
                error=str(exc),
            )

    return metadata


def _apply_environment_metadata_line(metadata: MerFileMetadata, stripped_line: str) -> None:
    if stripped_line.startswith("<BOARD "):
        metadata.board = _parse_bare_tag_value(stripped_line, "BOARD")
    elif stripped_line.startswith("<SOFTWARE "):
        metadata.software_version = _parse_bare_tag_value(stripped_line, "SOFTWARE")
        metadata.software = metadata.software_version
    elif stripped_line.startswith("<DIVE "):
        attrs = _parse_attributes(stripped_line)
        metadata.dive_id = _parse_int(attrs.get("ID"))
        metadata.dive_event_count = _parse_int(attrs.get("EVENTS"))
        metadata.dive_declared_event_count = metadata.dive_event_count
    elif stripped_line.startswith("<POOL "):
        attrs = _parse_attributes(stripped_line)
        metadata.pool_event_count = _parse_int(attrs.get("EVENTS"))
        metadata.pool_size_bytes = _parse_int(attrs.get("SIZE"))
        metadata.pool_declared_event_count = metadata.pool_event_count
        metadata.pool_declared_size_bytes = metadata.pool_size_bytes
    elif stripped_line.startswith("<GPSINFO "):
        attrs = _parse_attributes(stripped_line)
        metadata.gps_fixes.append(
            {
                "date": attrs.get("DATE", ""),
                "lat": attrs.get("LAT", ""),
                "lon": attrs.get("LON", ""),
            }
        )
    elif stripped_line.startswith("<DRIFT "):
        attrs = _parse_attributes(stripped_line)
        metadata.drifts.append(
            {
                "sec": _parse_int(attrs.get("SEC")),
                "usec": _parse_int(attrs.get("USEC")),
            }
        )
    elif stripped_line.startswith("<CLOCK "):
        attrs = _parse_attributes(stripped_line)
        hz = _parse_int(attrs.get("Hz"))
        if hz is not None:
            metadata.clock_frequencies_hz.append(hz)
    elif stripped_line.startswith("<SAMPLE "):
        attrs = _parse_attributes(stripped_line)
        metadata.sample_min = _parse_int(attrs.get("MIN"))
        metadata.sample_max = _parse_int(attrs.get("MAX"))
    elif stripped_line.startswith("<TRUE_SAMPLE_FREQ "):
        attrs = _parse_attributes(stripped_line)
        metadata.true_sample_freq_hz = _parse_float(attrs.get("FS_Hz"))


def _apply_parameter_metadata_line(metadata: MerFileMetadata, stripped_line: str) -> None:
    if stripped_line.startswith("<ADC "):
        attrs = _parse_attributes(stripped_line)
        metadata.adc_gain = _parse_int(attrs.get("GAIN"))
        metadata.adc_buffer = attrs.get("BUFFER")
    elif stripped_line.startswith("<STANFORD_PROCESS "):
        attrs = _parse_attributes(stripped_line)
        metadata.stanford_process_duration_h = _parse_int(
            attrs.get("DURATION_h") or attrs.get("DURATION_H")
        )
        metadata.stanford_process_period_h = _parse_int(
            attrs.get("PROCESS_PERIOD_h") or attrs.get("PROCESS_PERIOD_H")
        )
        metadata.stanford_process_window_len = _parse_int(attrs.get("WINDOW_LEN"))
        metadata.stanford_process_window_type = attrs.get("WINDOW_TYPE")
        metadata.stanford_process_overlap_percent = _parse_int(
            attrs.get("OVERLAP_PERCENT")
        )
        metadata.stanford_process_db_offset = _parse_float(
            attrs.get("dB_OFFSET") or attrs.get("DB_OFFSET")
        )
    elif stripped_line.startswith("<MISC "):
        attrs = _parse_attributes(stripped_line)
        metadata.upload_max = attrs.get("UPLOAD_MAX")


def _extract_sections(data: bytes, section_name: bytes) -> list[bytes]:
    """Extract all complete named top-level section bodies in source order."""

    return [
        match.group("body")
        for match in _SECTION_RE.finditer(data)
        if match.group("name") == section_name
    ]


def _split_tag_lines(section: bytes) -> list[str]:
    """Split a decoded section into non-empty raw tag lines."""

    text = section.decode("ascii", "ignore")
    return [line for line in text.splitlines() if line.strip()]


def _extract_tag_line(event_body: bytes, pattern: re.Pattern[bytes]) -> str | None:
    """Extract a single INFO or FORMAT tag line from an event body."""

    match = pattern.search(event_body)
    if match is None:
        return None
    return match.group(0).decode("ascii", "ignore")


def _extract_payload(event_body: bytes) -> bytes | None:
    """Extract the raw DATA payload bytes without delimiter framing."""

    start = event_body.find(_DATA_OPEN_TAG)
    if start == -1:
        return None
    payload_start = start + len(_DATA_OPEN_TAG)
    if event_body[payload_start : payload_start + len(_DATA_LEADING_FRAME)] == _DATA_LEADING_FRAME:
        payload_start += len(_DATA_LEADING_FRAME)

    end = event_body.rfind(_DATA_CLOSE_TAG, payload_start)
    if end == -1:
        raise ValueError("incomplete DATA block: missing </DATA>")

    payload_end = end
    if (
        payload_end >= len(_DATA_TRAILING_FRAME)
        and event_body[payload_end - len(_DATA_TRAILING_FRAME) : payload_end] == _DATA_TRAILING_FRAME
    ):
        payload_end -= len(_DATA_TRAILING_FRAME)
    return event_body[payload_start:payload_end]


def _iter_event_bodies(data: bytes) -> Iterator[bytes]:
    """Yield event bodies using DATA length framing when it is available.

    A waveform is arbitrary bytes, so delimiter-looking bytes within a FORMAT
    framed payload are data, not XML structure.  FORMAT-less events are only
    accepted when their DATA close tag is unique within the event.
    """

    cursor = 0
    while True:
        event_start = data.find(b"<EVENT>", cursor)
        if event_start == -1:
            return
        body_start = event_start + len(b"<EVENT>")
        data_start = data.find(_DATA_OPEN_TAG, body_start)
        if data_start == -1:
            raise ValueError("EVENT block missing DATA tag")

        prefix = data[body_start:data_start]
        format_line = _extract_tag_line(prefix, _FORMAT_RE)
        format_attrs = _parse_attributes(format_line)
        expected_nbytes = _expected_payload_nbytes(format_attrs)
        payload_start = data_start + len(_DATA_OPEN_TAG)
        if data[payload_start : payload_start + len(_DATA_LEADING_FRAME)] == _DATA_LEADING_FRAME:
            payload_start += len(_DATA_LEADING_FRAME)

        if expected_nbytes is None:
            close_tag_start, event_end = _find_event_data_close(data, payload_start)
            if data.find(_DATA_CLOSE_TAG, payload_start, close_tag_start) != -1:
                raise ValueError(
                    "ambiguous FORMAT-less DATA block: multiple </DATA> delimiters"
                )
        else:
            if expected_nbytes < 0:
                raise ValueError("negative expected DATA byte count")
            payload_end = payload_start + expected_nbytes
            trailing = len(_DATA_TRAILING_FRAME) if data[
                payload_end : payload_end + len(_DATA_TRAILING_FRAME)
            ] == _DATA_TRAILING_FRAME else 0
            close_tag_start = payload_end + trailing
            if data[close_tag_start : close_tag_start + len(_DATA_CLOSE_TAG)] != _DATA_CLOSE_TAG:
                # Keep malformed length-mismatch payloads intact; the record
                # exposes the mismatch rather than silently truncating it.
                close_tag_start, event_end = _find_event_data_close(data, payload_start)
            else:
                event_end = data.find(b"</EVENT>", close_tag_start + len(_DATA_CLOSE_TAG))
                if event_end == -1:
                    raise ValueError("incomplete EVENT block: missing </EVENT>")

        yield data[body_start:event_end]
        cursor = event_end + len(b"</EVENT>")


def _find_event_data_close(data: bytes, payload_start: int) -> tuple[int, int]:
    """Locate the DATA close tag whose only following content is EVENT framing."""

    cursor = payload_start
    while True:
        close_tag_start = data.find(_DATA_CLOSE_TAG, cursor)
        if close_tag_start == -1:
            raise ValueError("incomplete DATA block: missing </DATA>")
        event_end = data.find(b"</EVENT>", close_tag_start + len(_DATA_CLOSE_TAG))
        if event_end == -1:
            raise ValueError("incomplete EVENT block: missing </EVENT>")
        between = data[close_tag_start + len(_DATA_CLOSE_TAG) : event_end]
        if not between.strip(b" \t\r\n"):
            return close_tag_start, event_end
        cursor = close_tag_start + len(_DATA_CLOSE_TAG)


def _expected_payload_nbytes(format_attrs: dict[str, str]) -> int | None:
    length = format_attrs.get("LENGTH")
    bytes_per_sample = format_attrs.get("BYTES_PER_SAMPLE")
    if length is None or bytes_per_sample is None:
        return None
    return int(length) * int(bytes_per_sample)


def _parse_attributes(line: str | None) -> dict[str, str]:
    """Parse XML-like KEY=value attributes from a line."""

    if line is None:
        return {}
    return {key: value for key, value in _ATTR_RE.findall(line)}


def _parse_bare_tag_value(line: str, tag_name: str) -> str | None:
    """Parse a single bare value from a tag like <BOARD value />."""

    prefix = f"<{tag_name} "
    suffix = " />"
    if line.startswith(prefix) and line.endswith(suffix):
        return line[len(prefix) : -len(suffix)]
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-like datetime string conservatively."""

    if value is None:
        return None
    return parse_source_datetime(value)


def _parse_int(value: str | None) -> int | None:
    """Parse an integer value conservatively."""

    if value is None:
        return None
    return int(value)


def _parse_float(value: str | None) -> float | None:
    """Parse a float value conservatively."""

    if value is None:
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite numeric value: {value!r}")
    return parsed


def _parse_bool(value: str | None) -> bool | None:
    """Parse a simple YES/NO boolean value."""

    if value is None:
        return None
    if value == "YES":
        return True
    if value == "NO":
        return False
    return None


def _looks_like_tag_line(line: str) -> bool:
    return line.startswith("<") and line.endswith("/>")


def _report_malformed_block(
    callback: MalformedMerBlockCallback | None,
    *,
    block_index: int | None,
    block_kind: str,
    raw_block: str,
    error: str,
) -> None:
    if callback is not None:
        callback(block_index, block_kind, raw_block, error)
