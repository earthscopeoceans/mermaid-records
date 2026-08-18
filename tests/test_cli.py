# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
from mermaid_records import __version__
from mermaid_records.cli import build_parser, main


def test_cli_help_exposes_only_normalize_subcommand() -> None:
    help_text = build_parser().format_help()

    assert "normalize" in help_text
    assert "inspect-mer" not in help_text


def test_cli_version_option_reports_package_version(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    captured = capsys.readouterr()

    assert excinfo.value.code == 0
    assert captured.out == f"mermaid-records {__version__}\n"
    assert captured.err == ""


def test_cli_short_version_option_reports_package_version(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["-v"])

    captured = capsys.readouterr()

    assert excinfo.value.code == 0
    assert captured.out == f"mermaid-records {__version__}\n"
    assert captured.err == ""


def test_cli_help_exposes_top_level_version_option() -> None:
    option_strings = {
        option_string
        for action in build_parser()._actions
        for option_string in action.option_strings
    }

    assert {"-v", "--version"} <= option_strings


def test_normalize_cli_writes_log_and_mer_jsonl_outputs(tmp_path: Path, capsys) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")

    log_path = input_root / "0100_sample.LOG"
    log_path.write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    mer_path = input_root / "0100_sample.MER"
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

    output_dir = tmp_path / "output"

    result = main(
        [
            "normalize",
            "-i",
            str(input_root),
            "-o",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "NORMALIZATION SUMMARY" in captured.out
    assert "mode: stateful" in captured.out
    assert "raw files processed: 2" in captured.out
    assert "log records written=" in captured.out
    assert "mer records written=" in captured.out
    assert "Starting normalization" in captured.err
    assert "Processing instrument T0100" in captured.err
    assert "Normalizing LOG for instrument T0100" in captured.err
    assert "Normalizing MER for instrument T0100" in captured.err
    assert "Family" in captured.out
    assert "log_unclassified_records.467.174-T-0100.jsonl" in captured.out
    assert "Ordinary LOG lines" in captured.out
    assert "Difference" in captured.out
    assert _record_path(output_dir / "467.174-T-0100", "log_unclassified_records.jsonl").exists()
    assert not _record_path(output_dir / "467.174-T-0100", "log_operational_records.jsonl").exists()
    assert _record_path(output_dir / "467.174-T-0100", "mer_environment_records.jsonl").exists()
    assert not (output_dir / "467.174-T-0100" / "preflight_status.json").exists()


def test_normalize_cli_accepts_comma_and_space_separated_input_files(tmp_path: Path, capsys) -> None:
    log_a = tmp_path / "0100_a.LOG"
    log_b = tmp_path / "0100_b.LOG"
    log_c = tmp_path / "0100_c.LOG"
    log_a.write_text("1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n", encoding="utf-8")
    log_b.write_text("1700000001:[MAIN  ,0007]second\n", encoding="utf-8")
    log_c.write_text("1700000002:[MAIN  ,0007]third\n", encoding="utf-8")

    output_dir = tmp_path / "output"
    result = main(
        [
            "normalize",
            "--input-file",
            f"{log_a},{log_b}",
            str(log_c),
            "-o",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "NORMALIZATION SUMMARY" in captured.out
    assert "mode: stateless" in captured.out
    assert "raw files processed: 3" in captured.out
    assert _record_path(output_dir / "467.174-T-0100", "log_unclassified_records.jsonl").exists()
    assert not _record_path(output_dir / "467.174-T-0100", "log_operational_records.jsonl").exists()


def test_normalize_cli_warns_when_input_root_has_no_expected_sources(
    tmp_path: Path,
    capsys,
) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "0100_sample.S61").write_bytes(b"not in scope")

    output_dir = tmp_path / "output"
    result = main(["normalize", "-i", str(input_root), "-o", str(output_dir)])

    captured = capsys.readouterr()

    assert result == 0
    assert "NORMALIZATION SUMMARY" in captured.out
    assert "raw files processed: 0" in captured.out
    assert (
        f"WARNING: no expected source files found under {input_root} "
        "(expected .BIN, .LOG, or .MER)"
    ) in captured.err


def test_normalize_cli_reports_writer_assignment_counts_without_rereading_outputs(
    tmp_path: Path,
    capsys,
) -> None:
    first_log = tmp_path / "first" / "0100_same.LOG"
    second_log = tmp_path / "second" / "0100_same.LOG"
    first_log.parent.mkdir()
    second_log.parent.mkdir()
    raw_line = "1700000000:[MAIN  ,0007]same source text"
    first_log.write_text(f"{raw_line}\n", encoding="utf-8")
    second_log.write_text(f"{raw_line}\n", encoding="utf-8")

    output_dir = tmp_path / "output"
    result = main(
        [
            "normalize",
            "--input-file",
            str(first_log),
            str(second_log),
            "-o",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "log_unclassified_records.0100.jsonl" in captured.out
    assert _summary_count(captured.out, "TOTAL") == "2"
    assert _summary_count(captured.out, "Ordinary LOG lines") == "2"
    assert _summary_count(captured.out, "Difference") == "0"


def test_normalize_cli_dry_run_human_output_is_side_effect_free(tmp_path: Path, capsys) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    log_path = input_root / "0100_sample.LOG"
    log_path.write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    result = main(
        [
            "normalize",
            "-i",
            str(input_root),
            "-o",
            str(output_dir),
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "DRY RUN SUMMARY" in captured.out
    assert "mode: stateful" in captured.out
    assert "raw files processed: 1" in captured.out
    assert "not evaluated" in captured.out
    assert "INSTRUMENT 467.174-T-0100" in captured.out
    assert "log: append" in captured.out
    assert "0100_sample.LOG (0 B -> " in captured.out
    assert not output_dir.exists()


def test_normalize_cli_dry_run_warns_when_input_root_has_no_expected_sources(
    tmp_path: Path,
    capsys,
) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "notes.txt").write_text("not a raw source\n", encoding="utf-8")

    output_dir = tmp_path / "output"
    result = main(
        [
            "normalize",
            "-i",
            str(input_root),
            "-o",
            str(output_dir),
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "DRY RUN SUMMARY" in captured.out
    assert "raw files processed: 0" in captured.out
    assert (
        f"WARNING: no expected source files found under {input_root} "
        "(expected .BIN, .LOG, or .MER)"
    ) in captured.err
    assert not output_dir.exists()


def test_normalize_cli_dry_run_json_output(tmp_path: Path, capsys) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    log_path = input_root / "0100_sample.LOG"
    log_path.write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    result = main(
        [
            "normalize",
            "-i",
            str(input_root),
            "-o",
            str(output_dir),
            "--dry-run",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["mode"] == "stateful"
    assert payload["instruments"][0]["families"]["log"]["action"] == "append"
    assert payload["instruments"][0]["families"]["log"]["file_diffs"][0]["change_kind"] == "new"
    assert not output_dir.exists()


def test_normalize_cli_json_requires_dry_run(tmp_path: Path, capsys) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "normalize",
                "-i",
                str(input_root),
                "-o",
                str(output_dir),
                "--json",
            ]
        )

    captured = capsys.readouterr()

    assert excinfo.value.code == 2
    assert "--json requires --dry-run" in captured.err
    assert not output_dir.exists()


@pytest.mark.parametrize("force_flag", ["-f", "--force"])
def test_normalize_cli_force_reports_rewrite_in_dry_run_json(
    tmp_path: Path,
    capsys,
    force_flag: str,
) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    result = main(
        [
            "normalize",
            "-i",
            str(input_root),
            "-o",
            str(output_dir),
            "--dry-run",
            "--json",
            force_flag,
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["instruments"][0]["families"]["log"]["action"] == "rewrite"
    assert payload["instruments"][0]["families"]["mer"]["action"] == "rewrite"
    assert not output_dir.exists()


def test_normalize_cli_verbose_summary_expands_output(tmp_path: Path, capsys) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    result = main(
        [
            "normalize",
            "-i",
            str(input_root),
            "-o",
            str(output_dir),
            "--verbose",
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "    family actions:" in captured.out
    assert "      log: append=1 rewrite=0 noop=0" in captured.out
    assert "      mer: append=0 rewrite=0 noop=1" in captured.out
    assert "      per-instrument actions:" in captured.out
    assert f"output root: {output_dir.as_posix()}" in captured.out
    assert f"input root: {input_root.as_posix()}" in captured.out


def test_normalize_cli_short_verbose_flag_expands_output(tmp_path: Path, capsys) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    result = main(
        [
            "normalize",
            "-i",
            str(input_root),
            "-o",
            str(output_dir),
            "-v",
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "    family actions:" in captured.out
    assert "      per-instrument actions:" in captured.out
    assert "output root:" in captured.out


def test_run_normalization_pipeline_is_quiet_by_default(tmp_path: Path, capsys) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    from mermaid_records.normalize_pipeline import run_normalization_pipeline

    run_normalization_pipeline(input_root, output_dir=output_dir)
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""


def test_output_dir_resolves_from_mermaid_env(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    input_root = tmp_path / "inputs"
    mermaid_root = tmp_path / "mermaid"
    input_root.mkdir()
    mermaid_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MERMAID", mermaid_root.as_posix())

    result = main(["normalize", "-i", str(input_root)])

    captured = capsys.readouterr()

    assert result == 0
    assert "NORMALIZATION SUMMARY" in captured.out
    assert _record_path(
        mermaid_root / "records" / "467.174-T-0100",
        "log_unclassified_records.jsonl",
    ).exists()


def test_missing_output_dir_and_mermaid_env_errors_clearly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MERMAID", raising=False)

    with pytest.raises(SystemExit, match="--output-dir was not given and MERMAID is not set"):
        main(["normalize", "-i", str(input_root)])


def test_decoder_python_resolves_from_env_for_bin_runs(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "inputs"
    output_dir = tmp_path / "output"
    input_root.mkdir()
    (input_root / "0100_sample.BIN").write_bytes(b"raw-bin")
    decoder = _write_decoder(tmp_path / "decoder.py", "decoded from env python")
    mermaid_root = tmp_path / "mermaid"
    database_root = mermaid_root / "database"
    database_root.mkdir(parents=True)
    (database_root / "Databases.json").write_text("[]\n", encoding="utf-8")
    monkeypatch.setenv("MERMAID", mermaid_root.as_posix())
    monkeypatch.setenv("MERMAID_RECORDS_DECODER_PYTHON", sys.executable)
    monkeypatch.setenv("MERMAID_RECORDS_DECODER_SCRIPT", decoder.as_posix())

    result = main(["normalize", "-i", str(input_root), "-o", str(output_dir)])

    captured = capsys.readouterr()

    assert result == 0
    assert "bin files decoded=1" in captured.out
    rows = _jsonl_lines(output_dir / "0100" / "log_unclassified_records.jsonl")
    assert rows[0]["message"] == "decoded from env python"


def test_decoder_script_resolves_from_env_for_bin_runs(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "inputs"
    output_dir = tmp_path / "output"
    input_root.mkdir()
    (input_root / "0100_sample.BIN").write_bytes(b"raw-bin")
    decoder = _write_decoder(tmp_path / "decoder.py", "decoded from env script")
    mermaid_root = tmp_path / "mermaid"
    database_root = mermaid_root / "database"
    database_root.mkdir(parents=True)
    (database_root / "Databases.json").write_text("[]\n", encoding="utf-8")
    monkeypatch.setenv("MERMAID", mermaid_root.as_posix())
    monkeypatch.setenv("MERMAID_RECORDS_DECODER_PYTHON", sys.executable)
    monkeypatch.setenv("MERMAID_RECORDS_DECODER_SCRIPT", decoder.as_posix())

    result = main(["normalize", "-i", str(input_root), "-o", str(output_dir)])

    captured = capsys.readouterr()

    assert result == 0
    assert "bin files decoded=1" in captured.out
    rows = _jsonl_lines(output_dir / "0100" / "log_unclassified_records.jsonl")
    assert rows[0]["message"] == "decoded from env script"


def test_explicit_cli_decoder_args_override_env(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "inputs"
    output_dir = tmp_path / "output"
    input_root.mkdir()
    (input_root / "0100_sample.BIN").write_bytes(b"raw-bin")
    env_decoder = _write_decoder(tmp_path / "decoder_env.py", "decoded from env")
    cli_decoder = _write_decoder(tmp_path / "decoder_cli.py", "decoded from cli")
    mermaid_root = tmp_path / "mermaid"
    database_root = mermaid_root / "database"
    database_root.mkdir(parents=True)
    (database_root / "Databases.json").write_text("[]\n", encoding="utf-8")
    monkeypatch.setenv("MERMAID", mermaid_root.as_posix())
    monkeypatch.setenv("MERMAID_RECORDS_DECODER_PYTHON", "/does/not/exist/python")
    monkeypatch.setenv("MERMAID_RECORDS_DECODER_SCRIPT", env_decoder.as_posix())

    result = main(
        [
            "normalize",
            "-i",
            str(input_root),
            "-o",
            str(output_dir),
            "--decoder-python",
            sys.executable,
            "--decoder-script",
            str(cli_decoder),
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "bin files decoded=1" in captured.out
    rows = _jsonl_lines(output_dir / "0100" / "log_unclassified_records.jsonl")
    assert rows[0]["message"] == "decoded from cli"


def test_normalize_cli_limits_input_root_to_one_instrument_serial(
    tmp_path: Path,
    capsys,
) -> None:
    input_root = tmp_path / "inputs"
    target_root = input_root / "452.020-P-0030"
    other_root = input_root / "467.174-T-0030"
    target_root.mkdir(parents=True)
    other_root.mkdir(parents=True)
    (target_root / "0030_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]target\n",
        encoding="utf-8",
    )
    (other_root / "0030_sample.BIN").write_bytes(b"unrelated-bin")
    output_dir = tmp_path / "output"

    result = main(
        [
            "normalize",
            "--input-root",
            str(input_root),
            "--instrument-serial",
            "452.020-P-0030",
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "Selecting instrument serial 452.020-P-0030" in captured.err
    assert "raw files processed: 1" in captured.out
    assert (output_dir / "452.020-P-0030").is_dir()
    assert not (output_dir / "467.174-T-0030").exists()
    rows = _jsonl_lines(
        output_dir / "452.020-P-0030" / "log_unclassified_records.jsonl"
    )
    assert [row["message"] for row in rows] == ["target"]


def test_instrument_serial_requires_input_root_and_full_serial(
    tmp_path: Path,
    capsys,
) -> None:
    log_path = tmp_path / "0030_sample.LOG"
    log_path.write_text("1700000000:[MAIN  ,0007]target\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "normalize",
                "--input-file",
                str(log_path),
                "--instrument-serial",
                "452.020-P-0030",
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )
    assert excinfo.value.code == 2
    assert "--instrument-serial requires --input-root" in capsys.readouterr().err

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "normalize",
                "--input-root",
                str(tmp_path),
                "--instrument-serial",
                "P0030",
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )
    assert excinfo.value.code == 2
    assert "Unsupported instrument serial name" in capsys.readouterr().err


def test_missing_instrument_serial_fails_without_creating_output(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    instrument_root = input_root / "452.020-P-0030"
    instrument_root.mkdir(parents=True)
    (instrument_root / "0030_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]target\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"

    with pytest.raises(
        ValueError,
        match="Instrument serial not found under input root: 467.174-T-0200",
    ):
        main(
            [
                "normalize",
                "--input-root",
                str(input_root),
                "--instrument-serial",
                "467.174-T-0200",
                "--output-dir",
                str(output_dir),
            ]
        )

    assert not output_dir.exists()


def test_selected_bin_instrument_requires_decoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "inputs"
    instrument_root = input_root / "452.020-P-0030"
    instrument_root.mkdir(parents=True)
    (instrument_root / "0030_sample.BIN").write_bytes(b"raw-bin")
    monkeypatch.delenv("MERMAID_RECORDS_DECODER_PYTHON", raising=False)
    monkeypatch.delenv("MERMAID_RECORDS_DECODER_SCRIPT", raising=False)

    with pytest.raises(
        SystemExit,
        match="Provide --decoder-python or set MERMAID_RECORDS_DECODER_PYTHON",
    ):
        main(
            [
                "normalize",
                "--input-root",
                str(input_root),
                "--instrument-serial",
                "452.020-P-0030",
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )


def test_instrument_serial_dry_run_reports_only_target_and_writes_nothing(
    tmp_path: Path,
    capsys,
) -> None:
    input_root = tmp_path / "inputs"
    for serial, prefix in (
        ("452.020-P-0030", "0030"),
        ("467.174-T-0200", "0200"),
    ):
        instrument_root = input_root / serial
        instrument_root.mkdir(parents=True)
        (instrument_root / f"{prefix}_sample.LOG").write_text(
            f"1700000000:[MAIN  ,0007]{serial}\n",
            encoding="utf-8",
        )
    output_dir = tmp_path / "output"

    result = main(
        [
            "normalize",
            "--input-root",
            str(input_root),
            "--instrument-serial",
            "452.020-P-0030",
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert len(payload["instruments"]) == 1
    assert payload["instruments"][0]["instrument_serial"] == "452.020-P-0030"
    assert not output_dir.exists()


def test_bin_free_runs_do_not_require_decoder_env_or_args(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "inputs"
    output_dir = tmp_path / "output"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_sample.LOG").write_text(
        "1700000000:[MAIN  ,0007]buoy 467.174-T-0100\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MERMAID_RECORDS_DECODER_PYTHON", raising=False)
    monkeypatch.delenv("MERMAID_RECORDS_DECODER_SCRIPT", raising=False)

    result = main(["normalize", "-i", str(input_root), "-o", str(output_dir)])

    captured = capsys.readouterr()

    assert result == 0
    assert "NORMALIZATION SUMMARY" in captured.out
    assert "bin files decoded=0" in captured.out
    assert _record_path(output_dir / "467.174-T-0100", "log_unclassified_records.jsonl").exists()
    assert not _record_path(output_dir / "467.174-T-0100", "log_operational_records.jsonl").exists()


def test_bin_runs_require_decoder_python_when_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "inputs"
    output_dir = tmp_path / "output"
    input_root.mkdir()
    (input_root / "0100_sample.BIN").write_bytes(b"raw-bin")
    decoder = _write_decoder(tmp_path / "decoder.py", "decoded")
    monkeypatch.delenv("MERMAID_RECORDS_DECODER_PYTHON", raising=False)
    monkeypatch.setenv("MERMAID_RECORDS_DECODER_SCRIPT", decoder.as_posix())

    with pytest.raises(SystemExit, match="Provide --decoder-python or set MERMAID_RECORDS_DECODER_PYTHON"):
        main(["normalize", "-i", str(input_root), "-o", str(output_dir)])


def test_bin_runs_require_decoder_script_when_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "inputs"
    output_dir = tmp_path / "output"
    input_root.mkdir()
    (input_root / "0100_sample.BIN").write_bytes(b"raw-bin")
    monkeypatch.setenv("MERMAID_RECORDS_DECODER_PYTHON", sys.executable)
    monkeypatch.delenv("MERMAID_RECORDS_DECODER_SCRIPT", raising=False)

    with pytest.raises(SystemExit, match="Provide --decoder-script or set MERMAID_RECORDS_DECODER_SCRIPT"):
        main(["normalize", "-i", str(input_root), "-o", str(output_dir)])


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
    log = workdir / "0100_sample.LOG"
    log.write_text("1700000000:[MAIN  ,0007]{message}\\n", encoding="utf-8")
    return [path]
""",
        encoding="utf-8",
    )
    return path


def _jsonl_lines(path: Path) -> list[dict[str, object]]:
    path = _record_path(path.parent, path.name)
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _summary_count(output: str, label: str) -> str:
    line = next(line for line in output.splitlines() if line.startswith(label))
    return line.split()[-1]


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
