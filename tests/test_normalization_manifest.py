# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from mermaid_records import __version__
import mermaid_records.manifest as manifest_module
from mermaid_records.cli import main
from mermaid_records.manifest import (
    NORMALIZATION_MANIFEST_FILENAME,
    write_normalization_manifest,
)


def test_identical_corpora_have_same_snapshot_across_location_and_creation_order(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "relocated" / "second"
    entries = {
        "467.174-T-0100/log_unclassified_records.467.174-T-0100.jsonl": b'{"a":1}\n',
        "452.020-P-06/mer_event_records.452.020-P-06.jsonl": b"",
        "452.020-P-06/log_gps_records.452.020-P-06.jsonl": b'{"b":2}\n',
    }
    _write_corpus(first_root, list(entries.items()))
    _write_corpus(second_root, list(reversed(entries.items())))

    first = write_normalization_manifest(
        output_root=first_root,
        input_root=tmp_path / "input-a",
        generation_command="mermaid-records normalize -i input-a -o first",
    )
    second = write_normalization_manifest(
        output_root=second_root,
        input_root=tmp_path / "input-b",
        generation_command="mermaid-records normalize --output-dir second --input-root input-b",
    )

    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["files"] == second["files"]
    assert [item["path"] for item in first["files"]] == sorted(entries)


def test_fresh_normalizations_of_relocated_identical_inputs_have_same_snapshot(
    tmp_path: Path,
) -> None:
    input_roots = [tmp_path / "machine-a" / "inputs", tmp_path / "machine-b" / "inputs"]
    output_roots = [tmp_path / "machine-a" / "records", tmp_path / "machine-b" / "records"]
    for input_root in input_roots:
        input_root.mkdir(parents=True)
        (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
        (input_root / "0100_first.LOG").write_text(
            "1700000000:[MAIN  ,0007]first\n",
            encoding="utf-8",
        )

    for input_root, output_root in zip(input_roots, output_roots, strict=True):
        assert main(
            [
                "normalize",
                "-i",
                input_root.as_posix(),
                "-o",
                output_root.as_posix(),
            ]
        ) == 0

    manifests = [
        json.loads(
            (output_root / NORMALIZATION_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        for output_root in output_roots
    ]
    assert manifests[0]["input_root"] != manifests[1]["input_root"]
    assert manifests[0]["generation_command"] != manifests[1]["generation_command"]
    assert manifests[0]["snapshot_id"] == manifests[1]["snapshot_id"]
    assert manifests[0]["files"] == manifests[1]["files"]


def test_filesystem_timestamp_changes_do_not_change_snapshot(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    data_path = output_root / "0100" / "log_unclassified_records.0100.jsonl"
    _write_file(data_path, b'{"message":"same"}\n')
    first = write_normalization_manifest(
        output_root=output_root,
        input_root=None,
        generation_command=None,
    )

    os.utime(data_path, (1_000_000_000, 1_000_000_000))
    second = write_normalization_manifest(
        output_root=output_root,
        input_root=None,
        generation_command=None,
    )

    assert first["snapshot_id"] == second["snapshot_id"]


def test_changing_file_contents_changes_snapshot(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    data_path = output_root / "0100" / "log_unclassified_records.0100.jsonl"
    _write_file(data_path, b"before\n")
    before = write_normalization_manifest(
        output_root=output_root,
        input_root=None,
        generation_command=None,
    )

    data_path.write_bytes(b"after\n")
    after = write_normalization_manifest(
        output_root=output_root,
        input_root=None,
        generation_command=None,
    )

    assert before["snapshot_id"] != after["snapshot_id"]


def test_adding_removing_and_renaming_files_change_snapshot(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    original = output_root / "0100" / "log_unclassified_records.0100.jsonl"
    added = output_root / "0100" / "mer_event_records.0100.jsonl"
    renamed = output_root / "0100" / "log_gps_records.0100.jsonl"
    _write_file(original, b"same bytes\n")
    initial = _snapshot_id(output_root)

    _write_file(added, b"")
    with_added = _snapshot_id(output_root)
    added.unlink()
    after_removal = _snapshot_id(output_root)
    original.rename(renamed)
    after_rename = _snapshot_id(output_root)

    assert with_added != initial
    assert after_removal == initial
    assert after_rename != initial


def test_manifest_does_not_hash_itself_and_lists_correct_checksums(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    entries = {
        "0100/log_unclassified_records.0100.jsonl": b"one\n",
        "0200/mer_event_records.0200.jsonl": b"two\n",
    }
    _write_corpus(output_root, list(entries.items()))
    first = write_normalization_manifest(
        output_root=output_root,
        input_root=None,
        generation_command=None,
    )
    manifest_path = output_root / NORMALIZATION_MANIFEST_FILENAME
    manifest_path.write_text('{"changed":"metadata only"}\n', encoding="utf-8")
    second = write_normalization_manifest(
        output_root=output_root,
        input_root=None,
        generation_command=None,
    )

    assert first["snapshot_id"] == second["snapshot_id"]
    assert NORMALIZATION_MANIFEST_FILENAME not in {
        item["path"] for item in second["files"]
    }
    assert second["file_count"] == len(entries)
    for item in second["files"]:
        expected_bytes = entries[item["path"]]
        assert item["byte_size"] == len(expected_bytes)
        assert item["sha256"] == hashlib.sha256(expected_bytes).hexdigest()


def test_state_and_run_bookkeeping_are_not_corpus_data(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    _write_file(output_root / "0100" / "log_unclassified_records.0100.jsonl", b"data\n")
    _write_file(output_root / "0100" / "state" / "pruned_records.jsonl", b"state\n")
    _write_file(
        output_root / "0100" / "manifests" / "runs" / "run" / "malformed_log_lines.jsonl",
        b"diagnostic\n",
    )

    manifest = write_normalization_manifest(
        output_root=output_root,
        input_root=None,
        generation_command=None,
    )

    assert [item["path"] for item in manifest["files"]] == [
        "0100/log_unclassified_records.0100.jsonl"
    ]


def test_unknown_jsonl_is_not_part_of_the_normalized_corpus_snapshot(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    canonical = output_root / "0100" / "log_unclassified_records.0100.jsonl"
    _write_file(canonical, b"data\n")
    initial = _snapshot_id(output_root)

    _write_file(output_root / "0100" / "research_notes.jsonl", b"user data\n")

    assert _snapshot_id(output_root) == initial


def test_manifest_is_atomically_replaced(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "output"
    _write_file(output_root / "0100" / "log_unclassified_records.0100.jsonl", b"data\n")
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def _record_replace(source: Path, destination: Path) -> None:
        replace_calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(manifest_module.os, "replace", _record_replace)
    write_normalization_manifest(
        output_root=output_root,
        input_root=None,
        generation_command=None,
    )

    assert len(replace_calls) == 1
    source, destination = replace_calls[0]
    assert source.parent == output_root
    assert destination == output_root / NORMALIZATION_MANIFEST_FILENAME
    assert not source.exists()


def test_cli_writes_root_manifest_after_normalized_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "467.174-T-0100.vit").write_text("", encoding="utf-8")
    (input_root / "0100_first.LOG").write_text(
        "1700000000:[MAIN  ,0007]first\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "output"
    invocation = [
        "normalize",
        "-i",
        input_root.as_posix(),
        "-o",
        output_root.as_posix(),
    ]

    assert main(invocation) == 0

    manifest = json.loads(
        (output_root / NORMALIZATION_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["mermaid_records_version"] == __version__
    assert manifest["generation_command"] == (
        f"mermaid-records normalize -i {input_root.as_posix()} "
        f"-o {output_root.as_posix()}"
    )
    assert manifest["input_root"] == input_root.as_posix()
    assert manifest["checksum_algorithm"] == "sha256"
    assert manifest["snapshot_id"].startswith("sha256:")
    assert manifest["file_count"] == 13
    assert all(item["path"].endswith(".jsonl") for item in manifest["files"])
    assert not any("/manifests/" in item["path"] for item in manifest["files"])
    assert not any("/state/" in item["path"] for item in manifest["files"])


def test_stateless_cli_writes_root_manifest_without_run_manifests(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "0100_first.LOG"
    log_path.write_text(
        "1700000000:[MAIN  ,0007]first\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "output"

    assert main(
        [
            "normalize",
            "--input-file",
            log_path.as_posix(),
            "-o",
            output_root.as_posix(),
        ]
    ) == 0

    manifest = json.loads(
        (output_root / NORMALIZATION_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["input_root"] is None
    assert manifest["file_count"] == 13
    assert not (output_root / "0100" / "manifests").exists()


def _snapshot_id(output_root: Path) -> str:
    return str(
        write_normalization_manifest(
            output_root=output_root,
            input_root=None,
            generation_command=None,
        )["snapshot_id"]
    )


def _write_corpus(output_root: Path, entries: list[tuple[str, bytes]]) -> None:
    for relative_path, content in entries:
        _write_file(output_root / relative_path, content)


def _write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
