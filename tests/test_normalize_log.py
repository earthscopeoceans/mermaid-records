# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from mermaid_records import __version__
import mermaid_records.normalize_log as normalize_log_module
from mermaid_records.normalize_log import write_log_jsonl_families

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "data" / "fixtures" / "467.174-T-0100" / "log"
)
DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"


def test_log_record_family_schema_doc_hit_examples_classify_as_documented(
    tmp_path: Path,
) -> None:
    doc_examples = _log_schema_doc_hit_examples()
    assert set(doc_examples) == {
        "log_acquisition_records.jsonl",
        "log_ascent_request_records.jsonl",
        "log_battery_records.jsonl",
        "log_gps_records.jsonl",
        "log_parameter_records.jsonl",
        "log_pressure_temperature_records.jsonl",
        "log_ctd_records.jsonl",
        "log_testmode_records.jsonl",
        "log_iridium_records.jsonl",
        "log_unclassified_records.jsonl",
    }

    for filename, raw_lines in doc_examples.items():
        family_stem = filename.removesuffix(".jsonl")
        log_path = tmp_path / f"0100_doc_{family_stem}.LOG"
        log_path.write_text("\n".join([*raw_lines, ""]), encoding="utf-8")
        output_dir = tmp_path / family_stem

        write_log_jsonl_families(
            [log_path],
            output_dir,
            instrument_id="T0100",
            instrument_serial="467.174-T-0100",
        )

        raw_lines_by_output = _raw_lines_by_output_file(output_dir)
        target_output = f"{filename.removesuffix('.jsonl')}.467.174-T-0100.jsonl"
        assert set(raw_lines) <= raw_lines_by_output[target_output]

        for other_filename, output_raw_lines in raw_lines_by_output.items():
            if other_filename == target_output:
                continue
            assert set(raw_lines).isdisjoint(output_raw_lines)


def test_write_log_jsonl_families_preserves_unclassified_records(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_sample.LOG"
    log_path.write_text(
        "\n".join(
            [
                '1700000000:[UPLOAD,0248]Upload data files...',
                '1700000001:[UPLOAD,0231]"0100/AAAA0001.MER" uploaded at 83bytes/s',
                "1700000002:[PRESS ,0038]P+20179mbar,T+32767mdegC",
                "1700000003:[PUMP  ,0016]pump during 30000ms",
                "1700000004:[SURF  ,0071]<WARN>timeout",
                "1700000005:[MAIN  ,0007]buoy 467.174-T-0100",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)

    assert summary.total_records == 5
    assert summary.acquisition_records == 0
    assert summary.ascent_request_records == 0
    assert summary.gps_records == 0
    assert summary.parameter_records == 0
    assert summary.testmode_records == 0
    assert summary.ctd_records == 0
    assert summary.iridium_records == 1
    assert summary.pressure_temperature_records == 1
    assert summary.battery_records == 0
    assert summary.unclassified_records == 3
    assert summary.ordinary_log_lines_examined == 6
    assert summary.source_line_assignments == 6
    assert summary.duplicate_assignments == 0
    assert summary.missing_assignments == 0

    acquisition_records = _read_jsonl(output_dir / "log_acquisition_records.jsonl")
    ascent_request_records = _read_jsonl(
        output_dir / "log_ascent_request_records.jsonl"
    )
    gps_records = _read_jsonl(output_dir / "log_gps_records.jsonl")
    pressure_temperature_records = _read_jsonl(
        output_dir / "log_pressure_temperature_records.jsonl"
    )
    battery_records = _read_jsonl(output_dir / "log_battery_records.jsonl")
    parameter_records = _read_jsonl(output_dir / "log_parameter_records.jsonl")
    testmode_records = _read_jsonl(output_dir / "log_testmode_records.jsonl")
    ctd_records = _read_jsonl(output_dir / "log_ctd_records.jsonl")
    iridium_records = _read_jsonl(output_dir / "log_iridium_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert not (output_dir / "log_operational_records.jsonl").exists()
    assert acquisition_records == []
    assert ascent_request_records == []
    assert gps_records == []
    assert len(pressure_temperature_records) == 1
    assert battery_records == []
    assert parameter_records == []
    assert testmode_records == []
    assert ctd_records == []
    assert len(iridium_records) == 1
    assert len(unclassified_records) == 3
    all_records = [
        *pressure_temperature_records,
        *iridium_records,
        *unclassified_records,
    ]
    assert {
        record["mermaid_records_version"]
        for record in all_records
    } == {__version__}

    assert list(iridium_records[0]) == [
        "instrument_id",
        "instrument_serial",
        "mermaid_records_version",
        "source_file",
        "source_id",
        "source_sha256",
        "source_container",
        "session_index",
        "session_kind",
        "line_start_index",
        "line_end_index",
        "source_line_numbers",
        "start_record_time",
        "end_record_time",
        "start_log_epoch_time",
        "end_log_epoch_time",
        "iridium_event_count",
        "iridium_events",
        "raw_lines",
    ]
    assert iridium_records[0]["start_record_time"] == "2023-11-14T22:13:20.000000Z"
    assert iridium_records[0]["end_record_time"] == "2023-11-14T22:13:21.000000Z"
    assert iridium_records[0]["start_log_epoch_time"] == "1700000000"
    assert iridium_records[0]["end_log_epoch_time"] == "1700000001"
    assert iridium_records[0]["source_file"] == log_path.name
    assert iridium_records[0]["source_id"] == (
        f"sha256:{iridium_records[0]['source_sha256']}"
    )
    assert iridium_records[0]["instrument_serial"] == "0100"
    assert iridium_records[0]["source_line_numbers"] == [1, 2]
    assert (output_dir / "log_iridium_records.0100.jsonl").exists()
    assert "time" not in iridium_records[0]

    assert list(unclassified_records[0]) == [
        "instrument_id",
        "instrument_serial",
        "mermaid_records_version",
        "source_file",
        "source_id",
        "source_sha256",
        "source_container",
        "record_time",
        "log_epoch_time",
        "subsystem",
        "code",
        "message",
        "source_line_number",
        "severity",
        "unclassified_reason",
        "raw_line",
    ]

    iridium_events = iridium_records[0]["iridium_events"]
    assert [event["iridium_event_kind"] for event in iridium_events] == [
        "upload_batch",
        "upload_artifact",
    ]
    assert iridium_events[1]["referenced_artifact"] == "0100_AAAA0001.MER"
    assert iridium_events[1]["rate_bytes_per_s"] == 83
    assert iridium_events[1]["record_time"] == "2023-11-14T22:13:21.000000Z"
    assert iridium_events[1]["log_epoch_time"] == "1700000001"
    assert "time" not in iridium_events[1]

    assert pressure_temperature_records[0]["pressure_mbar"] == 20179
    assert pressure_temperature_records[0]["temperature_mdegc"] == 32767
    assert pressure_temperature_records[0]["record_time"] == "2023-11-14T22:13:22.000000Z"
    assert pressure_temperature_records[0]["log_epoch_time"] == "1700000002"
    assert pressure_temperature_records[0]["source_file"] == log_path.name
    assert "time" not in pressure_temperature_records[0]

    assert all(
        record["unclassified_reason"] == "no_family_match"
        for record in unclassified_records
    )
    assert unclassified_records[0]["record_time"] == "2023-11-14T22:13:23.000000Z"
    assert unclassified_records[0]["log_epoch_time"] == "1700000003"
    assert "time" not in unclassified_records[0]
    assert [record["message"] for record in unclassified_records] == [
        "pump during 30000ms",
        "<WARN>timeout",
        "buoy 467.174-T-0100"
    ]
    assert all(record["instrument_id"] == "0100" for record in iridium_records)
    assert all(record["instrument_serial"] == "0100" for record in iridium_records)
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_uses_authoritative_bin_for_direct_and_grouped_records(
    tmp_path: Path,
) -> None:
    decoded_log_path = tmp_path / "0100_sample.LOG"
    decoded_log_path.write_text(
        "\n".join(
            [
                "1700000000:[MRMAID,0002]acq started",
                "1700000001:    bypass 20000ms 120000ms (10000ms 200000ms stored)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    authoritative_bin_path = tmp_path / "0100_sample.BIN"
    authoritative_bin_path.write_bytes(b"authoritative-bin")
    output_dir = tmp_path / "jsonl"

    write_log_jsonl_families(
        [decoded_log_path],
        output_dir,
        authoritative_source_files={
            decoded_log_path: authoritative_bin_path,
        },
    )

    records = [
        record
        for path in output_dir.glob("log_*.jsonl")
        for record in _read_jsonl(path)
    ]
    assert len(records) == 2
    assert {record["source_file"] for record in records} == {
        authoritative_bin_path.name
    }


def test_log_rows_distinguish_same_basename_sources_by_content_hash(tmp_path: Path) -> None:
    first = tmp_path / "first" / "0100_same.LOG"
    second = tmp_path / "second" / "0100_same.LOG"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("1700000000:[MAIN  ,0007]first\n", encoding="utf-8")
    second.write_text("1700000001:[MAIN  ,0007]second\n", encoding="utf-8")

    output_dir = tmp_path / "jsonl"
    write_log_jsonl_families([first, second], output_dir)
    rows = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert {row["source_file"] for row in rows} == {"0100_same.LOG"}
    assert len({row["source_id"] for row in rows}) == 2
    assert len({row["source_sha256"] for row in rows}) == 2


def test_write_log_jsonl_families_classifies_extended_pressure_temperature_patterns(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "06_pressure.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[PRESS ,0038]P+20179mbar,T+32767mdegC",
                "1700000001:[MRMAID,0565]1527dbar, -10degC",
                "1700000002:[MAIN  ,0408]internal pressure 78680Pa",
                "1700000003:[MAIN  ,0498]Pint 76872Pa",
                "1700000004:[MAIN  ,0502]Pext -45mbar (rng 30mbar)",
                "1700000005:[BUOY  ,0656]P+151590mbar",
                "1700000006:[VALVE ,0234]battery 15073mV,  502152uA, P  +8921mbar",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)
    pressure_temperature_records = _read_jsonl(
        output_dir / "log_pressure_temperature_records.jsonl"
    )
    battery_records = _read_jsonl(output_dir / "log_battery_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.pressure_temperature_records == 6
    assert summary.battery_records == 1
    assert summary.unclassified_records == 0
    assert [record["message"] for record in pressure_temperature_records] == [
        "P+20179mbar,T+32767mdegC",
        "1527dbar, -10degC",
        "internal pressure 78680Pa",
        "Pint 76872Pa",
        "Pext -45mbar (rng 30mbar)",
        "P+151590mbar",
    ]
    assert pressure_temperature_records[0]["pressure_mbar"] == 20179
    assert pressure_temperature_records[0]["temperature_mdegc"] == 32767
    assert pressure_temperature_records[1]["pressure_dbar"] == 1527
    assert pressure_temperature_records[1]["temperature_degc"] == -10
    assert "pressure_mbar" not in pressure_temperature_records[1]
    assert "temperature_mdegc" not in pressure_temperature_records[1]
    assert pressure_temperature_records[2]["internal_pressure_pa"] == 78680
    assert pressure_temperature_records[3]["internal_pressure_pa"] == 76872
    assert pressure_temperature_records[4]["external_pressure_mbar"] == -45
    assert pressure_temperature_records[4]["external_pressure_range_mbar"] == 30
    assert pressure_temperature_records[5]["pressure_mbar"] == 151590
    assert [record["message"] for record in battery_records] == [
        "battery 15073mV,  502152uA, P  +8921mbar"
    ]
    assert unclassified_records == []
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_counts_source_identity_by_path(
    tmp_path: Path,
) -> None:
    first_log_path = tmp_path / "first" / "0100_same.LOG"
    second_log_path = tmp_path / "second" / "0100_same.LOG"
    first_log_path.parent.mkdir()
    second_log_path.parent.mkdir()
    raw_line = "1700000000:[MAIN  ,0007]same source text"
    first_log_path.write_text(f"{raw_line}\n", encoding="utf-8")
    second_log_path.write_text(f"{raw_line}\n", encoding="utf-8")

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families(
        [first_log_path, second_log_path],
        output_dir,
        instrument_serial="0100",
    )

    assert summary.total_records == 2
    assert summary.unclassified_records == 2
    assert summary.ordinary_log_lines_examined == 2
    assert summary.source_line_assignments == 2
    assert summary.duplicate_assignments == 0
    assert summary.missing_assignments == 0

    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")
    assert [record["source_file"] for record in unclassified_records] == [
        "0100_same.LOG",
        "0100_same.LOG",
    ]
    assert [record["source_line_number"] for record in unclassified_records] == [1, 1]
    assert [record["raw_line"] for record in unclassified_records] == [
        raw_line,
        raw_line,
    ]


def test_write_log_jsonl_families_converts_offset_times_to_utc(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_offset.LOG"
    log_path.write_text(
        "2023-11-14T22:13:20.123456+02:30:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    write_log_jsonl_families([log_path], output_dir)

    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert unclassified_records[0]["record_time"] == "2023-11-14T19:43:20.123456Z"


def test_write_log_jsonl_families_accepts_canonical_instrument_id_override(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_sample.LOG"
    log_path.write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    write_log_jsonl_families(
        [log_path],
        output_dir,
        instrument_id="T0100",
        instrument_serial="467.174-T-0100",
    )
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert unclassified_records[0]["instrument_id"] == "T0100"
    assert unclassified_records[0]["instrument_serial"] == "467.174-T-0100"


def test_write_log_jsonl_families_routes_pressure_rows_out_of_unclassified(
    tmp_path: Path,
) -> None:
    internal_log_path = tmp_path / "0026_5D3CDB8D.LOG"
    internal_log_path.write_text(
        "1564269461:[MAIN  ,408]internal pressure 78680Pa\n",
        encoding="utf-8",
    )
    shared_log_path = tmp_path / "0026_5D48EAB8.LOG"
    shared_log_path.write_text(
        "\n".join(
            [
                "1565146650:[MAIN  ,498]Vbat 14681mV (min 13967mV)",
                "1565146653:[MAIN  ,507]Pext -45mbar (rng 30mbar)",
                "1565060029:[SURF  ,328]7 cmd(s) received",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families(
        [shared_log_path, internal_log_path],
        output_dir,
        instrument_id="P0026",
        instrument_serial="452.020-P-0026",
    )

    pressure_temperature_records = _read_jsonl(
        output_dir / "log_pressure_temperature_records.jsonl"
    )
    battery_records = _read_jsonl(output_dir / "log_battery_records.jsonl")
    iridium_records = _read_jsonl(output_dir / "log_iridium_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.pressure_temperature_records == 2
    assert summary.battery_records == 1
    assert summary.iridium_records == 1
    assert summary.unclassified_records == 0
    assert not (output_dir / "log_operational_records.jsonl").exists()
    assert {
        (record["source_file"], record["message"])
        for record in pressure_temperature_records
    } == {
        ("0026_5D3CDB8D.LOG", "internal pressure 78680Pa"),
        ("0026_5D48EAB8.LOG", "Pext -45mbar (rng 30mbar)"),
    }
    pressure_by_message = {
        record["message"]: record for record in pressure_temperature_records
    }
    assert (
        pressure_by_message["internal pressure 78680Pa"]["internal_pressure_pa"]
        == 78680
    )
    assert pressure_by_message["Pext -45mbar (rng 30mbar)"][
        "external_pressure_mbar"
    ] == -45
    assert pressure_by_message["Pext -45mbar (rng 30mbar)"][
        "external_pressure_range_mbar"
    ] == 30
    assert len(battery_records) == 1
    assert {
        key: battery_records[0][key]
        for key in (
            "instrument_id", "instrument_serial", "mermaid_records_version",
            "source_file", "source_container", "record_time", "log_epoch_time",
            "subsystem", "code", "message", "source_line_number",
            "battery_record_kind", "voltage_mv", "current_ua", "minimum_voltage_mv",
            "raw_line",
        )
    } == {
        "instrument_id": "P0026", "instrument_serial": "452.020-P-0026",
        "mermaid_records_version": __version__, "source_file": "0026_5D48EAB8.LOG",
        "source_container": "log", "record_time": "2019-08-07T02:57:30.000000Z",
        "log_epoch_time": "1565146650", "subsystem": "MAIN", "code": "498",
        "message": "Vbat 14681mV (min 13967mV)", "source_line_number": 1,
        "battery_record_kind": "vbat_summary", "voltage_mv": 14681,
        "current_ua": None, "minimum_voltage_mv": 13967,
        "raw_line": "1565146650:[MAIN  ,498]Vbat 14681mV (min 13967mV)",
    }
    assert battery_records[0]["source_id"] == f"sha256:{battery_records[0]['source_sha256']}"
    assert iridium_records[0]["source_file"] == "0026_5D48EAB8.LOG"
    assert iridium_records[0]["session_kind"] == "event_sequence"
    assert iridium_records[0]["iridium_events"][0]["message"] == "7 cmd(s) received"
    assert iridium_records[0]["iridium_events"][0]["received_command_count"] == 7
    assert unclassified_records == []
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_keeps_unclassified_disjoint_from_all_log_families(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_all_families.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[UPLOAD,0248]Upload data files...",
                '1700000001:[UPLOAD,0231]"0100/AAAA0001.MER" uploaded at 83bytes/s',
                "1700000002:[PRESS ,0038]P+20179mbar,T+32767mdegC",
                "1700000003:[MONITR,0461]battery 14685mV,   12688uA",
                "1700000004:[MRMAID,0002]acq started",
                "1700000005:[MRMAID,0583]ascent request accepted",
                "1700000006:[SURF  ,0022]GPS fix...",
                "1700000007:[PUMP  ,0016]pump during 30000ms",
                "1700000008:[SURF  ,0071]<WARN>timeout",
                "1700000009:[MAIN  ,0007]raw unmatched line",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)

    assert summary.unclassified_records == 3
    assert summary.iridium_records == 1
    assert summary.pressure_temperature_records == 1
    assert summary.battery_records == 1
    assert summary.acquisition_records == 1
    assert summary.ascent_request_records == 1
    assert summary.gps_records == 1
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_classifies_legacy_pump_and_outflow_lines(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "06_sample.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[PUMP  ,368]during 900000ms",
                "1700000001:[PUMP  ,0378]Outflow calculated : 2711",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)
    pressure_temperature_records = _read_jsonl(
        output_dir / "log_pressure_temperature_records.jsonl"
    )
    battery_records = _read_jsonl(output_dir / "log_battery_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.total_records == 2
    assert summary.pressure_temperature_records == 0
    assert summary.battery_records == 0
    assert summary.ascent_request_records == 0
    assert summary.gps_records == 0
    assert summary.parameter_records == 0
    assert summary.testmode_records == 0
    assert summary.ctd_records == 0
    assert summary.unclassified_records == 2
    assert pressure_temperature_records == []
    assert battery_records == []
    assert [record["message"] for record in unclassified_records] == [
        "during 900000ms",
        "Outflow calculated : 2711",
    ]
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_preserves_old_measurement_population_accounting(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "06_routing.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[PRESS ,0081]P    +0mbar,T-10881mdegC",
                "1700000001:[MONITR,0461]battery 14685mV,   12688uA",
                "1700000002:[PUMP  ,0016]pump during 30000ms",
                "1700000003:[PUMP  ,0378]Outflow calculated : 2711",
                "1700000004:[MAIN  ,0007]New pressure offset: 40mbar",
                "1700000005:[MAIN  ,0007]from +2mbar/s to +4mbar/s",
                "1700000006:[MAIN  ,0007]P +12,T -34,S +56",
                "1700000007:[PUMP  ,0016]need to transfer +6mL (pump during 7185ms)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)
    pressure_temperature_records = _read_jsonl(
        output_dir / "log_pressure_temperature_records.jsonl"
    )
    battery_records = _read_jsonl(output_dir / "log_battery_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.pressure_temperature_records == 1
    assert summary.battery_records == 1
    assert summary.unclassified_records == 6
    assert [record["message"] for record in pressure_temperature_records] == [
        "P    +0mbar,T-10881mdegC"
    ]
    assert [record["message"] for record in battery_records] == [
        "battery 14685mV,   12688uA"
    ]
    assert [record["message"] for record in unclassified_records] == [
        "pump during 30000ms",
        "Outflow calculated : 2711",
        "New pressure offset: 40mbar",
        "from +2mbar/s to +4mbar/s",
        "P +12,T -34,S +56",
        "need to transfer +6mL (pump during 7185ms)",
    ]
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_fails_loudly_on_derived_family_multi_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "0100_ambiguous.LOG"
    log_path.write_text(
        "1700000000:[SURF  ,0022]GPS fix...\n",
        encoding="utf-8",
    )

    original_classifier = normalize_log_module._classify_battery

    def _ambiguous_battery(entry, *, instrument_id: str):
        record = original_classifier(entry, instrument_id=instrument_id)
        if record is not None:
            return record
        return {
            **normalize_log_module._common_log_record_fields(entry, instrument_id=instrument_id),
            "voltage_mv": 14685,
            "current_ua": 12688,
            "raw_line": entry.raw_line,
        }

    monkeypatch.setattr(normalize_log_module, "_classify_battery", _ambiguous_battery)

    with pytest.raises(
        ValueError,
        match="Operational derived-family multi-match: gps, battery",
    ):
        write_log_jsonl_families([log_path], tmp_path / "jsonl")


def test_write_log_jsonl_families_keeps_grouped_ctd_routing_outside_operational_exclusivity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "0100_sbe.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[SBE61 ,0396]P +20122,T +19514,S +34584",
                "1700000001:[PROFIL,0299]    speed_control=10mbar/s",
                "",
            ]
        ),
        encoding="utf-8",
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("Operational exclusivity collector should not run for grouped CTD episodes")

    monkeypatch.setattr(normalize_log_module, "_single_specific_family_match", _fail_if_called)

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)

    ctd_records = _read_jsonl(output_dir / "log_ctd_records.jsonl")
    assert summary.ctd_records == 1
    assert len(ctd_records) == 1
    assert ctd_records[0]["ctd_sample_count"] == 1
    assert ctd_records[0]["ctd_samples"] == [
        {
            "source_line_number": 1,
            "raw_values": {
                "P": "+20122",
                "T": "+19514",
                "S": "+34584",
            },
            "pressure_cbar_tenths": 20122,
            "temperature_mdegc_tenths": 19514,
            "salinity_psu_thousandths": 34584,
        }
    ]


def test_write_log_jsonl_families_parses_ctd_sample_values_from_compact_line(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_ctd.LOG"
    log_path.write_text(
        "\n".join(
            [
                "2023-04-27T02:15:08:[SBE61 ,0396]P +468,T+150471,S38141",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families(
        [log_path],
        output_dir,
        instrument_id="T0100",
        instrument_serial="467.174-T-0100",
    )

    ctd_records = _read_jsonl(output_dir / "log_ctd_records.jsonl")

    assert summary.ctd_records == 1
    assert ctd_records[0]["ctd_sample_count"] == 1
    assert ctd_records[0]["ctd_samples"] == [
        {
            "source_line_number": 1,
            "raw_values": {
                "P": "+468",
                "T": "+150471",
                "S": "38141",
            },
            "pressure_cbar_tenths": 468,
            "temperature_mdegc_tenths": 150471,
            "salinity_psu_thousandths": 38141,
        }
    ]
    assert not (output_dir / "log_sbe_records.jsonl").exists()


def test_write_log_jsonl_families_emits_acquisition_records(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_acq.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[MRMAID,0002]acq started",
                "1700000001:[MRMAID,0003]acq stopped",
                "1700000002:[MRMAID,0184]acq already started",
                "1700000003:[MRMAID,0185]acq already stopped",
                "1700000004:[SURF  ,0022]GPS fix...",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)
    acquisition_records = _read_jsonl(output_dir / "log_acquisition_records.jsonl")
    gps_records = _read_jsonl(output_dir / "log_gps_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.total_records == 5
    assert summary.acquisition_records == 4
    assert summary.ascent_request_records == 0
    assert summary.gps_records == 1
    assert summary.parameter_records == 0
    assert summary.testmode_records == 0
    assert summary.ctd_records == 0
    assert summary.unclassified_records == 0
    assert summary.acquisition_state_counts == {"started": 2, "stopped": 2}
    assert summary.acquisition_evidence_kind_counts == {
        "transition": 2,
        "assertion": 2,
    }

    assert {(record["acquisition_state"], record["acquisition_evidence_kind"]) for record in acquisition_records} == {
        ("started", "transition"),
        ("stopped", "transition"),
        ("started", "assertion"),
        ("stopped", "assertion"),
    }
    assert acquisition_records[0]["record_time"] == "2023-11-14T22:13:20.000000Z"
    assert acquisition_records[0]["log_epoch_time"] == "1700000000"
    assert "time" not in acquisition_records[0]
    assert gps_records[0]["gps_record_kind"] == "fix_attempt"
    assert gps_records[0]["raw_values"] is None
    assert gps_records[0]["record_time"] == "2023-11-14T22:13:24.000000Z"
    assert gps_records[0]["log_epoch_time"] == "1700000004"
    assert "time" not in gps_records[0]
    assert unclassified_records == []
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_emits_ascent_request_records(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_ascent.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[MRMAID,0583]ascent request accepted",
                "1700000001:[MRMAID,0005]ascent request rejected",
                "1700000002:[SURF  ,0022]GPS fix...",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)
    ascent_request_records = _read_jsonl(
        output_dir / "log_ascent_request_records.jsonl"
    )
    gps_records = _read_jsonl(output_dir / "log_gps_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.total_records == 3
    assert summary.ascent_request_records == 2
    assert summary.gps_records == 1
    assert summary.parameter_records == 0
    assert summary.testmode_records == 0
    assert summary.ctd_records == 0
    assert summary.ascent_request_state_counts == {
        "accepted": 1,
        "rejected": 1,
    }
    assert len(ascent_request_records) == 2
    assert {record["ascent_request_state"] for record in ascent_request_records} == {
        "accepted",
        "rejected",
    }
    assert ascent_request_records[0]["record_time"] == "2023-11-14T22:13:20.000000Z"
    assert ascent_request_records[0]["log_epoch_time"] == "1700000000"
    assert "time" not in ascent_request_records[0]
    assert gps_records[0]["gps_record_kind"] == "fix_attempt"
    assert unclassified_records == []
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_groups_parameter_block_into_one_episode(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_params.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[MAIN  ,0593]internal pressure 85448Pa",
                "1700000001:    bypass 20000ms 120000ms (10000ms 200000ms stored)",
                "1700000001:    valve 60000ms 12750 (60000ms 12750 stored)",
                "1700000001:    stage[0] 150000mbar (+/-5000mbar) 60000s (<60000s)",
                "1700000001:    stage[1] 150000mbar (+/-5000mbar) 648000s (<708000s)",
                "1700000002:[MAIN  ,0621]turn off bluetooth",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    malformed_log_lines: list[dict[str, object]] = []
    summary = write_log_jsonl_families(
        [log_path],
        output_dir,
        run_id="run-1",
        malformed_log_lines=malformed_log_lines,
    )
    parameter_records = _read_jsonl(output_dir / "log_parameter_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.total_records == 3
    assert summary.parameter_records == 1
    assert summary.pressure_temperature_records == 1
    assert summary.unclassified_records == 1
    assert len(parameter_records) == 1
    assert [record["message"] for record in unclassified_records] == [
        "turn off bluetooth",
    ]
    assert malformed_log_lines == []

    assert {
        key: value
        for key, value in parameter_records[0].items()
        if key not in {"source_id", "source_sha256"}
    } == {
        "instrument_id": "0100",
        "instrument_serial": "0100",
        "mermaid_records_version": __version__,
        "source_file": log_path.name,
        "episode_index": 0,
        "line_start_index": 2,
        "line_end_index": 5,
        "source_line_numbers": [2, 3, 4, 5],
        "start_record_time": "2023-11-14T22:13:21.000000Z",
        "end_record_time": "2023-11-14T22:13:21.000000Z",
        "start_log_epoch_time": "1700000001",
        "end_log_epoch_time": "1700000001",
        "raw_lines": [
            "1700000001:    bypass 20000ms 120000ms (10000ms 200000ms stored)",
            "1700000001:    valve 60000ms 12750 (60000ms 12750 stored)",
            "1700000001:    stage[0] 150000mbar (+/-5000mbar) 60000s (<60000s)",
            "1700000001:    stage[1] 150000mbar (+/-5000mbar) 648000s (<708000s)",
        ],
    }
    assert parameter_records[0]["source_id"] == f"sha256:{parameter_records[0]['source_sha256']}"
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_stops_parameter_episode_at_explicit_boundaries(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_parameter_boundaries.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[MAIN  ,0593]internal pressure 85448Pa",
                "1700000001:    bypass 20000ms 120000ms (10000ms 200000ms stored)",
                "1700000001:    valve 60000ms 12750 (60000ms 12750 stored)",
                "1700000002:[SURF  ,0071]<WARN>timeout",
                "1700000003:    pump 60000ms 30% 10750 80% (60000ms 30% 10750 80% stored)",
                "1700000003:    rate 2mbar/s (2mbar/s stored)",
                "1700000004:*** switching to 0100/NEXT.LOG ***",
                "1700000005:    surface 500mbar (300mbar stored)",
                "1700000005:    ascent 8mbar/s (8mbar/s stored)",
                "1700000006:Command list",
                "1700000007:[MAIN  ,0007]buoy 467.174-T-0100",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    malformed_log_lines: list[dict[str, object]] = []
    summary = write_log_jsonl_families(
        [log_path],
        output_dir,
        run_id="run-2",
        malformed_log_lines=malformed_log_lines,
    )
    parameter_records = _read_jsonl(output_dir / "log_parameter_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.total_records == 7
    assert summary.parameter_records == 3
    assert summary.pressure_temperature_records == 1
    assert summary.unclassified_records == 3
    assert [record["episode_index"] for record in parameter_records] == [0, 1, 2]
    assert [record["line_start_index"] for record in parameter_records] == [2, 5, 8]
    assert [record["line_end_index"] for record in parameter_records] == [3, 6, 9]
    assert [record["raw_lines"] for record in parameter_records] == [
        [
            "1700000001:    bypass 20000ms 120000ms (10000ms 200000ms stored)",
            "1700000001:    valve 60000ms 12750 (60000ms 12750 stored)",
        ],
        [
            "1700000003:    pump 60000ms 30% 10750 80% (60000ms 30% 10750 80% stored)",
            "1700000003:    rate 2mbar/s (2mbar/s stored)",
        ],
        [
            "1700000005:    surface 500mbar (300mbar stored)",
            "1700000005:    ascent 8mbar/s (8mbar/s stored)",
        ],
    ]
    assert [record["message"] for record in unclassified_records] == [
        "<WARN>timeout",
        "*** switching to 0100/NEXT.LOG ***",
        "buoy 467.174-T-0100",
    ]
    rollover_record = next(
        record
        for record in unclassified_records
        if record["message"] == "*** switching to 0100/NEXT.LOG ***"
    )
    assert rollover_record["switched_to_log_file"] == "0100_NEXT.LOG"
    assert rollover_record["source_file"] == log_path.name
    _assert_log_source_line_assignments_exact_once(output_dir)
    assert malformed_log_lines == [
            {
                "run_id": "run-2",
                "instrument_id": "0100",
                "instrument_serial": "0100",
                "source_file": log_path.as_posix(),
            "line_number": 10,
            "raw_line": "1700000006:Command list",
            "error": "line does not match expected LOG pattern",
        },
    ]


def test_write_log_jsonl_families_emits_gps_records(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_gps.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[SURF  ,0022]GPS fix...",
                "1700000001:[SURF  ,0082]N35deg19.262mn, E139deg39.043mn",
                "1700000002:[SURF  ,0082]Latitude : N43deg40.956mn, Longitude :E007deg19.175mn",
                "1700000003:[SURF  ,0084]hdop 0.820, vdop 1.180",
                "1700000004:[MRMAID,0052]$GPSACK:+0,+0,+0,+0,+0,+0,-30;",
                "1700000005:[MRMAID,0052]$GPSOFF:3686327;",
                "1700000006:[MAIN  ,0007]buoy 467.174-T-0100",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)
    gps_records = _read_jsonl(output_dir / "log_gps_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.total_records == 7
    assert summary.gps_records == 6
    assert summary.gps_record_kind_counts == {
        "dop": 1,
        "fix_attempt": 1,
        "fix_position": 2,
        "gps_ack": 1,
        "gps_off": 1,
    }
    assert summary.unclassified_records == 1
    assert len(gps_records) == 6
    assert gps_records[0]["gps_record_kind"] == "fix_attempt"
    assert gps_records[0]["raw_values"] is None
    assert gps_records[1]["raw_values"] == {
        "latitude": "N35deg19.262mn",
        "longitude": "E139deg39.043mn",
    }
    assert gps_records[2]["gps_record_kind"] == "fix_position"
    assert gps_records[2]["raw_values"] == {
        "latitude": "N43deg40.956mn",
        "longitude": "E007deg19.175mn",
    }
    assert gps_records[3]["raw_values"] == {"hdop": "0.820", "vdop": "1.180"}
    assert gps_records[4]["raw_values"] == {"gpsack": "+0,+0,+0,+0,+0,+0,-30"}
    assert gps_records[5]["raw_values"] == {"gpsoff": "3686327"}
    assert gps_records[3]["record_time"] == "2023-11-14T22:13:23.000000Z"
    assert gps_records[3]["log_epoch_time"] == "1700000003"
    assert "time" not in gps_records[3]
    assert [record["message"] for record in unclassified_records] == [
        "buoy 467.174-T-0100"
    ]
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_preserves_negative_epoch_iridium_session(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_negative_epoch_iridium.LOG"
    log_path.write_text(
        "\n".join(
            [
                "-1993752871:[SURF  ,0025]Iridium...",
                "-1993752681:[MRMAID,0645]$GPSACK:-19,-8,+16,+0,+0,+1,-999633;",
                "-1993752671:[MRMAID,0650]$GPSOFF:3686330;",
                "-1993752661:[SURF  ,0366]disconnected after 210s",
                "1681899427:[SURF  ,0394]S23deg29.970mn, W132deg30.444mn",
                '1681899430:[UPLOAD,0231]"0025/1234ABCD.MER" uploaded at 137bytes/s',
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)

    iridium_records = _read_jsonl(output_dir / "log_iridium_records.jsonl")
    gps_records = _read_jsonl(output_dir / "log_gps_records.jsonl")

    assert summary.iridium_records == 2
    assert summary.gps_records == 1
    assert len(iridium_records) == 2

    negative_session = iridium_records[0]
    assert negative_session["session_kind"] == "explicit_session"
    assert negative_session["source_line_numbers"] == [1, 2, 3, 4]
    assert negative_session["start_log_epoch_time"] == "-1993752871"
    assert negative_session["end_log_epoch_time"] == "-1993752661"
    assert negative_session["start_record_time"] == "1906-10-28T03:45:29.000000Z"
    assert negative_session["end_record_time"] == "1906-10-28T03:48:59.000000Z"
    assert [event["log_epoch_time"] for event in negative_session["iridium_events"]] == [
        "-1993752871",
        "-1993752681",
        "-1993752671",
        "-1993752661",
    ]
    assert [event["record_time"] for event in negative_session["iridium_events"]] == [
        "1906-10-28T03:45:29.000000Z",
        "1906-10-28T03:48:39.000000Z",
        "1906-10-28T03:48:49.000000Z",
        "1906-10-28T03:48:59.000000Z",
    ]
    assert negative_session["iridium_events"][1]["message"] == (
        "$GPSACK:-19,-8,+16,+0,+0,+1,-999633;"
    )
    assert negative_session["iridium_events"][2]["message"] == "$GPSOFF:3686330;"

    positive_upload_session = iridium_records[1]
    assert positive_upload_session["session_kind"] == "event_sequence"
    assert positive_upload_session["start_log_epoch_time"] == "1681899430"
    assert positive_upload_session["iridium_events"][0]["referenced_artifact"] == (
        "0025_1234ABCD.MER"
    )

    assert gps_records[0]["log_epoch_time"] == "1681899427"
    assert gps_records[0]["record_time"] == "2023-04-19T10:17:07.000000Z"


def test_incomplete_iridium_session_releases_unrelated_gps_line(tmp_path: Path) -> None:
    log_path = tmp_path / "0100_interrupted_iridium.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[SURF  ,0025]Iridium...",
                "1700000001:[SURF  ,0311]connected in 37s, signal quality 5",
                "1700000002:[SURF  ,0394]S23deg29.970mn, W132deg30.444mn",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    write_log_jsonl_families([log_path], output_dir)

    iridium_records = _read_jsonl(output_dir / "log_iridium_records.jsonl")
    gps_records = _read_jsonl(output_dir / "log_gps_records.jsonl")
    assert iridium_records[0]["source_line_numbers"] == [1, 2]
    assert gps_records[0]["source_line_number"] == 3
    assert gps_records[0]["raw_values"] == {
        "latitude": "S23deg29.970mn",
        "longitude": "W132deg30.444mn",
    }
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_groups_testmode_fixture_session_from_0100_examples(
    tmp_path: Path,
) -> None:
    log_path = FIXTURES_ROOT / "0100_64511916.LOG"
    output_dir = tmp_path / "jsonl"
    malformed_log_lines: list[dict[str, object]] = []

    summary = write_log_jsonl_families(
        [log_path],
        output_dir,
        run_id="run-testmode",
        malformed_log_lines=malformed_log_lines,
    )

    testmode_records = _read_jsonl(output_dir / "log_testmode_records.jsonl")
    ctd_records = _read_jsonl(output_dir / "log_ctd_records.jsonl")

    assert summary.testmode_records == 1
    assert len(testmode_records) == 1
    assert summary.ctd_records >= 1
    assert testmode_records[0]["instrument_id"] == "0100"
    assert testmode_records[0]["source_file"] == log_path.name
    assert testmode_records[0]["episode_index"] == 0
    assert testmode_records[0]["start_log_epoch_time"] == "1683036460"
    assert testmode_records[0]["end_log_epoch_time"] == "1683036824"
    assert testmode_records[0]["raw_lines"][0] == "1683036460:[TESTMD,0053]Enter in test mode? yes/no"
    assert "Command list for MOBY 4000m" in testmode_records[0]["raw_lines"]
    assert "Set params" in testmode_records[0]["raw_lines"]
    assert "1683036482:[SURF  ,0025]Iridium..." in testmode_records[0]["raw_lines"]
    assert testmode_records[0]["raw_lines"][-1] == "1683036824:[TESTMD,0252]0100>"
    assert all("Command list" not in row["raw_line"] for row in malformed_log_lines)
    assert all("Iridium..." not in row["raw_line"] for row in malformed_log_lines)
    assert ctd_records[0]["raw_lines"][0].startswith("1683036452:[SBE   ,0391]Mode changed")


def test_write_log_jsonl_families_groups_ctd_and_profil_fixture_blocks_from_0100_examples(
    tmp_path: Path,
) -> None:
    log_path = FIXTURES_ROOT / "0100_6491453E.LOG"
    output_dir = tmp_path / "jsonl"
    malformed_log_lines: list[dict[str, object]] = []

    summary = write_log_jsonl_families(
        [log_path],
        output_dir,
        run_id="run-ctd",
        malformed_log_lines=malformed_log_lines,
    )

    ctd_records = _read_jsonl(output_dir / "log_ctd_records.jsonl")
    parameter_records = _read_jsonl(output_dir / "log_parameter_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.ctd_records == 6
    assert len(ctd_records) == 6
    assert summary.parameter_records == 0
    assert parameter_records == []
    assert ctd_records[0]["instrument_id"] == "0100"
    assert ctd_records[0]["source_file"] == log_path.name
    assert ctd_records[0]["episode_index"] == 0
    assert ctd_records[0]["start_log_epoch_time"] == "1687246390"
    assert ctd_records[0]["end_log_epoch_time"] == "1687246390"
    assert ctd_records[0]["raw_lines"][0] == "1687246390:[STAGE ,0091]Stage [1] surfacing 43200s (<93600s) SBE61 "
    assert ctd_records[0]["raw_lines"][-1] == "1687246390:[PROFIL,0299]    speed_control=10mbar/s"
    assert any("[PROFIL,0284]" in line for line in ctd_records[0]["raw_lines"])
    assert "turn off bluetooth" in {record["message"] for record in unclassified_records}
    assert all("manual_profil=1" not in row["raw_line"] for row in malformed_log_lines)


def test_write_log_jsonl_families_groups_contiguous_ctd_measurements_from_0100_examples(
    tmp_path: Path,
) -> None:
    log_path = FIXTURES_ROOT / "0100_649FF25E.LOG"
    output_dir = tmp_path / "jsonl"
    malformed_log_lines: list[dict[str, object]] = []

    summary = write_log_jsonl_families(
        [log_path],
        output_dir,
        run_id="run-ctd",
        malformed_log_lines=malformed_log_lines,
    )

    ctd_records = _read_jsonl(output_dir / "log_ctd_records.jsonl")

    assert summary.ctd_records >= 3
    measurement_episode = next(
        record
        for record in ctd_records
        if record["raw_lines"][0] == "1688233527:[SBE   ,0391]Mode changed from UPDATE to START"
    )
    assert measurement_episode["raw_lines"][1] == "1688233527:[SBE   ,0385]Start manual acquisitions"
    assert measurement_episode["raw_lines"][2] == "1688233527:[SBE   ,0391]Mode changed from START to PROFILING"
    assert measurement_episode["raw_lines"][3] == "1688233561:[SBE61 ,0396]P +20122,T +19514,S +34584"
    assert measurement_episode["line_end_index"] == measurement_episode["line_start_index"] + 3
    assert measurement_episode["end_log_epoch_time"] == "1688233561"
    assert all("[SBE61 ,0396]" not in row["raw_line"] for row in malformed_log_lines)


def test_write_log_jsonl_families_groups_iridium_session_events_conservatively(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0700_iridium.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[SURF  ,0025]Iridium...",
                "1700000001:[SURF  ,0311]connected in 37s, signal quality 5",
                "1700000002:[MRMAID,0664]$TRIG:3,1;",
                "1700000003:[MRMAID,0664]$UPLOAD_MAX:150;",
                "1700000004:[SURF  ,0328]10 cmd(s) received",
                "1700000005:[UPLOAD,0248]Upload data files...",
                '1700000006:[UPLOAD,0231]"0070/60B742A0.MER" uploaded at 137bytes/s',
                "1700000007:[MRMAID,0604]1373 bytes in 0026/5D3CDEA0.MER",
                "1700000008:[ZTX   ,0486]peer ask to resume 07/5B6A9B02.LOG from byte 1024",
                "1700000009:[ZTX   ,0472]<ERR>peer ask to resume 0048/607503A2.MER (118847bytes) from byte 4294967294",
                '1700000010:[UPLOAD,9999]<ERR>upload "0026","0026/5DC0FCFC.MER"',
                "1700000011:[SURF  ,0069]transfer interrupted , retry",
                "1700000012:[MAIN  ,0013]2 file(s) uploaded",
                "1700000013:[SURF  ,0366]disconnected after 613s",
                "1700000014:[SURF  ,0226]Go dive (Minimum surface delay expired and no more file to upload)",
                "1700000015:[SURF  ,0056]<WARN>peer mute",
                "1700000016:[MAIN  ,0007]raw unmatched after session",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)

    iridium_records = _read_jsonl(output_dir / "log_iridium_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.iridium_records == 1
    assert len(iridium_records) == 1
    assert iridium_records[0]["session_kind"] == "explicit_session"
    assert iridium_records[0]["source_line_numbers"] == list(range(1, 15))
    events = iridium_records[0]["iridium_events"]
    assert [record["iridium_event_kind"] for record in events] == [
        "session_start",
        "connection",
        "command",
        "command",
        "command_summary",
        "upload_batch",
        "upload_artifact",
        "upload_progress_artifact",
        "upload_resume",
        "upload_resume",
        "upload_error_artifact",
        "upload_retry",
        "upload_session_summary",
        "disconnect",
    ]
    assert events[1]["connection_duration_s"] == 37
    assert events[1]["signal_quality"] == 5
    assert events[2]["command_name"] == "TRIG"
    assert events[2]["command_payload"] == "3,1"
    assert events[3]["command_name"] == "UPLOAD_MAX"
    assert events[3]["command_payload"] == "150"
    assert events[4]["received_command_count"] == 10
    assert events[5]["iridium_event_kind"] == "upload_batch"
    assert "referenced_artifact" not in events[5]
    assert "byte_count" not in events[5]

    assert events[6]["iridium_event_kind"] == "upload_artifact"
    assert events[6]["referenced_artifact"] == "0070_60B742A0.MER"
    assert events[6]["rate_bytes_per_s"] == 137

    assert events[7]["iridium_event_kind"] == "upload_progress_artifact"
    assert events[7]["referenced_artifact"] == "0026_5D3CDEA0.MER"
    assert events[7]["byte_count"] == 1373
    assert "rate_bytes_per_s" not in events[7]

    assert events[8]["iridium_event_kind"] == "upload_resume"
    assert events[8]["referenced_artifact"] == "07_5B6A9B02.LOG"
    assert events[8]["byte_offset"] == 1024
    assert events[8]["artifact_size_bytes"] is None

    assert events[9]["iridium_event_kind"] == "upload_resume"
    assert events[9]["referenced_artifact"] == "0048_607503A2.MER"
    assert events[9]["byte_offset"] == 4294967294
    assert events[9]["artifact_size_bytes"] == 118847
    assert events[9]["message"].startswith("<ERR>peer ask to resume")

    assert events[10]["iridium_event_kind"] == "upload_error_artifact"
    assert events[10]["referenced_artifact"] == "0026_5DC0FCFC.MER"

    assert events[11]["iridium_event_kind"] == "upload_retry"
    assert "referenced_artifact" not in events[11]

    assert events[12]["iridium_event_kind"] == "upload_session_summary"
    assert events[12]["uploaded_file_count"] == 2

    assert events[13]["iridium_event_kind"] == "disconnect"
    assert events[13]["disconnect_duration_s"] == 613

    assert {record["message"] for record in unclassified_records} >= {
        "Go dive (Minimum surface delay expired and no more file to upload)",
        "<WARN>peer mute",
        "raw unmatched after session",
    }


def test_write_log_jsonl_families_groups_wrapped_tagged_iridium_event_lines(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0700_wrapped.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:<ERR>[ZTX   ,472]peer ask to resume 0048/607503A2.MER (118847bytes) from byte 4294967294",
                "1700000001:<WRN>[SURF  ,0069]transfer interrupted , retry",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    summary = write_log_jsonl_families([log_path], output_dir)

    iridium_records = _read_jsonl(output_dir / "log_iridium_records.jsonl")
    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.iridium_records == 1
    assert summary.unclassified_records == 0
    assert len(iridium_records) == 1
    assert iridium_records[0]["session_kind"] == "event_sequence"
    events = iridium_records[0]["iridium_events"]
    assert [record["iridium_event_kind"] for record in events] == [
        "upload_resume",
        "upload_retry",
    ]
    assert events[0]["referenced_artifact"] == "0048_607503A2.MER"
    assert events[0]["artifact_size_bytes"] == 118847
    assert events[0]["byte_offset"] == 4294967294
    assert unclassified_records == []
    _assert_log_source_line_assignments_exact_once(output_dir)


def test_write_log_jsonl_families_routes_wrapped_nonfamily_lines_to_unclassified(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0700_wrapped_unclassified.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:<ERR>[MODEM ,0347]ping error",
                "1700000001:<WRN>[SURF  ,0056]peer mute",
                "1700000002:<WARN>[MAIN  ,0041]mission empty",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    malformed_log_lines: list[dict[str, object]] = []
    summary = write_log_jsonl_families(
        [log_path],
        output_dir,
        run_id="run-wrapped-unclassified",
        malformed_log_lines=malformed_log_lines,
    )

    unclassified_records = _read_jsonl(output_dir / "log_unclassified_records.jsonl")

    assert summary.unclassified_records == 3
    assert summary.iridium_records == 0
    assert malformed_log_lines == []
    assert [record["message"] for record in unclassified_records] == [
        "<ERR>ping error",
        "<WRN>peer mute",
        "<WARN>mission empty",
    ]
    assert [record["severity"] for record in unclassified_records] == [
        "err",
        "warn",
        "warn",
    ]
    assert all(record["unclassified_reason"] == "no_family_match" for record in unclassified_records)


def test_write_log_jsonl_families_keeps_true_unparsable_junk_malformed(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0700_bad.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:<ERR>broken wrapper without subsystem tag",
                "not even timestamped",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "jsonl"
    malformed_log_lines: list[dict[str, object]] = []
    summary = write_log_jsonl_families(
        [log_path],
        output_dir,
        run_id="run-malformed",
        malformed_log_lines=malformed_log_lines,
    )

    assert summary.unclassified_records == 0
    assert [row["raw_line"] for row in malformed_log_lines] == [
        "1700000000:<ERR>broken wrapper without subsystem tag",
        "not even timestamped",
    ]


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    path = _resolve_jsonl_path(path)
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _log_schema_doc_hit_examples() -> dict[str, list[str]]:
    doc_text = (DOCS_ROOT / "log_record_family_schemas.md").read_text(
        encoding="utf-8"
    )
    examples: dict[str, list[str]] = {}
    section_re = re.compile(
        r"^## `(?P<filename>log_[^`]+\.jsonl)`\n"
        r"(?P<body>.*?)(?=^## `log_|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    hits_re = re.compile(r"^Hits:\n\n```text\n(?P<hits>.*?)\n```", re.MULTILINE | re.DOTALL)
    for section_match in section_re.finditer(doc_text):
        filename = section_match.group("filename")
        hits_match = hits_re.search(section_match.group("body"))
        assert hits_match is not None, f"{filename} is missing a Hits block"
        examples[filename] = [
            line
            for line in hits_match.group("hits").splitlines()
            if line.strip()
        ]
    return examples


def _raw_lines_by_output_file(output_dir: Path) -> dict[str, set[str]]:
    raw_lines_by_output: dict[str, set[str]] = {}
    for path in sorted(output_dir.glob("log_*.jsonl")):
        raw_lines: set[str] = set()
        for record in _read_jsonl(path):
            if "raw_line" in record:
                raw_lines.add(str(record["raw_line"]))
            else:
                raw_lines.update(str(line) for line in record.get("raw_lines", []))
        raw_lines_by_output[path.name] = raw_lines
    return raw_lines_by_output


def _assert_log_source_line_assignments_exact_once(output_dir: Path) -> None:
    assignments: dict[tuple[object, object, object], list[str]] = {}
    for path in sorted(output_dir.glob("log_*.jsonl")):
        for record in _read_jsonl(path):
            for source_key in _source_line_keys(record):
                assignments.setdefault(source_key, []).append(path.name)

    duplicates = {
        source_key: families
        for source_key, families in assignments.items()
        if len(families) > 1
    }
    assert duplicates == {}


def _source_line_keys(record: dict[str, object]) -> list[tuple[object, object, object]]:
    if "raw_line" in record:
        return [(record["source_file"], record["source_line_number"], record["raw_line"])]
    if "raw_lines" not in record:
        return []
    return [
        (record["source_file"], line_number, raw_line)
        for line_number, raw_line in zip(
            record["source_line_numbers"],
            record["raw_lines"],
            strict=True,
        )
        if str(raw_line).strip()
    ]


def _resolve_jsonl_path(path: Path) -> Path:
    if path.exists():
        return path
    matches = sorted(path.parent.glob(f"{path.stem}.*{path.suffix}"))
    if len(matches) == 1:
        return matches[0]
    return path
