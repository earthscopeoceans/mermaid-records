# SPDX-License-Identifier: MIT

"""External adapter layer for upstream BIN-to-LOG decoding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Literal
from typing import Iterator

from .format_datetime import format_utc_datetime

type BinDecodePreflightMode = Literal["strict", "cached"]


@dataclass(slots=True)
class Bin2LogConfig:
    """Configuration for invoking the external manufacturer decoder."""

    python_executable: Path
    decoder_script: Path
    preflight_mode: BinDecodePreflightMode = "strict"
    preflight_status_dir: Path | None = None
    environment_fingerprint: str | None = None

    def __post_init__(self) -> None:
        """Validate the configured preflight mode."""

        if self.preflight_mode not in {"strict", "cached"}:
            raise ValueError(
                "preflight_mode must be one of: 'strict', 'cached'"
            )
        if self.environment_fingerprint is not None and not self.environment_fingerprint.strip():
            raise ValueError("environment_fingerprint must be non-empty when provided")


class Bin2LogError(RuntimeError):
    """Raised when external BIN-to-LOG decoding fails."""


def update_decoder_database(config: Bin2LogConfig) -> None:
    """Refresh the external decoder database files once for a batch workflow."""

    _validate_decoder_paths(config)
    harness = """
from pathlib import Path
import runpy
import sys

decoder_script = Path(sys.argv[1]).resolve()

sys.path.insert(0, str(decoder_script.parent))
namespace = runpy.run_path(str(decoder_script))

database_update = namespace.get("database_update")
if callable(database_update):
    database_update(None)
"""
    result = subprocess.run(
        [
            str(config.python_executable),
            "-c",
            harness,
            str(config.decoder_script),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _write_preflight_status(
            config,
            effective_mode=config.preflight_mode,
            database_update_succeeded=False,
            continued_after_failure=False,
            failure_detail=_format_subprocess_failure(
                result.returncode,
                result.stdout,
                result.stderr,
            ),
        )
        raise Bin2LogError(
            _format_subprocess_failure(result.returncode, result.stdout, result.stderr)
        )
    _handle_database_update_result(
        config,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def prepare_decode_workspace(
    workdir: Path,
    *,
    config: Bin2LogConfig,
    refresh_database: bool = False,
) -> None:
    """Run batch-scoped preprocess steps in a copied decode workspace."""

    _validate_decoder_paths(config)
    if not workdir.exists():
        raise FileNotFoundError(workdir)
    if not workdir.is_dir():
        raise Bin2LogError(f"Decode workspace is not a directory: {workdir}")

    harness = """
from pathlib import Path
import os
import runpy
import sys

decoder_script = Path(sys.argv[1]).resolve()
workdir = Path(sys.argv[2]).resolve()
refresh_database = sys.argv[3] == "1"

sys.path.insert(0, str(decoder_script.parent))
namespace = runpy.run_path(str(decoder_script))

database_update = namespace.get("database_update")
concatenate_files = namespace.get("concatenate_files")
concatenate_rbr_files = namespace.get("concatenate_rbr_files")

workdir_str = str(workdir) + os.sep
if refresh_database and callable(database_update):
    database_update(None)
if callable(concatenate_files):
    concatenate_files(workdir_str)
if callable(concatenate_rbr_files):
    concatenate_rbr_files(workdir_str)
"""
    result = subprocess.run(
        [
            str(config.python_executable),
            "-c",
            harness,
            str(config.decoder_script),
            str(workdir),
            "1" if refresh_database else "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if refresh_database:
            _write_preflight_status(
                config,
                effective_mode=config.preflight_mode,
                database_update_succeeded=False,
                continued_after_failure=False,
                failure_detail=_format_subprocess_failure(
                    result.returncode,
                    result.stdout,
                    result.stderr,
                ),
            )
        raise Bin2LogError(
            _format_subprocess_failure(result.returncode, result.stdout, result.stderr)
        )
    if refresh_database:
        _handle_database_update_result(
            config,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def decode_workspace_logs(workdir: Path, *, config: Bin2LogConfig) -> list[Path]:
    """Decode all BIN files currently present in a prepared workspace into LOG files."""

    _validate_decoder_paths(config)
    if not workdir.exists():
        raise FileNotFoundError(workdir)
    if not workdir.is_dir():
        raise Bin2LogError(f"Decode workspace is not a directory: {workdir}")

    _run_log_decoder(workdir, config)
    return sorted(workdir.glob("*.LOG"))


def iter_decoded_log_lines(path: Path, *, config: Bin2LogConfig) -> Iterator[str]:
    """Yield decoded LOG text lines for one raw .BIN file.

    Observed manufacturer decoder I/O contract:

    - `preprocess.py` decodes `.BIN` files into `.LOG` files via `decrypt_all(...)`
    - database refresh and concatenate steps are separate upstream preflight work
      and should be run once per copied batch workspace, not once per BIN
    - the manufacturer workflow may delete the working-copy `.BIN`

    The adapter therefore:

    - copies the requested `.BIN` into a temporary working directory
    - invokes only the actual BIN->LOG decode step for one copied BIN
    - reads emitted `.LOG` artifact(s)
    - leaves the original source `.BIN` untouched
    """

    _validate_inputs(path, config)

    with _decoded_log_workspace(path, config) as workdir:
        log_paths = sorted(workdir.glob("*.LOG"))
        if not log_paths:
            raise Bin2LogError(
                f"External decoder did not emit any .LOG files for {path}."
            )

        for log_path in log_paths:
            with log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    yield raw_line.rstrip("\r\n")


def _validate_inputs(path: Path, config: Bin2LogConfig) -> None:
    """Validate adapter inputs before subprocess invocation."""

    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise Bin2LogError(f"BIN input is not a file: {path}")
    _validate_decoder_paths(config)


def _validate_decoder_paths(config: Bin2LogConfig) -> None:
    """Validate configured decoder executable and script paths."""

    if not config.python_executable.exists():
        raise FileNotFoundError(config.python_executable)
    if not config.decoder_script.exists():
        raise FileNotFoundError(config.decoder_script)


class _DecodedLogWorkspace:
    """Context manager for a temporary BIN->LOG decode workspace."""

    def __init__(self, path: Path, config: Bin2LogConfig) -> None:
        self._path = path
        self._config = config
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self.workdir: Path | None = None

    def __enter__(self) -> Path:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="mermaid-bin2log-")
        self.workdir = Path(self._tmpdir.name)
        bin_copy = self.workdir / self._path.name
        shutil.copy2(self._path, bin_copy)
        _run_log_decoder(self.workdir, self._config)
        return self.workdir

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()


def _decoded_log_workspace(path: Path, config: Bin2LogConfig) -> _DecodedLogWorkspace:
    """Create a temporary workspace containing manufacturer-decoded LOG files."""

    return _DecodedLogWorkspace(path, config)


def _run_log_decoder(workdir: Path, config: Bin2LogConfig) -> None:
    """Invoke the external decoder script for the actual BIN->LOG decode step."""

    harness = """
from pathlib import Path
import os
import runpy
import sys

decoder_script = Path(sys.argv[1]).resolve()
workdir = Path(sys.argv[2]).resolve()

sys.path.insert(0, str(decoder_script.parent))
namespace = runpy.run_path(str(decoder_script))

decrypt_all = namespace["decrypt_all"]

workdir_str = str(workdir) + os.sep
decrypt_all(workdir_str)
"""
    result = subprocess.run(
        [
            str(config.python_executable),
            "-c",
            harness,
            str(config.decoder_script),
            str(workdir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Bin2LogError(
            _format_subprocess_failure(result.returncode, result.stdout, result.stderr)
        )


def _handle_database_update_result(
    config: Bin2LogConfig,
    *,
    stdout: str,
    stderr: str,
) -> None:
    """Apply the configured preflight policy to database update output."""

    problem_detail = _database_update_problem_detail(stdout, stderr)
    if problem_detail is None:
        _write_preflight_status(
            config,
            effective_mode=config.preflight_mode,
            database_update_succeeded=True,
            continued_after_failure=False,
            failure_detail=None,
        )
        return
    if config.preflight_mode == "strict":
        _write_preflight_status(
            config,
            effective_mode="strict",
            database_update_succeeded=False,
            continued_after_failure=False,
            failure_detail=problem_detail,
        )
        raise Bin2LogError(
            f"External decoder database update failed: {problem_detail}"
        )
    _write_preflight_status(
        config,
        effective_mode="cached_degraded",
        database_update_succeeded=False,
        continued_after_failure=True,
        failure_detail=problem_detail,
    )
    _warn_cached_preflight(problem_detail)


def _write_preflight_status(
    config: Bin2LogConfig,
    *,
    effective_mode: Literal["strict", "cached", "cached_degraded"],
    database_update_succeeded: bool,
    continued_after_failure: bool,
    failure_detail: str | None,
) -> None:
    """Persist database update status when a durable output directory is configured."""

    status_dir = config.preflight_status_dir
    if status_dir is None:
        return

    status_dir.mkdir(parents=True, exist_ok=True)
    status_path = status_dir / "preflight_status.json"
    payload = {
        "requested_mode": config.preflight_mode,
        "effective_mode": effective_mode,
        "database_update_attempted": True,
        "database_update_succeeded": database_update_succeeded,
        "continued_after_failure": continued_after_failure,
        "failure_detail": failure_detail,
        "decoder_python": str(config.python_executable),
        "decoder_script": str(config.decoder_script),
        "written_at": format_utc_datetime(datetime.now(timezone.utc)),
    }
    status_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _database_update_problem_detail(stdout: str, stderr: str) -> str | None:
    """Extract preprocess-reported database update problems from subprocess output."""

    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    problem_lines = [
        line.strip()
        for line in combined.splitlines()
        if line.strip().startswith("Error ") or line.strip().startswith('Exception:')
    ]
    if not problem_lines:
        return None
    return "; ".join(problem_lines)


def _warn_cached_preflight(detail: str) -> None:
    """Emit an explicit warning when cached preflight mode continues after refresh failure."""

    print(
        "WARNING: database_update failed; continuing in cached preflight mode: "
        f"{detail}",
        file=sys.stderr,
    )


def _format_subprocess_failure(returncode: int, stdout: str, stderr: str) -> str:
    """Format a subprocess failure message consistently."""

    stderr_text = stderr.strip()
    stdout_text = stdout.strip()
    detail = stderr_text or stdout_text or f"exit code {returncode}"
    return f"External decoder failed: {detail}"
