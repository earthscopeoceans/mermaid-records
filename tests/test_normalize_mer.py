# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from mermaid_records import __version__
import mermaid_records.normalize_mer as normalize_mer_module
from mermaid_records.normalize_mer import write_mer_jsonl_families

PSD_MER_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "data" / "fixtures" / "465.152-R-0001" / "mer"
)


def test_write_mer_jsonl_families_preserves_environment_parameter_and_event_rows(
    tmp_path: Path,
) -> None:
    mer_path = tmp_path / "0100_sample.MER"
    mer_path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "\t<SOFTWARE 2.1344 />\n"
            "\t<DIVE ID=2 EVENTS=9 />\n"
            "\t<POOL EVENTS=12 SIZE=275998 />\n"
            "\t<GPSINFO DATE=2024-02-07T22:47:22 LAT=+2845.7300 LON=+13848.3010 />\n"
            "\t<DRIFT SEC=-1 USEC=-108856 />\n"
            "\t<CLOCK Hz=3686332 />\n"
            "\t<SAMPLE MIN=-12392144 MAX=13882064 />\n"
            "\t<TRUE_SAMPLE_FREQ FS_Hz=40.014255 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<ADC GAIN=1 BUFFER=ON />\n"
            "\t<INPUT_FILTER CUTOFF=0.100000 POLES=4 />\n"
            "\t<STALTA STA=80 LTA=800 RATIO=2.100000 />\n"
            "\t<EVENT_LEN PRE=200 POST=4000 MIN=1024 MAX=8192 />\n"
            "\t<RATING MIN=0.100000 MAX=0.900000 />\n"
            "\t<CDF24 ENABLED=YES />\n"
            "\t<MODEL EXTRAPOLATED=0 REF0=0.0 WEIGHT0=1.0 />\n"
            "\t<ASCEND_THRESH PRESSURE=10 COUNTDOWN=3 />\n"
            "\t<MISC UPLOAD_MAX=100kB />\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-07T22:47:22 FNAME=2024-02-07T22_47_22.000000 "
            "SMP_OFFSET=614054 TRUE_FS=40.014107 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=4832 />\n"
            "\t<DATA>ABC</DATA>\n"
            "</EVENT>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-08T00:00:01.737670 PRESSURE=1504.00 TEMPERATURE=-11.0000 "
            "CRITERION=0.0296122 SNR=2.556 TRIG=2000 DETRIG=5819 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=1024 />\n"
            "\t<DATA>WXYZ</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )

    output_dir = tmp_path / "jsonl"
    summary = write_mer_jsonl_families([mer_path], output_dir)

    environment_records = _read_jsonl(output_dir / "mer_environment_records.jsonl")
    parameter_records = _read_jsonl(output_dir / "mer_parameter_records.jsonl")
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")
    assert {
        record["mermaid_records_version"]
        for record in [*environment_records, *parameter_records, *event_records]
    } == {__version__}

    assert summary.environment_records == 9
    assert summary.parameter_records == 9
    assert summary.event_records == 2
    assert summary.total_mer_files == 1
    assert summary.zero_event_files == 0
    assert summary.total_event_blocks == 2
    assert summary.environment_kind_counts == {
        "board": 1,
        "clock": 1,
        "dive": 1,
        "drift": 1,
        "gpsinfo": 1,
        "pool": 1,
        "sample": 1,
        "software": 1,
        "true_sample_freq": 1,
    }
    assert summary.parameter_kind_counts == {
        "adc": 1,
        "ascend_thresh": 1,
        "cdf24": 1,
        "event_len": 1,
        "input_filter": 1,
        "misc": 1,
        "model": 1,
        "rating": 1,
        "stalta": 1,
    }
    assert summary.unknown_environment_tags == []
    assert summary.unknown_parameter_tags == []
    assert summary.unknown_info_keys == []
    assert summary.unknown_format_keys == []

    gpsinfo_record = next(
        record for record in environment_records if record["environment_kind"] == "gpsinfo"
    )
    assert gpsinfo_record["gpsinfo_date"] == "2024-02-07T22:47:22.000000Z"
    assert gpsinfo_record["raw_values"] == {
        "date": "2024-02-07T22:47:22",
        "lat": "+2845.7300",
        "lon": "+13848.3010",
    }
    assert gpsinfo_record["line"] == "\t<GPSINFO DATE=2024-02-07T22:47:22 LAT=+2845.7300 LON=+13848.3010 />"

    drift_record = next(
        record for record in environment_records if record["environment_kind"] == "drift"
    )
    assert drift_record["raw_values"] == {"sec": "-1", "usec": "-108856"}
    assert gpsinfo_record["source_file"] == mer_path.name

    adc_record = next(
        record for record in parameter_records if record["parameter_kind"] == "adc"
    )
    assert adc_record["raw_values"] == {"buffer": "ON", "gain": "1"}
    assert adc_record["source_file"] == mer_path.name

    model_record = next(
        record for record in parameter_records if record["parameter_kind"] == "model"
    )
    assert model_record["raw_values"] == {
        "extrapolated": "0",
        "ref0": "0.0",
        "weight0": "1.0",
    }

    assert event_records[0]["block_index"] == 0
    assert list(event_records[0]) == [
        "instrument_id",
        "instrument_serial",
        "mermaid_records_version",
        "source_file",
        "source_id",
        "source_sha256",
        "source_container",
        "block_index",
        "event_index",
        "event_info_date",
        "event_rounds",
        "date",
        "rounds",
        "pressure",
        "temperature",
        "criterion",
        "snr",
        "trig",
        "detrig",
        "fname",
        "smp_offset",
        "true_fs",
        "endianness",
        "bytes_per_sample",
        "sampling_rate",
        "stages",
        "normalized",
        "length",
        "encoded_payload",
        "encoded_payload_byte_count",
        "data_payload_nbytes",
        "expected_payload_nbytes",
        "payload_length_matches_expected",
        "raw_info_line",
        "raw_format_line",
    ]
    assert event_records[0]["date"] == "2024-02-07T22:47:22.000000Z"
    assert event_records[0]["event_info_date"] == "2024-02-07T22:47:22.000000Z"
    assert event_records[0]["fname"] == "2024-02-07T22_47_22.000000"
    assert event_records[0]["smp_offset"] == "614054"
    assert event_records[0]["true_fs"] == "40.014107"
    assert event_records[0]["encoded_payload"] == base64.b64encode(b"ABC").decode("ascii")
    assert event_records[0]["encoded_payload_byte_count"] == 3
    assert event_records[0]["data_payload_nbytes"] == 3
    assert event_records[0]["expected_payload_nbytes"] == 19328
    assert event_records[0]["payload_length_matches_expected"] is False
    assert event_records[0]["raw_info_line"] == (
        '<INFO DATE=2024-02-07T22:47:22 FNAME=2024-02-07T22_47_22.000000 '
        'SMP_OFFSET=614054 TRUE_FS=40.014107 />'
    )

    assert event_records[1]["pressure"] == "1504.00"
    assert event_records[1]["temperature"] == "-11.0000"
    assert event_records[1]["criterion"] == "0.0296122"
    assert event_records[1]["snr"] == "2.556"
    assert event_records[1]["trig"] == "2000"
    assert event_records[1]["detrig"] == "5819"
    assert event_records[1]["length"] == "1024"
    assert event_records[1]["encoded_payload"] == base64.b64encode(b"WXYZ").decode("ascii")
    assert event_records[1]["encoded_payload_byte_count"] == 4
    assert event_records[1]["data_payload_nbytes"] == 4
    assert event_records[1]["expected_payload_nbytes"] == 4096
    assert event_records[1]["payload_length_matches_expected"] is False
    assert "record_time" not in event_records[1]
    assert "time" not in event_records[1]
    assert all(record["instrument_id"] == "0100" for record in event_records)
    assert all(record["instrument_serial"] == "0100" for record in event_records)
    assert all(record["source_file"] == mer_path.name for record in event_records)
    assert (output_dir / "mer_event_records.0100.jsonl").exists()


def test_write_mer_jsonl_families_converts_offset_dates_to_utc(
    tmp_path: Path,
) -> None:
    mer_path = tmp_path / "0100_offset.MER"
    mer_path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<GPSINFO DATE=2024-02-07T22:47:22+02:30 LAT=+2845.7300 LON=+13848.3010 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-07T22:47:22.123456+02:30 ROUNDS=1 />\n"
            "\t<DATA>ABC</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )

    output_dir = tmp_path / "jsonl"
    write_mer_jsonl_families([mer_path], output_dir)

    environment_records = _read_jsonl(output_dir / "mer_environment_records.jsonl")
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")

    assert environment_records[0]["gpsinfo_date"] == "2024-02-07T20:17:22.000000Z"
    assert event_records[0]["event_info_date"] == "2024-02-07T20:17:22.123456Z"
    assert event_records[0]["date"] == "2024-02-07T20:17:22.123456Z"


def test_write_mer_jsonl_families_accepts_canonical_instrument_id_override(tmp_path: Path) -> None:
    mer_path = tmp_path / "0100_sample.MER"
    mer_path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<MISC UPLOAD_MAX=100kB />\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-07T22:47:22 FNAME=2024-02-07T22_47_22.000000 "
            "SMP_OFFSET=614054 TRUE_FS=40.014107 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=4832 />\n"
            "\t<DATA>ABC</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )

    output_dir = tmp_path / "jsonl"
    write_mer_jsonl_families(
        [mer_path],
        output_dir,
        instrument_id="T0100",
        instrument_serial="467.174-T-0100",
    )
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")

    assert event_records[0]["instrument_id"] == "T0100"
    assert event_records[0]["instrument_serial"] == "467.174-T-0100"


def test_write_mer_jsonl_families_supports_rounds_info_field(tmp_path: Path) -> None:
    mer_path = tmp_path / "0100_rounds.MER"
    mer_path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<MISC UPLOAD_MAX=100kB />\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-07T22:47:22 ROUNDS=17 FNAME=2024-02-07T22_47_22.000000 "
            "SMP_OFFSET=614054 TRUE_FS=40.014107 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=4832 />\n"
            "\t<DATA>ABC</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )

    output_dir = tmp_path / "jsonl"
    write_mer_jsonl_families([mer_path], output_dir)
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")

    assert event_records[0]["rounds"] == "17"


def test_mer_environment_and_parameter_stage_tag_spaces_are_exclusive() -> None:
    for tag in normalize_mer_module._ENVIRONMENT_KIND_MAP:
        assert tag not in normalize_mer_module._PARAMETER_KIND_MAP
        assert (
            normalize_mer_module._classify_mer_tag_kind(tag, stage_name="environment")
            == normalize_mer_module._ENVIRONMENT_KIND_MAP[tag]
        )
    for tag in normalize_mer_module._PARAMETER_KIND_MAP:
        assert tag not in normalize_mer_module._ENVIRONMENT_KIND_MAP
        assert (
            normalize_mer_module._classify_mer_tag_kind(tag, stage_name="parameter")
            == normalize_mer_module._PARAMETER_KIND_MAP[tag]
        )


def test_mer_tag_stage_multi_match_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(normalize_mer_module._PARAMETER_KIND_MAP, "BOARD", "bad_overlap")
    with pytest.raises(ValueError, match="MER derived-family multi-match"):
        normalize_mer_module._classify_mer_tag_kind("BOARD", stage_name="environment")


def test_write_mer_jsonl_families_supports_stanford_process_parameter(tmp_path: Path) -> None:
    mer_path = tmp_path / "0100_stanford.MER"
    mer_path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<STANFORD_PROCESS DURATION_H=12 PROCESS_PERIOD_H=1 WINDOW_LEN=3600 "
            "WINDOW_TYPE=HANN OVERLAP_PERCENT=50 DB_OFFSET=120 />\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-07T22:47:22 FNAME=2024-02-07T22_47_22.000000 "
            "SMP_OFFSET=614054 TRUE_FS=40.014107 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=4832 />\n"
            "\t<DATA>ABC</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )

    output_dir = tmp_path / "jsonl"
    summary = write_mer_jsonl_families([mer_path], output_dir)
    parameter_records = _read_jsonl(output_dir / "mer_parameter_records.jsonl")

    assert summary.parameter_kind_counts == {"stanford_process": 1}
    assert parameter_records[0]["parameter_kind"] == "stanford_process"
    assert parameter_records[0]["raw_values"] == {
        "db_offset": "120",
        "duration_h": "12",
        "overlap_percent": "50",
        "process_period_h": "1",
        "window_len": "3600",
        "window_type": "HANN",
    }
    assert parameter_records[0]["line"] == (
        "\t<STANFORD_PROCESS DURATION_H=12 PROCESS_PERIOD_H=1 WINDOW_LEN=3600 "
        "WINDOW_TYPE=HANN OVERLAP_PERCENT=50 DB_OFFSET=120 />"
    )


def test_write_mer_jsonl_families_reports_source_file_on_unhandled_field(tmp_path: Path) -> None:
    mer_path = tmp_path / "0100_bad.MER"
    mer_path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<MISC UPLOAD_MAX=100kB />\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-07T22:47:22 BADKEY=17 FNAME=2024-02-07T22_47_22.000000 "
            "SMP_OFFSET=614054 TRUE_FS=40.014107 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=4832 />\n"
            "\t<DATA>ABC</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )

    output_dir = tmp_path / "jsonl"

    try:
        write_mer_jsonl_families([mer_path], output_dir)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected MER normalization failure")

    assert mer_path.as_posix() in message
    assert "BADKEY" in message


def test_write_mer_jsonl_families_counts_zero_event_files(tmp_path: Path) -> None:
    empty_mer = tmp_path / "0001_empty.MER"
    empty_mer.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<MISC UPLOAD_MAX=100kB />\n"
            "</PARAMETERS>\n"
        ).encode("ascii")
    )

    output_dir = tmp_path / "jsonl"
    summary = write_mer_jsonl_families([empty_mer], output_dir)

    assert summary.total_mer_files == 1
    assert summary.zero_event_files == 1
    assert summary.total_event_blocks == 0
    assert _read_jsonl(output_dir / "mer_event_records.jsonl") == []


def test_write_mer_jsonl_families_excludes_data_framing_bytes_from_payload_length(
    tmp_path: Path,
) -> None:
    mer_path = tmp_path / "0100_framed.MER"
    payload = b"A" * 19328
    mer_path.write_bytes(
        (
            b"<ENVIRONMENT>\n"
            b"\t<BOARD 452116600-A0 />\n"
            b"</ENVIRONMENT>\n"
            b"<PARAMETERS>\n"
            b"\t<MISC UPLOAD_MAX=100kB />\n"
            b"</PARAMETERS>\n"
            b"<EVENT>\n"
            b"\t<INFO DATE=2024-02-07T22:47:22 FNAME=framed.000000 SMP_OFFSET=614054 TRUE_FS=40.014107 />\n"
            b"\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            b"STAGES=5 NORMALIZED=YES LENGTH=4832 />\n"
            b"\t<DATA>\n\r"
            + payload
            + b"\n\r\t</DATA>\n"
            b"</EVENT>\n"
        )
    )

    output_dir = tmp_path / "jsonl"
    write_mer_jsonl_families([mer_path], output_dir)
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")

    assert event_records[0]["data_payload_nbytes"] == 19328
    assert event_records[0]["encoded_payload_byte_count"] == 19328
    assert event_records[0]["expected_payload_nbytes"] == 19328
    assert event_records[0]["payload_length_matches_expected"] is True


def test_write_mer_jsonl_families_reports_payload_length_mismatch(
    tmp_path: Path,
) -> None:
    mer_path = tmp_path / "0100_payload_mismatch.MER"
    payload = b"B" * 7
    mer_path.write_bytes(
        (
            b"<ENVIRONMENT>\n"
            b"\t<BOARD 452116600-A0 />\n"
            b"</ENVIRONMENT>\n"
            b"<PARAMETERS>\n"
            b"\t<MISC UPLOAD_MAX=100kB />\n"
            b"</PARAMETERS>\n"
            b"<EVENT>\n"
            b"\t<INFO DATE=2024-02-07T22:47:22 FNAME=mismatch.000000 SMP_OFFSET=1 TRUE_FS=40.0 />\n"
            b"\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            b"STAGES=5 NORMALIZED=YES LENGTH=2 />\n"
            b"\t<DATA>\n\r"
            + payload
            + b"\n\r\t</DATA>\n"
            b"</EVENT>\n"
        )
    )

    output_dir = tmp_path / "jsonl"
    write_mer_jsonl_families([mer_path], output_dir)
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")

    assert event_records[0]["data_payload_nbytes"] == 7
    assert event_records[0]["encoded_payload_byte_count"] == 7
    assert event_records[0]["expected_payload_nbytes"] == 8
    assert event_records[0]["payload_length_matches_expected"] is False


def test_write_mer_jsonl_families_supports_stanford_events_without_format(
    tmp_path: Path,
) -> None:
    mer_path = tmp_path / "0002_stanford_psd.MER"
    payload_one = b"\x01\x02\x03\x04"
    payload_two = b"\x05\x06"
    mer_path.write_bytes(
        (
            b"<ENVIRONMENT>\n"
            b"\t<BOARD 465152600-75 />\n"
            b"\t<SOFTWARE 2.1377-STANFORD />\n"
            b"\t<DIVE ID=5 EVENTS=0 />\n"
            b"\t<POOL EVENTS=2 SIZE=6 />\n"
            b"\t<GPSINFO DATE=2021-10-09T02:10:30 LAT=+4324.8290 LON=+00734.9800 />\n"
            b"\t<GPSINFO DATE=2021-10-09T02:21:13 LAT=+4324.7380 LON=+00734.8920 />\n"
            b"\t<DRIFT USEC=0 />\n"
            b"\t<DRIFT SEC=1 USEC=-999938 />\n"
            b"\t<CLOCK Hz=3686304 />\n"
            b"\t<CLOCK Hz=3686303 />\n"
            b"\t<SAMPLE MIN=2147483647 MAX=-2147483648 />\n"
            b"\t<TRUE_SAMPLE_FREQ FS_Hz=40.014121 />\n"
            b"</ENVIRONMENT>\n"
            b"<PARAMETERS>\n"
            b"\t<ADC GAIN=1 BUFFER=ON />\n"
            b"\t<STANFORD_PROCESS DURATION_h=168 PROCESS_PERIOD_h=3 WINDOW_LEN=1024 WINDOW_TYPE=Hanning OVERLAP_PERCENT=10 dB_OFFSET=0 />\n"
            b"\t<MISC UPLOAD_MAX=120kB />\n"
            b"</PARAMETERS>\n"
            b"<EVENT>\n"
            b"\t<INFO DATE=2021-10-16T04:31:58.638228 ROUNDS=468 />\n"
            b"\t<DATA>\n\r"
            + payload_one
            + b"\n\r\t</DATA>\n\r</EVENT>\n"
            b"<EVENT>\n"
            b"\t<INFO DATE=2021-10-16T01:31:59.250533 ROUNDS=469 />\n"
            b"\t<DATA>\n\r"
            + payload_two
            + b"\n\r\t</DATA>\n\r</EVENT>\n"
        )
    )

    output_dir = tmp_path / "jsonl"
    malformed_mer_blocks: list[dict[str, object]] = []
    summary = write_mer_jsonl_families(
        [mer_path],
        output_dir,
        run_id="run-stanford",
        malformed_mer_blocks=malformed_mer_blocks,
    )

    environment_records = _read_jsonl(output_dir / "mer_environment_records.jsonl")
    parameter_records = _read_jsonl(output_dir / "mer_parameter_records.jsonl")
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")

    assert malformed_mer_blocks == []
    assert summary.total_mer_files == 1
    assert summary.zero_event_files == 0
    assert [record["event_index"] for record in event_records] == [0, 1]
    assert [record["event_rounds"] for record in event_records] == ["468", "469"]
    assert [record["event_info_date"] for record in event_records] == [
        "2021-10-16T04:31:58.638228Z",
        "2021-10-16T01:31:59.250533Z",
    ]
    assert [record["raw_format_line"] for record in event_records] == [None, None]
    assert event_records[0]["encoded_payload"] == base64.b64encode(payload_one).decode("ascii")
    assert event_records[0]["encoded_payload_byte_count"] == len(payload_one)
    assert event_records[0]["expected_payload_nbytes"] is None
    assert event_records[0]["payload_length_matches_expected"] is None
    assert event_records[1]["encoded_payload"] == base64.b64encode(payload_two).decode("ascii")
    assert event_records[1]["encoded_payload_byte_count"] == len(payload_two)

    board_record = next(
        record for record in environment_records if record["environment_kind"] == "board"
    )
    software_record = next(
        record for record in environment_records if record["environment_kind"] == "software"
    )
    dive_record = next(
        record for record in environment_records if record["environment_kind"] == "dive"
    )
    pool_record = next(
        record for record in environment_records if record["environment_kind"] == "pool"
    )
    sample_record = next(
        record for record in environment_records if record["environment_kind"] == "sample"
    )
    true_fs_record = next(
        record
        for record in environment_records
        if record["environment_kind"] == "true_sample_freq"
    )

    assert board_record["board"] == "465152600-75"
    assert software_record["software"] == "2.1377-STANFORD"
    assert dive_record["dive_id"] == 5
    assert dive_record["dive_declared_event_count"] == 0
    assert pool_record["pool_declared_event_count"] == 2
    assert pool_record["pool_declared_size_bytes"] == 6
    assert sample_record["sample_min"] == 2147483647
    assert sample_record["sample_max"] == -2147483648
    assert true_fs_record["true_sample_freq_hz"] == 40.014121
    assert sum(1 for record in environment_records if record["environment_kind"] == "gpsinfo") == 2
    assert sum(1 for record in environment_records if record["environment_kind"] == "drift") == 2
    assert sum(1 for record in environment_records if record["environment_kind"] == "clock") == 2

    adc_record = next(
        record for record in parameter_records if record["parameter_kind"] == "adc"
    )
    stanford_process_record = next(
        record
        for record in parameter_records
        if record["parameter_kind"] == "stanford_process"
    )
    misc_record = next(
        record for record in parameter_records if record["parameter_kind"] == "misc"
    )

    assert adc_record["adc_gain"] == 1
    assert adc_record["adc_buffer"] == "ON"
    assert stanford_process_record["stanford_process_duration_h"] == 168
    assert stanford_process_record["stanford_process_period_h"] == 3
    assert stanford_process_record["stanford_process_window_len"] == 1024
    assert stanford_process_record["stanford_process_window_type"] == "Hanning"
    assert stanford_process_record["stanford_process_overlap_percent"] == 10
    assert stanford_process_record["stanford_process_db_offset"] == 0.0
    assert misc_record["upload_max"] == "120kB"


def test_write_mer_jsonl_families_accepts_metadata_only_stanford_mer(
    tmp_path: Path,
) -> None:
    mer_path = tmp_path / "0007_metadata_only.MER"
    mer_path.write_bytes(
        (
            b"<ENVIRONMENT>\n"
            b"\t<BOARD 465152600-80 />\n"
            b"\t<SOFTWARE 2.1377-STANFORD />\n"
            b"\t<DIVE ID=0 EVENTS=0 />\n"
            b"\t<POOL EVENTS=0 SIZE=0 />\n"
            b"</ENVIRONMENT>\n"
            b"<PARAMETERS>\n"
            b"\t<ADC GAIN=1 BUFFER=ON />\n"
            b"\t<STANFORD_PROCESS DURATION_h=168 PROCESS_PERIOD_h=1 WINDOW_LEN=512 WINDOW_TYPE=Hanning OVERLAP_PERCENT=0 dB_OFFSET=0 />\n"
            b"\t<MISC UPLOAD_MAX=120kB />\n"
            b"</PARAMETERS>\n"
        )
    )

    output_dir = tmp_path / "jsonl"
    malformed_mer_blocks: list[dict[str, object]] = []
    summary = write_mer_jsonl_families(
        [mer_path],
        output_dir,
        run_id="run-stanford-empty",
        malformed_mer_blocks=malformed_mer_blocks,
    )

    assert malformed_mer_blocks == []
    assert summary.total_mer_files == 1
    assert summary.zero_event_files == 1
    assert summary.total_event_blocks == 0
    assert _read_jsonl(output_dir / "mer_event_records.jsonl") == []


def test_write_mer_jsonl_families_quarantines_repeated_metadata_sections(
    tmp_path: Path,
) -> None:
    mer_path = tmp_path / "0007_repeated_metadata.MER"
    mer_path.write_bytes(
        b"<ENVIRONMENT><BOARD first /></ENVIRONMENT>"
        b"<ENVIRONMENT><BOARD second /></ENVIRONMENT>"
        b"<PARAMETERS><MISC UPLOAD_MAX=100kB /></PARAMETERS>"
    )
    malformed_mer_blocks: list[dict[str, object]] = []

    output_dir = tmp_path / "jsonl"
    summary = write_mer_jsonl_families(
        [mer_path],
        output_dir,
        run_id="run-repeated-metadata",
        malformed_mer_blocks=malformed_mer_blocks,
    )

    environment_records = _read_jsonl(output_dir / "mer_environment_records.jsonl")
    assert summary.environment_records == 2
    assert [record["line"] for record in environment_records] == [
        "<BOARD first />",
        "<BOARD second />",
    ]
    assert malformed_mer_blocks[0]["block_kind"] == "repeated_metadata_section"
    assert "second" in malformed_mer_blocks[0]["raw_block"]


def test_write_mer_jsonl_families_exercises_real_psd_fixture_subset(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "jsonl"
    malformed_mer_blocks: list[dict[str, object]] = []

    summary = write_mer_jsonl_families(
        [
            PSD_MER_FIXTURE_ROOT / "0001_6255B101.MER",
            PSD_MER_FIXTURE_ROOT / "0001_625CB0C0.MER",
        ],
        output_dir,
        run_id="run-real-psd",
        malformed_mer_blocks=malformed_mer_blocks,
    )

    environment_records = _read_jsonl(output_dir / "mer_environment_records.jsonl")
    parameter_records = _read_jsonl(output_dir / "mer_parameter_records.jsonl")
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")

    assert malformed_mer_blocks == []
    assert summary.environment_records == 27
    assert summary.parameter_records == 6
    assert summary.event_records == 10
    assert summary.total_mer_files == 2
    assert summary.zero_event_files == 1
    assert summary.total_event_blocks == 10
    assert summary.environment_kind_counts == {
        "board": 2,
        "clock": 5,
        "dive": 2,
        "drift": 5,
        "gpsinfo": 5,
        "pool": 2,
        "sample": 2,
        "software": 2,
        "true_sample_freq": 2,
    }
    assert summary.parameter_kind_counts == {
        "adc": 2,
        "misc": 2,
        "stanford_process": 2,
    }

    assert {record["source_file"] for record in environment_records} == {
        "0001_6255B101.MER",
        "0001_625CB0C0.MER",
    }
    assert {record["source_file"] for record in parameter_records} == {
        "0001_6255B101.MER",
        "0001_625CB0C0.MER",
    }
    assert {record["source_file"] for record in event_records} == {"0001_625CB0C0.MER"}
    assert [record["event_index"] for record in event_records] == list(range(10))
    assert [record["event_rounds"] for record in event_records[:3]] == [
        "237",
        "468",
        "468",
    ]
    assert event_records[-1]["event_rounds"] == "468"
    assert event_records[0]["event_info_date"] == "2022-04-12T10:02:58.273497Z"
    assert event_records[-1]["event_info_date"] == "2022-04-11T07:03:03.177795Z"
    assert [record["raw_format_line"] for record in event_records] == [None] * 10
    assert {record["encoded_payload_byte_count"] for record in event_records} == {512}
    assert {record["data_payload_nbytes"] for record in event_records} == {512}
    assert {record["expected_payload_nbytes"] for record in event_records} == {None}
    assert {record["payload_length_matches_expected"] for record in event_records} == {None}
    assert {record["stanford_psd_processing"]["process_period_h"] for record in event_records} == {3}
    assert {record["stanford_psd_processing"]["window_len"] for record in event_records} == {1024}
    assert {record["stanford_psd_processing"]["overlap_percent"] for record in event_records} == {10}
    assert {record["stanford_psd_processing"]["raw_parameter_line"] for record in event_records} == {
        "\t<STANFORD_PROCESS  DURATION_h=168  PROCESS_PERIOD_h=3  WINDOW_LEN=1024  WINDOW_TYPE=Hanning  OVERLAP_PERCENT=10  dB_OFFSET=0 />"
    }

    stanford_process_records = [
        record
        for record in parameter_records
        if record["parameter_kind"] == "stanford_process"
    ]
    assert len(stanford_process_records) == 2
    assert {record["stanford_process_duration_h"] for record in stanford_process_records} == {168}
    assert {record["stanford_process_period_h"] for record in stanford_process_records} == {3}
    assert {record["stanford_process_window_len"] for record in stanford_process_records} == {1024}
    assert {record["stanford_process_window_type"] for record in stanford_process_records} == {
        "Hanning"
    }
    assert {record["stanford_process_overlap_percent"] for record in stanford_process_records} == {
        10
    }
    assert {record["stanford_process_db_offset"] for record in stanford_process_records} == {
        0.0
    }

    metadata_only_pool_record = next(
        record
        for record in environment_records
        if record["source_file"] == "0001_6255B101.MER"
        and record["environment_kind"] == "pool"
    )
    eventful_pool_record = next(
        record
        for record in environment_records
        if record["source_file"] == "0001_625CB0C0.MER"
        and record["environment_kind"] == "pool"
    )
    assert metadata_only_pool_record["pool_declared_event_count"] == 0
    assert metadata_only_pool_record["pool_declared_size_bytes"] == 0
    assert eventful_pool_record["pool_declared_event_count"] == 10
    assert eventful_pool_record["pool_declared_size_bytes"] == 6060


def test_write_mer_jsonl_families_excludes_stanford_data_framing_bytes_without_format(
    tmp_path: Path,
) -> None:
    mer_path = tmp_path / "0002_framed_stanford.MER"
    payload = b"\x10\x20\x30\x40\x50"
    mer_path.write_bytes(
        (
            b"<ENVIRONMENT>\n"
            b"\t<BOARD 465152600-75 />\n"
            b"</ENVIRONMENT>\n"
            b"<PARAMETERS>\n"
            b"\t<MISC UPLOAD_MAX=120kB />\n"
            b"</PARAMETERS>\n"
            b"<EVENT>\n"
            b"\t<INFO DATE=2021-10-16T04:31:58.638228 ROUNDS=468 />\n"
            b"\t<DATA>\n\r"
            + payload
            + b"\n\r\t</DATA>\n\r</EVENT>\n"
        )
    )

    output_dir = tmp_path / "jsonl"
    write_mer_jsonl_families([mer_path], output_dir)
    event_records = _read_jsonl(output_dir / "mer_event_records.jsonl")

    assert event_records[0]["encoded_payload"] == base64.b64encode(payload).decode("ascii")
    assert event_records[0]["encoded_payload_byte_count"] == len(payload)
    assert event_records[0]["data_payload_nbytes"] == len(payload)
    assert event_records[0]["raw_format_line"] is None


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    path = _resolve_jsonl_path(path)
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _resolve_jsonl_path(path: Path) -> Path:
    if path.exists():
        return path
    matches = sorted(path.parent.glob(f"{path.stem}.*{path.suffix}"))
    if len(matches) == 1:
        return matches[0]
    return path
