# SPDX-License-Identifier: MIT

"""Per-instrument manifest persistence for normalization pipeline runs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
from typing import TYPE_CHECKING

from . import __version__
from .format_datetime import format_utc_datetime
from .format_record_filenames import is_canonical_record_filename, validate_instrument_serial

if TYPE_CHECKING:
    from .bin2log import Bin2LogConfig


NORMALIZATION_MANIFEST_FILENAME = "normalization_manifest.json"


def write_normalization_manifest(
    *,
    output_root: Path,
    input_root: Path | None,
    generation_command: str | None,
) -> dict[str, object]:
    """Atomically write the content-addressed normalized-corpus manifest."""

    files = _normalized_file_inventory(output_root)
    snapshot_hasher = hashlib.sha256()
    for item in files:
        snapshot_hasher.update(
            (
                f"{item['path']}\t{item['byte_size']}\t{item['sha256']}\n"
            ).encode("utf-8")
        )
    git_commit, git_dirty = _package_git_state()
    payload: dict[str, object] = {
        "schema_version": 1,
        "mermaid_records_version": __version__,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "generation_command": generation_command,
        "generated_at": _iso_now(),
        "input_root": input_root.as_posix() if input_root is not None else None,
        "checksum_algorithm": "sha256",
        "snapshot_id": f"sha256:{snapshot_hasher.hexdigest()}",
        "file_count": len(files),
        "files": files,
    }
    _write_json_atomic(output_root / NORMALIZATION_MANIFEST_FILENAME, payload)
    return payload


def begin_instrument_run(
    *,
    instrument_output_dir: Path,
    input_root: Path,
    raw_source_paths: list[Path],
    config: Bin2LogConfig | None,
    normalization_version: str,
    instrument_serial: str,
) -> dict[str, object]:
    """Create manifest context for one instrument-level stateful run."""

    started_at = _iso_now()
    run_id = _manifest_run_id()
    serial = validate_instrument_serial(instrument_serial)
    manifests_root = instrument_output_dir / "manifests"
    run_dir = manifests_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    source_state = build_source_state(
        raw_source_paths=raw_source_paths,
        config=config if _has_bin(raw_source_paths) else None,
        input_root=input_root,
        normalization_version=normalization_version,
        instrument_serial=serial,
    )
    return {
        "run_id": run_id,
        "started_at": started_at,
        "run_dir": run_dir,
        "manifests_root": manifests_root,
        "instrument_output_dir": instrument_output_dir,
        "instrument_serial": serial,
        "source_state": source_state,
    }


def finalize_instrument_run(
    *,
    context: dict[str, object],
    preflight_mode: str | None,
    error: BaseException | None,
    input_file_diffs: list[dict[str, object]] | None = None,
    malformed_log_lines: list[dict[str, object]] | None = None,
    skipped_log_files: list[dict[str, object]] | None = None,
    malformed_mer_blocks: list[dict[str, object]] | None = None,
    skipped_mer_files: list[dict[str, object]] | None = None,
) -> None:
    """Write per-instrument manifests for a completed or failed run."""

    run_dir = Path(context["run_dir"])
    manifests_root = Path(context["manifests_root"])
    instrument_output_dir = Path(context["instrument_output_dir"])
    source_state = context["source_state"]
    instrument_serial = validate_instrument_serial(str(context["instrument_serial"]))
    run_json = {
        "run_id": context["run_id"],
        "instrument_serial": instrument_serial,
        "started_at": context["started_at"],
        "completed_at": _iso_now(),
        "input_root": source_state["input_root"],
        "output_root": instrument_output_dir.as_posix(),
        "normalization_version": source_state["normalization_version"],
        "preflight_mode": preflight_mode,
        "status": _run_status(instrument_output_dir, error),
    }
    outputs_json = build_outputs_manifest(instrument_output_dir)

    _write_json(run_dir / "run.json", run_json)
    _write_json(run_dir / "outputs.json", outputs_json)
    _write_json(run_dir / "source_state.json", source_state)
    _write_jsonl(run_dir / "input_file_diffs.jsonl", input_file_diffs or [])
    _write_jsonl(run_dir / "malformed_log_lines.jsonl", malformed_log_lines or [])
    _write_jsonl(run_dir / "skipped_log_files.jsonl", skipped_log_files or [])
    _write_jsonl(run_dir / "malformed_mer_blocks.jsonl", malformed_mer_blocks or [])
    _write_jsonl(run_dir / "skipped_mer_files.jsonl", skipped_mer_files or [])

    preflight_run_path = run_dir / "preflight_status.json"
    preflight_root = instrument_output_dir / "preflight_status.json"
    if preflight_root.exists():
        preflight_run_path.write_text(
            preflight_root.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    latest_json = {
        "run_id": context["run_id"],
        "instrument_serial": instrument_serial,
        "status": run_json["status"],
        "started_at": context["started_at"],
        "completed_at": run_json["completed_at"],
        "run_manifest": (run_dir / "run.json").relative_to(instrument_output_dir).as_posix(),
        "outputs_manifest": (run_dir / "outputs.json").relative_to(instrument_output_dir).as_posix(),
        "source_state_manifest": (
            (run_dir / "source_state.json").relative_to(instrument_output_dir).as_posix()
        ),
    }
    if preflight_run_path.exists():
        latest_json["preflight_status"] = preflight_run_path.relative_to(
            instrument_output_dir
        ).as_posix()
    manifests_root.mkdir(parents=True, exist_ok=True)
    _write_json(manifests_root / "latest.json", latest_json)


def latest_source_state(instrument_output_dir: Path) -> dict[str, object] | None:
    """Load the latest persisted source state for one instrument, if present."""

    latest_path = instrument_output_dir / "manifests" / "latest.json"
    if not latest_path.exists():
        return None
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    source_state_path = instrument_output_dir / latest["source_state_manifest"]
    if not source_state_path.exists():
        return None
    return json.loads(source_state_path.read_text(encoding="utf-8"))


def latest_outputs_manifest(instrument_output_dir: Path) -> dict[str, object] | None:
    """Load the latest persisted outputs manifest for one instrument, if present."""

    latest_path = instrument_output_dir / "manifests" / "latest.json"
    if not latest_path.exists():
        return None
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    outputs_manifest_path = instrument_output_dir / latest["outputs_manifest"]
    if not outputs_manifest_path.exists():
        return None
    return json.loads(outputs_manifest_path.read_text(encoding="utf-8"))


def output_dir_contains_manifests(output_dir: Path) -> bool:
    """Return whether the output tree already contains manifests."""

    if not output_dir.exists():
        return False
    return any(path.is_dir() and path.name == "manifests" for path in output_dir.rglob("manifests"))


def build_source_state(
    *,
    raw_source_paths: list[Path],
    config: Bin2LogConfig | None,
    input_root: Path,
    normalization_version: str,
    instrument_serial: str,
) -> dict[str, object]:
    """Build source state for one instrument-level run."""

    serial = validate_instrument_serial(instrument_serial)
    raw_sources = [
        {
            "source_file": path.as_posix(),
            "source_kind": _source_kind(path),
            "size_bytes": path.stat().st_size,
            "content_hash": _hash_file(path),
        }
        for path in sorted(raw_source_paths)
    ]
    return {
        "input_root": input_root.as_posix(),
        "instrument_serial": serial,
        "normalization_version": normalization_version,
        "raw_sources": raw_sources,
        "decoder_state": _decoder_state(config),
    }


def build_outputs_manifest(instrument_output_dir: Path) -> dict[str, object]:
    """Build the output inventory for one instrument-level output root."""

    jsonl_outputs = [
        {
            "path": path.relative_to(instrument_output_dir).as_posix(),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(instrument_output_dir.glob("*.jsonl"))
        if path.is_file()
    ]
    counts = {}
    for item in jsonl_outputs:
        path = instrument_output_dir / item["path"]
        with path.open("r", encoding="utf-8") as handle:
            counts[path.name.removesuffix(".jsonl")] = sum(1 for line in handle if line.strip())
    return {
        "instrument_serial": instrument_output_dir.name,
        "jsonl_outputs": jsonl_outputs,
        "counts": counts,
    }


def record_pruned_sources(
    *,
    instrument_output_dir: Path,
    instrument_id: str,
    instrument_serial: str,
    removed_sources: list[dict[str, object]],
) -> None:
    """Append pruned-source records for removed raw files."""

    if not removed_sources:
        return
    state_dir = instrument_output_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    pruned_path = state_dir / "pruned_records.jsonl"
    removed_at = _iso_now()
    with pruned_path.open("a", encoding="utf-8") as handle:
        for source in removed_sources:
            record = {
                "instrument_id": instrument_id,
                "instrument_serial": validate_instrument_serial(instrument_serial),
                "source_file": source.get("_source_path", source["source_file"]),
                "source_kind": source["source_kind"],
                "removed_at": removed_at,
            }
            handle.write(json.dumps(record, allow_nan=False))
            handle.write("\n")


def _decoder_state(config: Bin2LogConfig | None) -> dict[str, object] | None:
    if config is None:
        return None

    database_root = _database_root()
    database_files = _database_files(database_root)
    return {
        "decoder_python": str(config.python_executable),
        "decoder_python_version": _python_version(config.python_executable),
        "decoder_script": str(config.decoder_script),
        "decoder_script_hash": _hash_file(config.decoder_script),
        "preflight_mode": config.preflight_mode,
        "database_bundle_hash": _bundle_hash(database_files),
        "database_files": [path.as_posix() for path in database_files],
        "decoder_git_commit": _git_commit(config.decoder_script.parent),
        "decoder_environment_fingerprint": config.environment_fingerprint,
    }


def _database_root() -> Path | None:
    mermaid_root = os.environ.get("MERMAID")
    if not mermaid_root:
        return None
    database_root = Path(mermaid_root) / "database"
    if not database_root.exists() or not database_root.is_dir():
        return None
    return database_root


def _database_files(database_root: Path | None) -> list[Path]:
    if database_root is None:
        return []
    return sorted(path for path in database_root.glob("*.json") if path.is_file())


def _bundle_hash(paths: list[Path]) -> str | None:
    existing_paths = [path for path in paths if path.exists() and path.is_file()]
    if not existing_paths:
        return None
    manifest_lines = [f"{path.name}\t{_hash_file(path)}" for path in existing_paths]
    return hashlib.sha256("\n".join(manifest_lines).encode("utf-8")).hexdigest()


def _python_version(python_executable: Path) -> str | None:
    result = subprocess.run(
        [str(python_executable), "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_commit(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _package_git_state() -> tuple[str | None, bool | None]:
    package_dir = Path(__file__).resolve().parent
    commit = _git_commit(package_dir)
    if commit is None:
        return None, None
    try:
        result = subprocess.run(
            ["git", "-C", str(package_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return commit, None
    if result.returncode != 0:
        return commit, None
    return commit, bool(result.stdout)


def _normalized_file_inventory(output_root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in output_root.rglob("*.jsonl"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(output_root)
        if {"manifests", "state"} & set(relative_path.parts[:-1]):
            continue
        if not is_canonical_record_filename(path.name):
            continue
        files.append(
            {
                "path": relative_path.as_posix(),
                "byte_size": path.stat().st_size,
                "sha256": _hash_file(path),
            }
        )
    return sorted(files, key=lambda item: str(item["path"]))


def _run_status(instrument_output_dir: Path, error: BaseException | None) -> str:
    if error is None:
        return "success"
    if _has_materialized_outputs(instrument_output_dir) or (
        instrument_output_dir / "preflight_status.json"
    ).exists():
        return "partial"
    return "failed"


def _source_kind(path: Path) -> str:
    suffix = path.suffix.upper()
    if suffix == ".BIN":
        return "bin"
    if suffix == ".LOG":
        return "log"
    if suffix == ".MER":
        return "mer"
    raise ValueError(f"Unsupported raw source kind for manifest: {path}")


def _has_bin(paths: list[Path]) -> bool:
    return any(path.suffix.upper() == ".BIN" for path in paths)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _iso_now() -> str:
    return format_utc_datetime(datetime.now(timezone.utc))


def _manifest_run_id() -> str:
    timestamp = format_utc_datetime(datetime.now(timezone.utc))
    return f"{timestamp}-{secrets.token_hex(3)}"


def _has_materialized_outputs(instrument_output_dir: Path) -> bool:
    return any(
        path.is_file() and path.stat().st_size > 0 for path in instrument_output_dir.glob("*.jsonl")
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, allow_nan=False))
            handle.write("\n")
