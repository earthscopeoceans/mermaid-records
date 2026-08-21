[![Tests](https://github.com/Bathymetrix/mermaid-records/actions/workflows/ci.yml/badge.svg)](https://github.com/Bathymetrix/mermaid-records/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://pypi.org/project/mermaid-records/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

# mermaid-records

`mermaid-records` normalizes raw MERMAID `BIN`, `LOG`, and `MER` artifacts into structured JSONL record families. It is a parsing and normalization layer only. It does not perform coordinate conversion, interval inference, waveform analysis, or higher-level interpretation.

## CRITICAL SOURCE-AUTHORITY RULE: BIN-DECODED LOG WINS

When a `BIN` and an existing `LOG` have the same filename stem, only the LOG
decoded from that `BIN` is normalized. The existing same-stem LOG is excluded
from normalization, manifests, diffs, and incremental planning, but is never
deleted or modified.

Existing LOG files remain authoritative when no same-stem BIN exists. This is
required for earlier instruments that transmitted LOG data without BIN data.
LOG-family `source_file` values record the authoritative input basename:
the native `.LOG` basename for LOG-only inputs and the `.BIN` basename for
BIN-derived content.

## Current contract

The release-facing contract is intentionally narrow:

- supported raw inputs are `BIN`, `LOG`, and `MER`
- the installed CLI surface is `mermaid-records normalize`
- the package-root Python API is metadata-only (`mermaid_records.__version__`, authorship, and license metadata)
- normalized JSONL outputs are the primary supported output
- every normalized JSONL row includes `mermaid_records_version`, sourced from
  the package’s canonical version metadata

`BIN` handling still depends on an external decoder step that produces `LOG` content. `mermaid-records` owns normalization around that seam; it does not reimplement the manufacturer decoder.

Version 2.0.0 is a deliberate post-v1 breaking output contract change. Normalized JSONL filenames now include the instrument serial, and every normalized record row includes both `instrument_id` and `instrument_serial`.

## Installation

```bash
python -m pip install .
```

For development:

```bash
python -m pip install -e .[dev]
```

## Canonical CLI example

Fixture-backed example; this command does not require the external BIN decoder:

```bash
mermaid-records normalize \
  --input-root data/fixtures/452.020-P-06 \
  --output-dir /tmp/mermaid-records-example
```

Quick usage flags:

- Input selection: `--input-root` for `stateful mode`, optionally scoped with
  `--instrument-serial <full-serial>`, or `--input-file` for explicit
  `stateless mode` runs
- Output: `--output-dir`; if omitted, the CLI uses `$MERMAID/records` when `MERMAID` is set
- BIN decoder and preflight: `--decoder-python` and `--decoder-script` (used together), plus `--preflight-mode {strict,cached}`
- Planning and reporting: `--dry-run`, `--json` (dry-run only), and `--verbose` / `-v`
- Rewrite control: `-f`, `--force` to force targeted family rewrites instead of append/noop incremental decisions

See [docs/cli.md](docs/cli.md) for the full CLI reference.

Developer-facing JSONL family references:

- [LOG record family schemas](docs/log_record_family_schemas.md)
- [MER record family schemas](docs/mer_record_family_schemas.md)

## Execution modes

`mermaid-records normalize` has two execution modes:

- `stateful` mode is selected by `--input-root`
- `stateless` mode is selected by `--input-file`

`Stateful` mode persists `manifests/` and `state/` per instrument and enables incremental append/rewrite/noop planning. `Stateless` mode does not write those per-instrument bookkeeping directories, does not use incremental state, and rewrites the targeted package-owned JSONL family outputs for each explicit run. Both modes write the root `normalization_manifest.json` after successful non-dry-run normalization.

`--instrument-serial`, used with `--input-root`, narrows a stateful corpus run
to one full serial such as `452.020-P-0030`. It does not touch manifests,
outputs, or decoder state for other instruments.

That `stateless` rewrite contract is intentional: rerunning the same explicit `--input-file` set does not silently duplicate JSONL rows.

When BIN decode preflight runs with a durable instrument output directory, the current run writes `preflight_status.json` at instrument root. This is tied to BIN decode, not to `stateful` mode by itself.

In `stateful` mode, `manifests/latest.json` includes `preflight_status` only when the current run produced that artifact. When no preflight runs, the field is absent rather than `null`, and stale preflight artifacts from earlier runs are not carried forward.

## Output layout

Typical per-instrument outputs look like:

```text
<output-dir>/
  normalization_manifest.json
  452.020-P-06/
    log_acquisition_records.452.020-P-06.jsonl
    log_ascent_request_records.452.020-P-06.jsonl
    log_battery_records.452.020-P-06.jsonl
    log_gps_records.452.020-P-06.jsonl
    log_parameter_records.452.020-P-06.jsonl
    log_pressure_temperature_records.452.020-P-06.jsonl
    log_ctd_records.452.020-P-06.jsonl
    log_testmode_records.452.020-P-06.jsonl
    log_iridium_records.452.020-P-06.jsonl
    log_unclassified_records.452.020-P-06.jsonl
    mer_environment_records.452.020-P-06.jsonl
    mer_event_records.452.020-P-06.jsonl
    mer_parameter_records.452.020-P-06.jsonl
    manifests/              # stateful mode only
    state/                  # stateful mode only
    preflight_status.json   # only when the current run's BIN decode preflight ran
```

Every per-instrument output directory materializes the canonical top-level JSONL file set even when some families are empty.

`normalization_manifest.json` inventories the normalized JSONL corpus with
per-file byte sizes and SHA-256 checksums and publishes a deterministic
content-addressed snapshot identifier. See
[docs/normalization_manifest.md](docs/normalization_manifest.md) for the
schema and independent verification recipe.

`instrument_id` is the station/instrument identifier analogous to `kstnm`, for example `P0006`. `instrument_serial` is the full hardware/dataset serial, for example `452.020-P-06`. For stateless explicit-file runs, the pipeline uses the same path/log-derived serial resolution as stateful mode when available and otherwise falls back to the raw file prefix.

## Fixture coverage

The release-facing fixtures intentionally cover a few concrete float/data classes, not the full fleet:

- `452.020-P-06`: older-generation direct `LOG` + `MER` family with no `BIN` branch
- `465.152-R-0001`: compact real PSD / Stanford-style raw `BIN` + `MER` subset, including a metadata-only `MER` and an event-bearing `MER` with no `<FORMAT>` lines
- `467.174-T-0100`: BIN-backed family with tracked raw `BIN`, decoded `LOG`, raw `MER`, and `S61` fixture branches

These fixtures are representative test anchors for the implemented behavior. They do not claim coverage for every float generation, record family variant, or decoder edge case.

## Python API posture

`mermaid_records` exposes only conservative package metadata at the package root. Functional helpers are intentionally not re-exported there as a broader stable API promise.

## Documentation map

- [docs/cli.md](docs/cli.md) — CLI flags, execution modes, and safe usage patterns
- [docs/normalization_manifest.md](docs/normalization_manifest.md) — content-addressed corpus manifest schema and digest recipe
- [docs/limitations.md](docs/limitations.md) — preservation limits, mode-dependent artifacts, and allowed transformations
- [docs/ethos.md](docs/ethos.md) — scope discipline and design philosophy
- [docs/notes/normalization_release_contract.md](docs/notes/normalization_release_contract.md) — detailed behavioral reference

© 2026 Bathymetrix, LLC
