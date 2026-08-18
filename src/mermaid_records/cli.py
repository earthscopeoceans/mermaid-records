# Bathymetrix™
# https://bathymetrix.com
# © 2026 Bathymetrix, LLC
# Author: Joel D. Simon <jdsimon@bathymetrix.com>
# SPDX-License-Identifier: MIT

"""Command line interface for mermaid_records."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import time

from . import __version__
from .bin2log import Bin2LogConfig
from .discovery import iter_bin_files
from .normalize_pipeline import (
    DryRunSummary,
    NormalizationPipelineSummary,
    instrument_has_bin_inputs,
    run_normalization_pipeline,
)
from .parse_instrument_name import parse_instrument_name


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""

    parser = argparse.ArgumentParser(
        prog="mermaid-records",
        description="Bathymetrix™ CLI for the MERMAID normalization pipeline.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    normalize = subparsers.add_parser(
        "normalize",
        help="Run the normalization pipeline from raw inputs to JSONL outputs.",
    )
    input_group = normalize.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "-i",
        "--input-root",
        type=Path,
        help="Root directory containing BIN, LOG, and/or MER inputs.",
    )
    input_group.add_argument(
        "--input-file",
        nargs="+",
        type=str,
        action="append",
        default=None,
        help="Explicit raw source file(s) to normalize in stateless mode. Accepts comma-separated and/or space-separated lists.",
    )
    normalize.add_argument(
        "--instrument-serial",
        type=_parse_instrument_serial,
        help="Limit stateful --input-root normalization to one full instrument serial, for example 452.020-P-0030.",
    )
    normalize.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Directory where normalized JSONL outputs will be written.",
    )
    normalize.add_argument(
        "--decoder-python",
        type=Path,
        default=None,
        help="Python executable for the external manufacturer decoder environment.",
    )
    normalize.add_argument(
        "--decoder-script",
        type=Path,
        default=None,
        help="Path to external preprocess.py decoder script.",
    )
    normalize.add_argument(
        "--preflight-mode",
        choices=("strict", "cached"),
        default="strict",
        help="BIN decode preflight policy: strict requires successful live refresh; cached warns and continues on cached decoder state.",
    )
    normalize.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the normalization plan and file-level diffs without writing files.",
    )
    normalize.add_argument(
        "-f",
        "--force",
        dest="force_rewrite",
        action="store_true",
        help="Rewrite all targeted instrument outputs instead of using append/noop incremental decisions.",
    )
    normalize.add_argument(
        "--json",
        action="store_true",
        help="Print dry-run output as structured JSON instead of a human-readable plan. Requires --dry-run.",
    )
    normalize.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print an expanded end-of-run summary.",
    )
    normalize.set_defaults(handler=_handle_normalize)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""

    invocation_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(invocation_args)
    args.generation_command = shlex.join(["mermaid-records", *invocation_args])
    _validate_args(parser, args)
    handler = getattr(args, "handler")
    return handler(args)


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject unsupported flag combinations before running the CLI."""

    if args.command == "normalize" and args.json and not args.dry_run:
        parser.error("--json requires --dry-run")
    if (
        args.command == "normalize"
        and args.instrument_serial is not None
        and args.input_root is None
    ):
        parser.error("--instrument-serial requires --input-root")


def _handle_normalize(args: argparse.Namespace) -> int:
    """Handle the normalize subcommand."""

    started = time.perf_counter()
    output_dir = _resolve_output_dir(args.output_dir)
    input_files = _parse_input_files(args.input_file)
    decoder_required = _decoder_required(
        input_root=args.input_root,
        input_files=input_files,
        instrument_serial=args.instrument_serial,
    )
    config = None
    decoder_python = _resolve_decoder_python(args.decoder_python)
    decoder_script = _resolve_decoder_script(args.decoder_script)
    if decoder_required:
        if decoder_python is None:
            raise SystemExit(
                "BIN inputs require a decoder Python. Provide --decoder-python or set "
                "MERMAID_RECORDS_DECODER_PYTHON."
            )
        if decoder_script is None:
            raise SystemExit(
                "BIN inputs require a decoder script. Provide --decoder-script or set "
                "MERMAID_RECORDS_DECODER_SCRIPT."
            )
    if decoder_python is not None or decoder_script is not None:
        if decoder_python is None or decoder_script is None:
            raise SystemExit(
                "--decoder-python and --decoder-script must be provided together, either "
                "explicitly or via MERMAID_RECORDS_DECODER_PYTHON and "
                "MERMAID_RECORDS_DECODER_SCRIPT."
            )
        config = Bin2LogConfig(
            python_executable=decoder_python,
            decoder_script=decoder_script,
            preflight_mode=args.preflight_mode,
        )

    summary = run_normalization_pipeline(
        args.input_root,
        output_dir=output_dir,
        config=config,
        input_files=input_files,
        instrument_serial=args.instrument_serial,
        dry_run=args.dry_run,
        force_rewrite=args.force_rewrite,
        progress=_cli_progress,
        generation_command=args.generation_command,
    )
    elapsed_s = time.perf_counter() - started
    payload = summary.to_dict()
    if args.dry_run and args.json:
        print(json.dumps(payload, sort_keys=True))
    elif args.dry_run:
        print(
            _format_human_dry_run(
                payload,
                summary=summary,
                elapsed_s=elapsed_s,
                verbose=args.verbose,
            )
        )
    else:
        print(_format_run_summary(summary, elapsed_s=elapsed_s, verbose=args.verbose))
    return 0


def _parse_input_files(values: list[list[str]] | None) -> list[Path] | None:
    """Flatten repeated --input-file arguments into a list of paths."""

    if not values:
        return None

    paths: list[Path] = []
    for group in values:
        for item in group:
            for token in item.split(","):
                token = token.strip()
                if token:
                    paths.append(Path(token))
    return paths


def _parse_instrument_serial(value: str) -> str:
    """Validate and return one canonical full instrument serial."""

    try:
        return parse_instrument_name(value).serial
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _resolve_output_dir(output_dir: Path | None) -> Path:
    if output_dir is not None:
        return output_dir
    mermaid_root = os.environ.get("MERMAID")
    if mermaid_root:
        return Path(mermaid_root) / "records"
    raise SystemExit(
        "Output directory is unresolved: --output-dir was not given and MERMAID is not set. "
        "Provide --output-dir or set MERMAID."
    )


def _resolve_decoder_python(decoder_python: Path | None) -> Path | None:
    if decoder_python is not None:
        return decoder_python
    configured = os.environ.get("MERMAID_RECORDS_DECODER_PYTHON")
    return Path(configured) if configured else None


def _resolve_decoder_script(decoder_script: Path | None) -> Path | None:
    if decoder_script is not None:
        return decoder_script
    configured = os.environ.get("MERMAID_RECORDS_DECODER_SCRIPT")
    return Path(configured) if configured else None


def _decoder_required(
    *,
    input_root: Path | None,
    input_files: list[Path] | None,
    instrument_serial: str | None,
) -> bool:
    if input_root is not None:
        if instrument_serial is not None:
            return instrument_has_bin_inputs(
                input_root,
                instrument_serial=instrument_serial,
            )
        return any(True for _ in iter_bin_files(input_root))
    return any(path.suffix.upper() == ".BIN" for path in input_files or [])


def _format_dry_run(payload: dict[str, object]) -> str:
    lines: list[str] = []
    for instrument_payload in payload["instruments"]:
        counts = instrument_payload["counts"]
        lines.append(f"INSTRUMENT {Path(instrument_payload['output_dir']).name}")
        lines.append(
            "  files: "
            f"total={counts['total']} new={counts['new']} changed={counts['changed']} "
            f"removed={counts['removed']} unchanged={counts['unchanged']}"
        )
        for family_name in ("log", "mer"):
            family = instrument_payload["families"][family_name]
            lines.append(f"  {family_name}: {family['action']}")
            for change_kind in ("new", "changed", "removed"):
                rows = [row for row in family["file_diffs"] if row["change_kind"] == change_kind]
                if not rows:
                    continue
                lines.append(f"    {change_kind}:")
                for row in rows:
                    lines.append(f"      - {_format_diff_row(row)}")
            if family["decoder_invalidated"]:
                lines.append("    decoder-invalidated:")
                for row in family["decoder_invalidated"]:
                    lines.append(f"      - {_format_diff_row(row)}")
    return "\n".join(lines)


def _format_human_dry_run(
    payload: dict[str, object],
    *,
    summary: DryRunSummary,
    elapsed_s: float,
    verbose: bool,
) -> str:
    sections = [_format_run_summary(summary, elapsed_s=elapsed_s, verbose=verbose)]
    plan = _format_dry_run(payload)
    if plan:
        sections.append(plan)
    return "\n\n".join(sections)


def _format_run_summary(
    summary: NormalizationPipelineSummary | DryRunSummary,
    *,
    elapsed_s: float,
    verbose: bool,
) -> str:
    metrics = summary.metrics
    lines = [
        "DRY RUN SUMMARY" if isinstance(summary, DryRunSummary) else "NORMALIZATION SUMMARY",
        f"  mode: {summary.mode}",
        f"  raw files processed: {metrics.raw_files_processed}",
        (
            "  raw files: "
            f"new={metrics.raw_files_new} changed={metrics.raw_files_changed} "
            f"removed={metrics.raw_files_removed}"
        ),
        (
            "  instruments: "
            f"append={metrics.instruments_append} rewrite={metrics.instruments_rewrite} "
            f"noop={metrics.instruments_noop}"
        ),
    ]

    if isinstance(summary, DryRunSummary):
        lines.append(
            "  output totals: "
            "log records written=not evaluated log records removed=not evaluated "
            "mer records written=not evaluated mer records removed=not evaluated"
        )
        lines.append(
            "  issues: "
            "malformed log lines=not evaluated skipped log files=not evaluated "
            "malformed mer blocks=not evaluated skipped mer files=not evaluated"
        )
    else:
        lines.append(
            "  output totals: "
            f"log records written={metrics.log_records_written} "
            f"log records removed={metrics.log_records_removed} "
            f"mer records written={metrics.mer_records_written} "
            f"mer records removed={metrics.mer_records_removed}"
        )
        lines.append(
            "  issues: "
            f"malformed log lines={metrics.malformed_log_lines} "
            f"skipped log files={metrics.skipped_log_files} "
            f"malformed mer blocks={metrics.malformed_mer_blocks} "
            f"skipped mer files={metrics.skipped_mer_files}"
        )

    lines.append(
        "  decode: "
        f"bin files decoded={metrics.bin_files_decoded} "
        f"preflight mode={metrics.preflight_mode or 'n/a'}"
    )
    if not isinstance(summary, DryRunSummary):
        lines.extend(_format_log_family_assignment_table(metrics))
    lines.append(f"  runtime: total wall-clock time={elapsed_s:.2f}s")

    if verbose:
        lines.extend(
            [
                "    family actions:",
                (
                    "      log: "
                    f"append={metrics.log_instruments_append} rewrite={metrics.log_instruments_rewrite} "
                    f"noop={metrics.log_instruments_noop}"
                ),
                (
                    "      mer: "
                    f"append={metrics.mer_instruments_append} rewrite={metrics.mer_instruments_rewrite} "
                    f"noop={metrics.mer_instruments_noop}"
                ),
                "      per-instrument actions:",
                *_format_per_instrument_outputs(summary),
                f"  output root: {summary.output_dir}",
            ]
        )
        if summary.input_root is not None:
            lines.append(f"  input root: {summary.input_root}")
        if summary.input_files:
            lines.append(f"  explicit input files: {len(summary.input_files)}")

    return "\n".join(lines)


def _format_log_family_assignment_table(metrics) -> list[str]:
    if not metrics.log_family_source_line_counts:
        return []
    difference = metrics.log_source_line_assignments - metrics.log_ordinary_lines_examined
    family_width = max(
        len("Family"),
        *(len(family) for family in metrics.log_family_source_line_counts),
        len("Ordinary LOG lines"),
    )
    count_width = max(
        len("Records"),
        *(len(f"{count:,}") for count in metrics.log_family_source_line_counts.values()),
        len(f"{metrics.log_source_line_assignments:,}"),
        len(f"{metrics.log_ordinary_lines_examined:,}"),
        len(f"{difference:,}"),
    )
    separator = "-" * (family_width + 2 + count_width)
    lines = [
        "",
        f"{'Family':<{family_width}}  {'Records':>{count_width}}",
        separator,
    ]
    for family, count in sorted(metrics.log_family_source_line_counts.items()):
        lines.append(f"{family:<{family_width}}  {count:>{count_width},}")
    lines.extend(
        [
            separator,
            f"{'TOTAL':<{family_width}}  {metrics.log_source_line_assignments:>{count_width},}",
            "",
            f"{'Ordinary LOG lines':<{family_width}}  {metrics.log_ordinary_lines_examined:>{count_width},}",
            f"{'Difference':<{family_width}}  {difference:>{count_width},}",
        ]
    )
    return lines


def _format_per_instrument_outputs(
    summary: NormalizationPipelineSummary | DryRunSummary,
) -> list[str]:
    if isinstance(summary, NormalizationPipelineSummary):
        return [
            (
                f"        {Path(item.output_dir).name}: "
                f"log_family={item.log_action} mer_family={item.mer_action} | "
                f"sources bin={item.bin_count} log={item.log_count} mer={item.mer_count}"
            )
            for item in summary.processed_instruments
        ]

    return [
        (
            f"        {Path(item['output_dir']).name}: "
            f"log_family={item['families']['log']['action']} "
            f"mer_family={item['families']['mer']['action']} | "
            f"sources "
            f"bin={sum(1 for row in item['families']['log']['file_diffs'] if row['source_kind'] == 'bin')} "
            f"log={sum(1 for row in item['families']['log']['file_diffs'] if row['source_kind'] == 'log')} "
            f"mer={sum(1 for row in item['families']['mer']['file_diffs'] if row['source_kind'] == 'mer')}"
        )
        for item in summary.instruments
    ]


def _format_diff_row(row: dict[str, object]) -> str:
    name = Path(row["source_file"]).name
    previous_size = int(row["previous_size_bytes"])
    current_size = int(row["current_size_bytes"])
    if row["change_kind"] == "changed":
        return f"{name} (hash changed, {previous_size} B -> {current_size} B)"
    return f"{name} ({previous_size} B -> {current_size} B)"


def _cli_progress(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
