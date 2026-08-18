# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from mermaid_records import __version__
from mermaid_records.bin2log import Bin2LogConfig
import mermaid_records.normalize_log as normalize_log_module
import mermaid_records.normalize_mer as normalize_mer_module
from mermaid_records.normalize_pipeline import run_normalization_pipeline


def test_stateful_run_writes_per_instrument_outputs_and_manifests(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "452.020-P-06.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "06_first.LOG", "first")
    _write_mer(input_root / "06_first.MER")

    output_root = tmp_path / "output"
    summary = run_normalization_pipeline(input_root, output_dir=output_root)

    instrument_dir = output_root / "452.020-P-06"
    latest = _read_json(instrument_dir / "manifests" / "latest.json")
    run_json = _read_json(instrument_dir / latest["run_manifest"])
    source_state = _read_json(instrument_dir / latest["source_state_manifest"])
    diff_rows = _jsonl_lines(instrument_dir / "manifests" / "runs" / latest["run_id"] / "input_file_diffs.jsonl")

    assert summary.mode == "stateful"
    assert [item.instrument_id for item in summary.processed_instruments] == ["P0006"]
    unclassified_path = _record_path(instrument_dir, "log_unclassified_records.jsonl")
    environment_path = _record_path(instrument_dir, "mer_environment_records.jsonl")
    assert unclassified_path.name == "log_unclassified_records.452.020-P-06.jsonl"
    assert environment_path.name == "mer_environment_records.452.020-P-06.jsonl"
    assert unclassified_path.exists()
    assert not _record_path(instrument_dir, "log_operational_records.jsonl").exists()
    assert environment_path.exists()
    assert run_json["status"] == "success"
    assert run_json["instrument_serial"] == "452.020-P-06"
    assert latest["instrument_serial"] == "452.020-P-06"
    assert source_state["input_root"] == input_root.as_posix()
    assert source_state["instrument_serial"] == "452.020-P-06"
    assert source_state["normalization_version"] == __version__
    assert {item["source_kind"] for item in source_state["raw_sources"]} == {"log", "mer"}
    assert {item["change_kind"] for item in diff_rows} == {"new"}
    assert all(item["run_id"] == latest["run_id"] for item in diff_rows)
    assert {item["source_file"] for item in diff_rows} == {"06_first.LOG", "06_first.MER"}
    assert {item["instrument_id"] for item in diff_rows} == {"P0006"}
    assert {item["instrument_serial"] for item in diff_rows} == {"452.020-P-06"}
    unclassified_rows = _jsonl_lines(instrument_dir / "log_unclassified_records.jsonl")
    environment_rows = _jsonl_lines(instrument_dir / "mer_environment_records.jsonl")
    all_record_rows = [
        row
        for path in sorted(instrument_dir.glob("*.jsonl"))
        for row in _jsonl_lines(path)
    ]
    assert unclassified_rows[0]["instrument_id"] == "P0006"
    assert unclassified_rows[0]["source_file"] == "06_first.LOG"
    assert environment_rows[0]["source_file"] == "06_first.MER"
    assert unclassified_rows[0]["instrument_serial"] == "452.020-P-06"
    assert unclassified_rows[0]["instrument_id"] != unclassified_rows[0]["instrument_serial"]
    assert all(row["instrument_serial"] == "452.020-P-06" for row in all_record_rows)
    assert list(diff_rows[0]) == [
        "source_file",
        "source_kind",
        "instrument_id",
        "instrument_serial",
        "previous_exists",
        "current_exists",
        "previous_size_bytes",
        "current_size_bytes",
        "previous_hash",
        "current_hash",
        "change_kind",
        "decoder_state_changed",
        "run_id",
    ]


def test_stateful_mode_ignores_out_of_scope_file_types(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")
    (input_root / "0100_first.S61").write_bytes(b"ignored")
    (input_root / "0100_first.S41").write_bytes(b"ignored")
    (input_root / "0100_first.RBR").write_bytes(b"ignored")

    output_root = tmp_path / "output"
    summary = run_normalization_pipeline(input_root, output_dir=output_root)

    instrument_dir = output_root / "467.174-T-0100"
    source_state = _read_json(instrument_dir / "manifests" / "runs" / _read_json(instrument_dir / "manifests" / "latest.json")["run_id"] / "source_state.json")

    assert [item.instrument_id for item in summary.processed_instruments] == ["T0100"]
    assert {item["source_kind"] for item in source_state["raw_sources"]} == {"log"}
    assert _record_path(instrument_dir, "log_unclassified_records.jsonl").exists()
    assert not _record_path(instrument_dir, "log_operational_records.jsonl").exists()
    assert _record_path(instrument_dir, "mer_environment_records.jsonl").exists()
    assert _record_path(instrument_dir, "mer_environment_records.jsonl").read_text(encoding="utf-8") == ""
    assert (instrument_dir / "state" / "pruned_records.jsonl").exists()


def test_stateful_append_path_appends_only_new_files(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)
    _write_log(input_root / "0100_second.LOG", "second")
    summary = run_normalization_pipeline(input_root, output_dir=output_root)

    instrument_summary = summary.processed_instruments[0]
    unclassified_lines = _jsonl_lines(output_root / "467.174-T-0100" / "log_unclassified_records.jsonl")

    assert instrument_summary.log_action == "append"
    assert len(unclassified_lines) == 2
    assert unclassified_lines[0]["message"] == "first"
    assert unclassified_lines[1]["message"] == "second"


def test_stateful_second_run_with_no_raw_source_changes_is_noop(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")
    _write_mer(input_root / "0100_first.MER")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)
    summary = run_normalization_pipeline(input_root, output_dir=output_root)

    instrument_summary = summary.processed_instruments[0]
    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    diff_rows = _jsonl_lines(
        output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"] / "input_file_diffs.jsonl"
    )

    assert instrument_summary.log_action == "noop"
    assert instrument_summary.mer_action == "noop"
    assert {row["change_kind"] for row in diff_rows} == {"unchanged"}


def test_stateful_immediate_reruns_create_distinct_manifest_run_directories(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)
    first_latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")

    run_normalization_pipeline(input_root, output_dir=output_root)
    second_latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")

    runs_root = output_root / "467.174-T-0100" / "manifests" / "runs"
    run_dirs = sorted(path.name for path in runs_root.iterdir() if path.is_dir())

    assert first_latest["run_id"] != second_latest["run_id"]
    assert len(run_dirs) == 2
    assert run_dirs == sorted([first_latest["run_id"], second_latest["run_id"]])
    assert all(len(run_id.rsplit("-", 1)[-1]) == 6 for run_id in run_dirs)
    assert all(run_id.count("-") == 3 for run_id in run_dirs)
    assert all("T" in run_id and ":" in run_id and "Z-" in run_id for run_id in run_dirs)


def test_stateful_force_rewrite_rewrites_unchanged_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")
    _write_mer(input_root / "0100_first.MER")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)
    summary = run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        force_rewrite=True,
    )

    instrument_summary = summary.processed_instruments[0]
    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    diff_rows = _jsonl_lines(
        output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"] / "input_file_diffs.jsonl"
    )
    unclassified_rows = _jsonl_lines(output_root / "467.174-T-0100" / "log_unclassified_records.jsonl")
    event_rows = _jsonl_lines(output_root / "467.174-T-0100" / "mer_event_records.jsonl")

    assert instrument_summary.log_action == "rewrite"
    assert instrument_summary.mer_action == "rewrite"
    assert {row["change_kind"] for row in diff_rows} == {"unchanged"}
    assert [row["message"] for row in unclassified_rows] == ["first"]
    assert len(event_rows) == 1


def test_stateful_force_rewrite_removes_stale_package_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")
    _write_mer(input_root / "0100_first.MER")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)
    instrument_dir = output_root / "467.174-T-0100"
    (instrument_dir / "log_measurement_records.jsonl").write_text('{"stale": true}\n', encoding="utf-8")
    (instrument_dir / "keep.me").write_text("preserve", encoding="utf-8")
    (instrument_dir / "manifests" / "stale.txt").write_text("old manifest", encoding="utf-8")
    (instrument_dir / "state" / "stale.txt").write_text("old state", encoding="utf-8")

    run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        force_rewrite=True,
    )

    latest = _read_json(instrument_dir / "manifests" / "latest.json")

    assert not (instrument_dir / "log_measurement_records.jsonl").exists()
    assert (instrument_dir / "keep.me").read_text(encoding="utf-8") == "preserve"
    assert not (instrument_dir / "manifests" / "stale.txt").exists()
    assert not (instrument_dir / "state" / "stale.txt").exists()
    assert (instrument_dir / "manifests" / "runs" / latest["run_id"]).is_dir()
    assert (instrument_dir / "state" / "pruned_records.jsonl").exists()


def test_targeted_force_rewrite_does_not_touch_other_instrument(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    target_root = input_root / "452.020-P-0030"
    other_root = input_root / "467.174-T-0200"
    target_root.mkdir(parents=True)
    other_root.mkdir(parents=True)
    _write_log(target_root / "0030_first.LOG", "target")
    _write_log(other_root / "0200_first.LOG", "other")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)

    target_output = output_root / "452.020-P-0030"
    other_output = output_root / "467.174-T-0200"
    (target_output / "log_measurement_records.jsonl").write_text(
        '{"stale": true}\n',
        encoding="utf-8",
    )
    other_stale = other_output / "log_measurement_records.jsonl"
    other_stale.write_text('{"preserve": true}\n', encoding="utf-8")
    other_latest_before = (other_output / "manifests" / "latest.json").read_bytes()

    summary = run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        instrument_serial="452.020-P-0030",
        force_rewrite=True,
    )

    assert [item.instrument_serial for item in summary.processed_instruments] == [
        "452.020-P-0030"
    ]
    assert not (target_output / "log_measurement_records.jsonl").exists()
    assert other_stale.read_text(encoding="utf-8") == '{"preserve": true}\n'
    assert (other_output / "manifests" / "latest.json").read_bytes() == other_latest_before


def test_stateful_append_path_appends_only_new_mer_files(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_mer(input_root / "0100_first.MER")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)
    _write_second_mer(input_root / "0100_second.MER")
    summary = run_normalization_pipeline(input_root, output_dir=output_root)

    instrument_summary = summary.processed_instruments[0]
    event_lines = _jsonl_lines(output_root / "467.174-T-0100" / "mer_event_records.jsonl")

    assert instrument_summary.mer_action == "append"
    assert len(event_lines) == 2
    assert event_lines[0]["fname"] == "2024-02-07T22_47_22.000000"
    assert event_lines[1]["fname"] == "2024-02-08T01_02_03.000000"


def test_stateful_rewrite_and_prune_on_changed_or_removed_source(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    log_path = input_root / "0100_first.LOG"
    _write_log(log_path, "first")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)
    _write_log(log_path, "first changed")
    summary = run_normalization_pipeline(input_root, output_dir=output_root)
    lines_after_change = _jsonl_lines(output_root / "467.174-T-0100" / "log_unclassified_records.jsonl")

    assert summary.processed_instruments[0].log_action == "rewrite"
    assert len(lines_after_change) == 1
    assert lines_after_change[0]["message"] == "first changed"

    log_path.unlink()
    summary = run_normalization_pipeline(input_root, output_dir=output_root)
    pruned_lines = _jsonl_lines(output_root / "467.174-T-0100" / "state" / "pruned_records.jsonl")

    assert summary.processed_instruments[0].log_action == "rewrite"
    assert _record_path(output_root / "467.174-T-0100", "log_unclassified_records.jsonl").exists()
    assert _record_path(output_root / "467.174-T-0100", "log_unclassified_records.jsonl").read_text(encoding="utf-8") == ""
    assert pruned_lines[-1]["source_file"] == log_path.as_posix()
    assert pruned_lines[-1]["source_kind"] == "log"


def test_stateful_rewrite_and_prune_on_changed_or_removed_mer_source(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    mer_path = input_root / "0100_first.MER"
    _write_mer(mer_path)
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)
    _write_second_mer(mer_path)
    summary = run_normalization_pipeline(input_root, output_dir=output_root)
    event_lines_after_change = _jsonl_lines(output_root / "467.174-T-0100" / "mer_event_records.jsonl")

    assert summary.processed_instruments[0].mer_action == "rewrite"
    assert len(event_lines_after_change) == 1
    assert event_lines_after_change[0]["fname"] == "2024-02-08T01_02_03.000000"

    mer_path.unlink()
    summary = run_normalization_pipeline(input_root, output_dir=output_root)
    pruned_lines = _jsonl_lines(output_root / "467.174-T-0100" / "state" / "pruned_records.jsonl")

    assert summary.processed_instruments[0].mer_action == "rewrite"
    assert _record_path(output_root / "467.174-T-0100", "mer_event_records.jsonl").exists()
    assert _record_path(output_root / "467.174-T-0100", "mer_event_records.jsonl").read_text(encoding="utf-8") == ""
    assert pruned_lines[-1]["source_file"] == mer_path.as_posix()
    assert pruned_lines[-1]["source_kind"] == "mer"


def test_decoder_state_invalidates_only_bin_dependent_instrument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "0100_first.BIN").write_bytes(b"raw-bin")
    _write_log(input_root / "0200_first.LOG", "plain log")

    mermaid_root = tmp_path / "mermaid_root"
    database_root = mermaid_root / "database"
    database_root.mkdir(parents=True)
    (database_root / "Databases.json").write_text("[]\n", encoding="utf-8")
    monkeypatch.setenv("MERMAID", str(mermaid_root))

    decoder_a = _write_decoder(tmp_path / "decoder_a.py", "decoded a")
    decoder_b = _write_decoder(tmp_path / "decoder_b.py", "decoded b")
    output_root = tmp_path / "output"

    run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        config=Bin2LogConfig(
            python_executable=Path(sys.executable),
            decoder_script=decoder_a,
        ),
    )
    log_only_before = _record_path(output_root / "0200", "log_unclassified_records.jsonl").read_text(encoding="utf-8")

    summary = run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        config=Bin2LogConfig(
            python_executable=Path(sys.executable),
            decoder_script=decoder_b,
        ),
    )

    by_instrument = {item.instrument_id: item for item in summary.processed_instruments}
    bin_lines = _jsonl_lines(output_root / "0100" / "log_unclassified_records.jsonl")
    log_only_after = _record_path(output_root / "0200", "log_unclassified_records.jsonl").read_text(encoding="utf-8")

    assert by_instrument["0100"].decoder_state_invalidated is True
    assert by_instrument["0100"].log_action == "rewrite"
    assert by_instrument["0200"].decoder_state_invalidated is False
    assert by_instrument["0200"].log_action == "noop"
    assert bin_lines[0]["message"] == "decoded b"
    assert log_only_before == log_only_after
    latest = _read_json(output_root / "0100" / "manifests" / "latest.json")
    diff_rows = _jsonl_lines(output_root / "0100" / "manifests" / "runs" / latest["run_id"] / "input_file_diffs.jsonl")
    assert diff_rows[0]["change_kind"] == "unchanged"
    assert diff_rows[0]["decoder_state_changed"] is True
    assert diff_rows[0]["source_file"] == "0100_first.BIN"


def test_missing_decoder_fingerprint_conservatively_rewrites_bin_outputs(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "0100_first.BIN").write_bytes(b"raw-bin")
    decoder = _write_decoder(tmp_path / "decoder.py", "decoded")
    config = Bin2LogConfig(
        python_executable=Path(sys.executable),
        decoder_script=decoder,
    )
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root, config=config)
    summary = run_normalization_pipeline(input_root, output_dir=output_root, config=config)

    assert summary.processed_instruments[0].decoder_state_invalidated is True
    assert summary.processed_instruments[0].log_action == "rewrite"


def test_stateful_nonfinite_mer_value_is_quarantined_with_raw_text(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "0100_bad.MER").write_text(
        "<ENVIRONMENT>\n<TRUE_SAMPLE_FREQ FS_Hz=NaN />\n</ENVIRONMENT>\n"
        "<PARAMETERS>\n</PARAMETERS>\n",
        encoding="ascii",
    )
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)

    instrument_dir = output_root / "0100"
    latest = _read_json(instrument_dir / "manifests" / "latest.json")
    quarantined = _jsonl_lines(
        instrument_dir / "manifests" / "runs" / latest["run_id"] / "malformed_mer_blocks.jsonl"
    )
    assert quarantined[0]["raw_block"] == "<TRUE_SAMPLE_FREQ FS_Hz=NaN />"
    assert "Non-finite numeric value" in quarantined[0]["error"]


def test_stateless_nonfinite_mer_value_fails_closed(tmp_path: Path) -> None:
    mer_path = tmp_path / "0100_bad.MER"
    mer_path.write_text(
        "<ENVIRONMENT>\n<TRUE_SAMPLE_FREQ FS_Hz=Infinity />\n</ENVIRONMENT>\n"
        "<PARAMETERS>\n</PARAMETERS>\n",
        encoding="ascii",
    )

    with pytest.raises(ValueError, match="Non-finite numeric value"):
        run_normalization_pipeline(output_dir=tmp_path / "output", input_files=[mer_path])


def test_same_stem_bin_shadows_native_log_for_state_and_normalization(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    bin_path = input_root / "0100_first.BIN"
    bin_path.write_bytes(b"raw-bin")
    shadowed_log = input_root / "0100_first.LOG"
    _write_log(shadowed_log, "native log must not be normalized")
    _write_log(input_root / "0100_log_only.LOG", "native log-only source")

    decoder = _write_decoder(tmp_path / "decoder.py", "authoritative decoded log")
    config = Bin2LogConfig(
        python_executable=Path(sys.executable),
        decoder_script=decoder,
        environment_fingerprint="test-decoder-v1",
    )
    output_root = tmp_path / "output"

    first_summary = run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        config=config,
    )

    instrument_dir = output_root / "0100"
    rows = _jsonl_lines(instrument_dir / "log_unclassified_records.jsonl")
    latest = _read_json(instrument_dir / "manifests" / "latest.json")
    source_state = _read_json(instrument_dir / latest["source_state_manifest"])

    assert first_summary.processed_instruments[0].bin_count == 1
    assert first_summary.processed_instruments[0].log_count == 1
    assert {row["message"] for row in rows} == {
        "authoritative decoded log",
        "native log-only source",
    }
    assert {
        row["message"]: row["source_file"]
        for row in rows
    } == {
        "authoritative decoded log": "0100_first.BIN",
        "native log-only source": "0100_log_only.LOG",
    }
    assert {Path(row["source_file"]).name for row in source_state["raw_sources"]} == {
        "0100_first.BIN",
        "0100_log_only.LOG",
    }

    _write_log(shadowed_log, "changed native log must remain ignored")
    second_summary = run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        config=config,
    )

    assert second_summary.processed_instruments[0].log_action == "noop"
    assert _jsonl_lines(instrument_dir / "log_unclassified_records.jsonl") == rows


def test_same_stem_bin_shadows_native_log_in_stateless_mode(
    tmp_path: Path,
) -> None:
    bin_path = tmp_path / "0100_first.BIN"
    bin_path.write_bytes(b"raw-bin")
    native_log = tmp_path / "0100_first.LOG"
    _write_log(native_log, "native log must not be normalized")
    decoder = _write_decoder(tmp_path / "decoder.py", "authoritative decoded log")
    output_root = tmp_path / "output"

    summary = run_normalization_pipeline(
        output_dir=output_root,
        input_files=[native_log, bin_path],
        config=Bin2LogConfig(
            python_executable=Path(sys.executable),
            decoder_script=decoder,
        ),
    )

    rows = _jsonl_lines(output_root / "0100" / "log_unclassified_records.jsonl")

    assert summary.processed_instruments[0].bin_count == 1
    assert summary.processed_instruments[0].log_count == 0
    assert [row["message"] for row in rows] == ["authoritative decoded log"]
    assert [row["source_file"] for row in rows] == ["0100_first.BIN"]


def test_decoder_state_tolerates_database_file_removed_after_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "0100_first.BIN").write_bytes(b"raw-bin")

    mermaid_root = tmp_path / "mermaid_root"
    database_root = mermaid_root / "database"
    database_root.mkdir(parents=True)
    (database_root / "Databases.json").write_text("[]\n", encoding="utf-8")
    missing_json = database_root / "DatabaseV1_0.json"
    missing_json.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("MERMAID", str(mermaid_root))

    decoder = _write_decoder(tmp_path / "decoder.py", "decoded")
    output_root = tmp_path / "output"

    import mermaid_records.manifest as manifest_module

    original_database_files = manifest_module._database_files

    def _database_files_then_remove(database_root: Path | None) -> list[Path]:
        paths = original_database_files(database_root)
        if missing_json.exists():
            missing_json.unlink()
        return paths

    monkeypatch.setattr(manifest_module, "_database_files", _database_files_then_remove)

    summary = run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        config=Bin2LogConfig(
            python_executable=Path(sys.executable),
            decoder_script=decoder,
        ),
    )

    assert summary.metrics.bin_files_decoded == 1
    latest = _read_json(output_root / "0100" / "manifests" / "latest.json")
    source_state = _read_json(output_root / "0100" / latest["source_state_manifest"])
    assert source_state["decoder_state"]["database_bundle_hash"] is not None


def test_stateful_non_bin_rerun_does_not_inherit_stale_preflight_status(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    bin_path = input_root / "0100_first.BIN"
    bin_path.write_bytes(b"raw-bin")

    decoder = _write_decoder(tmp_path / "decoder.py", "decoded")
    output_root = tmp_path / "output"

    run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        config=Bin2LogConfig(
            python_executable=Path(sys.executable),
            decoder_script=decoder,
        ),
    )

    instrument_dir = output_root / "467.174-T-0100"
    first_latest = _read_json(instrument_dir / "manifests" / "latest.json")

    assert first_latest["preflight_status"] == (
        f"manifests/runs/{first_latest['run_id']}/preflight_status.json"
    )
    assert (instrument_dir / "preflight_status.json").exists()
    assert (instrument_dir / first_latest["preflight_status"]).exists()

    bin_path.unlink()
    _write_log(input_root / "0100_second.LOG", "second")

    run_normalization_pipeline(input_root, output_dir=output_root)

    second_latest = _read_json(instrument_dir / "manifests" / "latest.json")
    second_run_dir = instrument_dir / "manifests" / "runs" / second_latest["run_id"]

    assert not (instrument_dir / "preflight_status.json").exists()
    assert "preflight_status" not in second_latest
    assert not (second_run_dir / "preflight_status.json").exists()


def test_stateless_mode_isolated_and_rejects_existing_manifests(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    log_path = input_root / "0100_first.LOG"
    _write_log(log_path, "first")

    output_root = tmp_path / "output"
    summary = run_normalization_pipeline(
        output_dir=output_root,
        input_files=[log_path],
    )

    assert summary.mode == "stateless"
    assert not (output_root / "0100" / "manifests").exists()
    assert _record_path(output_root / "0100", "log_unclassified_records.jsonl").exists()
    assert not _record_path(output_root / "0100", "log_operational_records.jsonl").exists()
    assert _record_path(output_root / "0100", "mer_environment_records.jsonl").exists()
    assert _record_path(output_root / "0100", "mer_environment_records.jsonl").read_text(encoding="utf-8") == ""

    manifests_dir = output_root / "0200" / "manifests"
    manifests_dir.mkdir(parents=True)
    (manifests_dir / "latest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="already contains manifests"):
        run_normalization_pipeline(
            output_dir=output_root,
            input_files=[log_path],
        )


def test_stateless_mode_rejects_unsupported_explicit_input_files(tmp_path: Path) -> None:
    unsupported_path = tmp_path / "0100_profile.S61"
    unsupported_path.write_bytes(b"")

    with pytest.raises(ValueError, match="Unsupported input file type for the current normalization contract"):
        run_normalization_pipeline(
            output_dir=tmp_path / "output",
            input_files=[unsupported_path],
        )


def test_stateless_dry_run_is_side_effect_free(tmp_path: Path) -> None:
    log_path = tmp_path / "0100_first.LOG"
    _write_log(log_path, "first")
    output_root = tmp_path / "output"

    summary = run_normalization_pipeline(
        output_dir=output_root,
        input_files=[log_path],
        dry_run=True,
    )
    payload = summary.to_dict()

    assert summary.mode == "stateless"
    assert payload["instruments"][0]["families"]["log"]["action"] == "rewrite"
    assert payload["instruments"][0]["counts"]["new"] == 1
    assert not output_root.exists()


def test_stateless_rerun_rewrites_existing_outputs_without_duplication(tmp_path: Path) -> None:
    first_log = tmp_path / "0100_first.LOG"
    _write_log(first_log, "first")
    output_root = tmp_path / "output"

    run_normalization_pipeline(output_dir=output_root, input_files=[first_log])
    summary = run_normalization_pipeline(output_dir=output_root, input_files=[first_log])

    unclassified_rows = _jsonl_lines(output_root / "0100" / "log_unclassified_records.jsonl")

    assert summary.processed_instruments[0].log_action == "rewrite"
    assert [row["message"] for row in unclassified_rows] == ["first"]


def test_stateless_rerun_rewrites_existing_outputs_without_force_rewrite(tmp_path: Path) -> None:
    first_log = tmp_path / "0100_first.LOG"
    second_log = tmp_path / "0100_second.LOG"
    _write_log(first_log, "first")
    _write_log(second_log, "second")
    output_root = tmp_path / "output"

    run_normalization_pipeline(output_dir=output_root, input_files=[first_log])
    summary = run_normalization_pipeline(output_dir=output_root, input_files=[second_log])

    unclassified_rows = _jsonl_lines(output_root / "0100" / "log_unclassified_records.jsonl")

    assert summary.processed_instruments[0].log_action == "rewrite"
    assert [row["message"] for row in unclassified_rows] == ["second"]


def test_stateless_force_rewrite_removes_stale_package_outputs(tmp_path: Path) -> None:
    log_path = tmp_path / "0100_first.LOG"
    _write_log(log_path, "first")
    output_root = tmp_path / "output"

    run_normalization_pipeline(output_dir=output_root, input_files=[log_path])
    instrument_dir = output_root / "0100"
    (instrument_dir / "log_measurement_records.jsonl").write_text('{"stale": true}\n', encoding="utf-8")
    (instrument_dir / "keep.me").write_text("preserve", encoding="utf-8")
    (instrument_dir / "state").mkdir()
    (instrument_dir / "state" / "stale.txt").write_text("old state", encoding="utf-8")

    run_normalization_pipeline(
        output_dir=output_root,
        input_files=[log_path],
        force_rewrite=True,
    )

    assert not (instrument_dir / "log_measurement_records.jsonl").exists()
    assert (instrument_dir / "keep.me").read_text(encoding="utf-8") == "preserve"
    assert not (instrument_dir / "state" / "stale.txt").exists()
    assert not (instrument_dir / "manifests").exists()


def test_dry_run_force_rewrite_reports_rewrite_actions_without_writing(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")
    _write_mer(input_root / "0100_first.MER")
    output_root = tmp_path / "output"

    summary = run_normalization_pipeline(
        input_root,
        output_dir=output_root,
        dry_run=True,
        force_rewrite=True,
    )
    payload = summary.to_dict()

    assert payload["instruments"][0]["families"]["log"]["action"] == "rewrite"
    assert payload["instruments"][0]["families"]["mer"]["action"] == "rewrite"
    assert not output_root.exists()


def test_dry_run_is_side_effect_free_and_reports_file_diffs(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    log_path = input_root / "0100_first.LOG"
    mer_path = input_root / "0100_first.MER"
    _write_log(log_path, "first")
    _write_mer(mer_path)

    output_root = tmp_path / "output"
    run_normalization_pipeline(input_root, output_dir=output_root)

    _write_log(log_path, "first changed")
    _write_log(input_root / "0100_second.LOG", "second")
    mer_path.unlink()

    latest_before = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    runs_root = output_root / "467.174-T-0100" / "manifests" / "runs"
    run_ids_before = sorted(path.name for path in runs_root.iterdir() if path.is_dir())
    pruned_records_path = output_root / "467.174-T-0100" / "state" / "pruned_records.jsonl"
    pruned_records_before = pruned_records_path.read_text(encoding="utf-8")
    summary = run_normalization_pipeline(input_root, output_dir=output_root, dry_run=True)
    payload = summary.to_dict()

    assert summary.mode == "stateful"
    assert _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json") == latest_before
    assert sorted(path.name for path in runs_root.iterdir() if path.is_dir()) == run_ids_before
    assert pruned_records_path.read_text(encoding="utf-8") == pruned_records_before
    instrument_payload = payload["instruments"][0]
    assert instrument_payload["counts"] == {
        "total": 3,
        "new": 1,
        "changed": 1,
        "removed": 1,
        "unchanged": 0,
    }
    assert instrument_payload["families"]["log"]["action"] == "rewrite"
    assert instrument_payload["families"]["mer"]["action"] == "rewrite"
    assert {row["change_kind"] for row in instrument_payload["families"]["log"]["file_diffs"]} == {"new", "changed"}
    assert {row["change_kind"] for row in instrument_payload["families"]["mer"]["file_diffs"]} == {"removed"}


def test_bin_decode_failure_reports_offending_source_paths(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    bin_path = input_root / "0100_first.BIN"
    bin_path.write_bytes(b"raw-bin")
    decoder = tmp_path / "decoder_fail.py"
    decoder.write_text(
        """
def database_update(_arg):
    print("Update Databases")

def concatenate_files(path):
    return [path]

def concatenate_rbr_files(path):
    return [path]

def decrypt_all(path):
    raise RuntimeError("decoder boom")
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Error while decoding BIN source\\(s\\)") as excinfo:
        run_normalization_pipeline(
            input_root,
            output_dir=tmp_path / "output",
            config=Bin2LogConfig(
                python_executable=Path(sys.executable),
                decoder_script=decoder,
            ),
        )

    assert bin_path.as_posix() in str(excinfo.value)


def test_stateful_logs_malformed_log_lines_and_continues(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    log_path = input_root / "0100_malformed.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[MAIN  ,0007]first",
                "[DIVING,15",
                "1700000001:[MAIN  ,0007]second",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_root = tmp_path / "output"
    run_normalization_pipeline(input_root, output_dir=output_root)

    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    run_dir = output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"]
    malformed_rows = _jsonl_lines(run_dir / "malformed_log_lines.jsonl")
    unclassified_rows = _jsonl_lines(output_root / "467.174-T-0100" / "log_unclassified_records.jsonl")

    assert [row["message"] for row in unclassified_rows] == ["first", "second"]
    assert [row["source_file"] for row in unclassified_rows] == ["0100_malformed.LOG", "0100_malformed.LOG"]
    assert malformed_rows == [
        {
            "error": "line does not match expected LOG pattern",
            "instrument_id": "T0100",
            "instrument_serial": "467.174-T-0100",
            "line_number": 2,
            "raw_line": "[DIVING,15",
            "run_id": latest["run_id"],
            "source_file": log_path.as_posix(),
        }
    ]
    assert _jsonl_lines(run_dir / "skipped_log_files.jsonl") == []


def test_stateful_records_skipped_log_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    log_path = input_root / "0100_broken.LOG"
    _write_log(log_path, "first")

    def _raise_unreadable(path: Path, *, on_malformed_line=None):
        raise OSError("simulated unreadable log")
        yield  # pragma: no cover

    monkeypatch.setattr(normalize_log_module, "_iter_log_source_units", _raise_unreadable)

    output_root = tmp_path / "output"
    run_normalization_pipeline(input_root, output_dir=output_root)

    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    run_dir = output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"]
    skipped_rows = _jsonl_lines(run_dir / "skipped_log_files.jsonl")

    assert skipped_rows == [
        {
            "error": "simulated unreadable log",
            "instrument_id": "T0100",
            "instrument_serial": "467.174-T-0100",
            "run_id": latest["run_id"],
            "source_file": log_path.as_posix(),
            "skipped_at": skipped_rows[0]["skipped_at"],
        }
    ]
    assert _jsonl_lines(run_dir / "malformed_log_lines.jsonl") == []


def test_stateful_logs_malformed_mer_blocks_and_continues(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    mer_path = input_root / "0100_malformed.MER"
    mer_path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<MISC UPLOAD_MAX=100kB />\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-07T22:47:22 FNAME=bad.000000 SMP_OFFSET=1 TRUE_FS=40.0 />\n"
            "\t<DATA>BAD</DATA>\n"
            "</EVENT>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-08T01:02:03 FNAME=good.000000 SMP_OFFSET=2 TRUE_FS=40.0 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=12 />\n"
            "\t<DATA>GOOD</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )

    output_root = tmp_path / "output"
    run_normalization_pipeline(input_root, output_dir=output_root)

    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    run_dir = output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"]
    malformed_rows = _jsonl_lines(run_dir / "malformed_mer_blocks.jsonl")
    event_rows = _jsonl_lines(output_root / "467.174-T-0100" / "mer_event_records.jsonl")

    assert [row["fname"] for row in event_rows] == ["bad.000000", "good.000000"]
    assert event_rows[0]["raw_format_line"] is None
    assert malformed_rows == []
    assert _jsonl_lines(run_dir / "skipped_mer_files.jsonl") == []


def test_stateful_logs_incomplete_mer_event_block_and_continues(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    mer_path = input_root / "0100_incomplete_data.MER"
    mer_path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<MISC UPLOAD_MAX=100kB />\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-07T22:47:22 FNAME=bad.000000 SMP_OFFSET=1 TRUE_FS=40.0 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=12 />\n"
            "\t<DATA>\n\rABCDEF\n"
            "</EVENT>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-08T01:02:03 FNAME=good.000000 SMP_OFFSET=2 TRUE_FS=40.0 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=3 />\n"
            "\t<DATA>GOOD</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )

    output_root = tmp_path / "output"
    run_normalization_pipeline(input_root, output_dir=output_root)

    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    run_dir = output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"]
    malformed_rows = _jsonl_lines(run_dir / "malformed_mer_blocks.jsonl")
    event_rows = _jsonl_lines(output_root / "467.174-T-0100" / "mer_event_records.jsonl")

    assert len(event_rows) == 1
    assert event_rows[0]["fname"] == "good.000000"
    assert malformed_rows == [
        {
            "block_index": 0,
            "block_kind": "event_data",
            "error": "incomplete DATA block: missing </DATA>",
            "instrument_id": "T0100",
            "instrument_serial": "467.174-T-0100",
            "raw_block": malformed_rows[0]["raw_block"],
            "run_id": latest["run_id"],
            "source_file": mer_path.as_posix(),
        }
    ]
    assert "<DATA>" in malformed_rows[0]["raw_block"]


def test_stateful_records_skipped_mer_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    mer_path = input_root / "0100_broken.MER"
    _write_mer(mer_path)

    def _raise_unreadable(path: Path, *, on_malformed_block=None):
        raise OSError("simulated unreadable mer")

    monkeypatch.setattr(normalize_mer_module, "parse_mer_file_recoverable", _raise_unreadable)

    output_root = tmp_path / "output"
    run_normalization_pipeline(input_root, output_dir=output_root)

    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    run_dir = output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"]
    skipped_rows = _jsonl_lines(run_dir / "skipped_mer_files.jsonl")

    assert skipped_rows == [
        {
            "error": "simulated unreadable mer",
            "instrument_id": "T0100",
            "instrument_serial": "467.174-T-0100",
            "run_id": latest["run_id"],
            "source_file": mer_path.as_posix(),
            "skipped_at": skipped_rows[0]["skipped_at"],
        }
    ]
    assert _jsonl_lines(run_dir / "malformed_mer_blocks.jsonl") == []


def test_stateful_skips_hopelessly_broken_mer_file(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    mer_path = input_root / "0100_hopeless.MER"
    mer_path.write_bytes(b"this is not a mer file at all")

    output_root = tmp_path / "output"
    run_normalization_pipeline(input_root, output_dir=output_root)

    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    run_dir = output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"]
    skipped_rows = _jsonl_lines(run_dir / "skipped_mer_files.jsonl")

    assert skipped_rows == [
        {
            "error": (
                "MER structure unreadable: no recoverable ENVIRONMENT, PARAMETERS, "
                "or EVENT content"
            ),
            "instrument_id": "T0100",
            "instrument_serial": "467.174-T-0100",
            "run_id": latest["run_id"],
            "source_file": mer_path.as_posix(),
            "skipped_at": skipped_rows[0]["skipped_at"],
        }
    ]
    assert _jsonl_lines(run_dir / "malformed_mer_blocks.jsonl") == []


def test_stateless_malformed_log_fails_closed_without_manifests(tmp_path: Path) -> None:
    log_path = tmp_path / "0100_malformed.LOG"
    log_path.write_text(
        "\n".join(
            [
                "1700000000:[MAIN  ,0007]first",
                "[DIVING,15",
                "1700000001:[MAIN  ,0007]second",
                "",
            ]
        ),
        encoding="utf-8",
    )

    output_root = tmp_path / "output"
    with pytest.raises(ValueError, match="Malformed LOG line 2"):
        run_normalization_pipeline(output_dir=output_root, input_files=[log_path])

    assert not (output_root / "0100" / "manifests").exists()
    assert _record_path(output_root / "0100", "log_unclassified_records.jsonl").read_text(encoding="utf-8") == ""


def test_stateful_run_materializes_canonical_output_file_set(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")

    output_root = tmp_path / "output"
    run_normalization_pipeline(input_root, output_dir=output_root)

    instrument_dir = output_root / "467.174-T-0100"
    serial = "467.174-T-0100"
    expected_jsonl = {
        f"log_acquisition_records.{serial}.jsonl",
        f"log_ascent_request_records.{serial}.jsonl",
        f"log_gps_records.{serial}.jsonl",
        f"log_pressure_temperature_records.{serial}.jsonl",
        f"log_battery_records.{serial}.jsonl",
        f"log_parameter_records.{serial}.jsonl",
        f"log_testmode_records.{serial}.jsonl",
        f"log_ctd_records.{serial}.jsonl",
        f"log_iridium_records.{serial}.jsonl",
        f"log_unclassified_records.{serial}.jsonl",
        f"mer_environment_records.{serial}.jsonl",
        f"mer_parameter_records.{serial}.jsonl",
        f"mer_event_records.{serial}.jsonl",
    }

    assert {path.name for path in instrument_dir.glob("*.jsonl")} == expected_jsonl
    assert _record_path(instrument_dir, "mer_event_records.jsonl").read_text(encoding="utf-8") == ""
    latest = _read_json(instrument_dir / "manifests" / "latest.json")
    outputs_manifest = _read_json(instrument_dir / latest["outputs_manifest"])
    assert {item["path"] for item in outputs_manifest["jsonl_outputs"]} == expected_jsonl
    assert outputs_manifest["instrument_serial"] == serial
    assert outputs_manifest["counts"][f"log_unclassified_records.{serial}"] == 1
    assert outputs_manifest["counts"][f"mer_event_records.{serial}"] == 0
    assert (instrument_dir / "state" / "pruned_records.jsonl").exists()


def test_first_run_diff_semantics_treat_previous_state_as_empty(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    _write_log(input_root / "0100_first.LOG", "first")
    output_root = tmp_path / "output"

    run_normalization_pipeline(input_root, output_dir=output_root)

    latest = _read_json(output_root / "467.174-T-0100" / "manifests" / "latest.json")
    diff_rows = _jsonl_lines(
        output_root / "467.174-T-0100" / "manifests" / "runs" / latest["run_id"] / "input_file_diffs.jsonl"
    )

    assert len(diff_rows) == 1
    assert diff_rows[0]["source_file"] == "0100_first.LOG"
    assert diff_rows[0]["previous_exists"] is False
    assert diff_rows[0]["current_exists"] is True
    assert diff_rows[0]["previous_size_bytes"] == 0
    assert diff_rows[0]["previous_hash"] is None
    assert diff_rows[0]["change_kind"] == "new"


def _write_log(path: Path, message: str) -> None:
    path.write_text(f"1700000000:[MAIN  ,0007]{message}\n", encoding="utf-8")


def _write_mer(path: Path) -> None:
    path.write_bytes(
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


def _write_second_mer(path: Path) -> None:
    path.write_bytes(
        (
            "<ENVIRONMENT>\n"
            "\t<BOARD 452116600-A0 />\n"
            "</ENVIRONMENT>\n"
            "<PARAMETERS>\n"
            "\t<MISC UPLOAD_MAX=200kB />\n"
            "</PARAMETERS>\n"
            "<EVENT>\n"
            "\t<INFO DATE=2024-02-08T01:02:03 FNAME=2024-02-08T01_02_03.000000 "
            "SMP_OFFSET=614055 TRUE_FS=40.014107 />\n"
            "\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            "STAGES=5 NORMALIZED=YES LENGTH=2048 />\n"
            "\t<DATA>DEFG</DATA>\n"
            "</EVENT>\n"
        ).encode("ascii")
    )


def _write_decoder(path: Path, message: str) -> Path:
    path.write_text(
        f"""
from pathlib import Path

def database_update(_arg):
    print("Update Databases")

def concatenate_files(path):
    return [path]

def concatenate_rbr_files(path):
    return [path]

def decrypt_all(path):
    workdir = Path(path)
    log = workdir / "0100_first.LOG"
    log.write_text("1700000000:[MAIN  ,0007]{message}\\n", encoding="utf-8")
    return [path]
""",
        encoding="utf-8",
    )
    return path


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl_lines(path: Path) -> list[dict[str, object]]:
    path = _record_path(path.parent, path.name)
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _record_path(instrument_dir: Path, base_filename: str) -> Path:
    path = instrument_dir / base_filename
    if path.exists():
        return path
    stem = Path(base_filename).stem
    suffix = Path(base_filename).suffix
    matches = sorted(instrument_dir.glob(f"{stem}.*{suffix}"))
    if len(matches) == 1:
        return matches[0]
    return path
