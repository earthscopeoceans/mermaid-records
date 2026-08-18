# mermaid-records

These instructions supplement:

- the global Codex AGENTS (`~/.codex/AGENTS.md`)
- the shared MERMAID AGENTS (`$MERMAID/AGENTS.md`)

Before beginning work, read and follow:

- `$MERMAID/AGENTS.md`

If you cannot locate, read, or understand the shared MERMAID AGENTS, stop and
tell the user before proceeding. Do not silently continue using only this file.

If instructions conflict, this file takes precedence.

## Repository Scope

`mermaid-records` normalizes raw MERMAID `BIN`, `LOG`, and `MER` files into
structured record-family JSONL.

This repository owns parsing, structural extraction, source routing,
normalization, and normalization-state bookkeeping.

Do not add higher-level scientific or workflow interpretation, including:

- unit or coordinate conversion;
- inferred durations from sample counts and rates;
- DET/REQ association logic;
- inferred acquisition intervals;
- timeline construction;
- downstream catalog or waveform interpretation.

Normalize structure, not meaning.

`CYCLE` and `CYCLE.h` are out of scope unless explicitly requested.

## Source Authority

When a raw `BIN` and an existing `LOG` share the same filename stem, the
`BIN`-decoded `LOG` is the sole authoritative normalization source.

Ignore the same-stem raw `LOG` during normalization, manifests, incremental
planning, audits, and output generation.

Continue accepting raw `LOG` files only when no same-stem `BIN` exists.

Never modify raw inputs. If decoding may alter source files, decode only from
temporary copies.

Apply BIN-over-LOG precedence before computing incremental state so a shadowed
`LOG` cannot affect outputs.

`source_file` must always identify the authoritative original `.BIN` or `.LOG`,
never a temporary decoded file.

## Parsing Boundaries

Keep decoding, parsing, and interpretation separate.

- decoding converts `BIN` → `LOG`;
- parsing consumes `LOG` and `MER`;
- interpretation belongs downstream.

`database_update(...)` is a batch preflight operation, not a parser.

Preflight modes:

- `strict`: fail closed;
- `cached`: continue only when degraded operation is explicit and recorded.

## LOG Routing

Every parsed LOG line must belong to exactly one destination.

Resolve grouped routes (`parameter`, `testmode`, `ctd`, `iridium`) before
ordinary LOG-family classification.

Routing rules:

- one match → one family;
- no match → `log_unclassified_records`;
- multiple matches → fail loudly;
- no parsed line may disappear silently.

Use `log_` prefixes for LOG-derived outputs and `mer_` prefixes only for
MER-derived outputs.

Prefer coherent subsystem-oriented families over fragmented or miscellaneous
buckets.

### Acquisition

Treat:

- `acq started`
- `acq stopped`

as transition evidence.

Treat:

- `acq already started`
- `acq already stopped`

as state assertions only.

Do not infer acquisition intervals from assertions.

### Ascent Requests

Create ascent-request records only from explicit accepted or rejected request
messages.

Do not infer request state.

### GPS and Iridium

Emit GPS records only from explicit GPS source lines.

Emit one `log_iridium_records` row per explicit session (or contiguous
mid-session sequence).

Preserve:

- consumed `raw_lines`;
- literal `iridium_events`.

Do not infer commands, timing, uploads, or positions beyond the LOG text.

### Literal Telemetry

Route:

- `P...mbar,T...mdegC` → `log_pressure_temperature_records`;
- `battery ...mV, ...uA` → `log_battery_records`.

Unmatched measurement-style lines belong in
`log_unclassified_records` unless a new family is intentionally introduced.

### Filename References

Canonicalize parsed filename references by replacing `/` with `_`.

Emit rollover targets as canonical `.LOG` filenames.

Preserve rollover banners as operational records.

## MER Parsing

Parse each `.MER` into:

- one `MerFileMetadata`;
- zero or more `MerEventBlock`s.

For metadata:

- preserve raw `ENVIRONMENT`;
- preserve raw `PARAMETERS`;
- conservatively extract repeated `GPSINFO`, `DRIFT`, and `CLOCK`;
- preserve GPS coordinates as raw strings.

Valid Stanford PSD files may contain metadata with no `EVENT` blocks.

For event blocks:

- parse `INFO`;
- parse `FORMAT` when present;
- allow valid `INFO` + `DATA` without `FORMAT`;
- preserve `DATA` payload bytes without waveform interpretation;
- count only bytes inside the `DATA` element.

A single `.MER` may contain events associated with different dives.

Do not infer dive membership.

Preserve unknown MER record types.

## Normalized Records

Every JSONL row must include top-level
`mermaid_records_version`.

Except for explicitly preserved raw strings, timestamps must be UTC ISO-8601
with six fractional digits and a trailing `Z`.

Convert to UTC before formatting.

Use basename-only `source_file` in normalized records.

Maintain deterministic processing order.

Never modify JSONL files in place. Supported update mechanisms are:

- append;
- complete rewrite.

Every instrument output directory must contain the complete canonical family
set, including empty files.

## Instrument Identity

Maintain the distinction between:

- `instrument_serial` (full hardware serial, e.g. `467.174-T-0100`);
- `instrument_id` (canonical identifier, e.g. `T0100`).

Never conflate the two.

Derive canonical IDs in one centralized implementation.

## Execution Modes

Supported execution modes:

- `stateful`;
- `stateless`.

`--instrument-serial` scopes a stateful run; it is not a third mode.

Stateless mode:

- rewrites outputs;
- performs no incremental planning;
- rejects instrument directories containing stateful bookkeeping.

Stateful mode performs conservative incremental updates.

Dry-run and report-only modes must produce no filesystem side effects.

## Generated State

Every successful non-dry-run normalization writes an atomic:

`normalization_manifest.json`

`snapshot_id` is computed from normalized JSONL paths, sizes, and SHA-256
checksums only.

Exclude bookkeeping directories from snapshot computation.

`input_file_diffs.jsonl` records only raw file-level changes.

`preflight_status.json` is run-scoped.

`--force` may delete only package-owned generated artifacts.

Never delete unknown user files.

## Package Metadata

The canonical package version lives only in:

`src/mermaid_records/__init__.py`

`pyproject.toml` must obtain the version dynamically.

Do not duplicate version numbers.

For every user-visible behavior or public-interface change intended for a
release, bump the canonical package version before handoff. Do not bump the
version for tests, internal refactors, or documentation-only corrections that
do not change the released behavior.

The package root intentionally exports metadata only.

The repository uses the MIT license.

## Documentation

Whenever code changes:

- LOG-family schemas;
- filenames;
- routing;
- grouping;
- exclusivity rules;

update:

`docs/log_record_family_schemas.md`

Keep `CHANGELOG.md` current for user-visible changes.

Before adding audit infrastructure, inspect existing audit helpers for reuse.

Audit runs should remain isolated, preserve per-run logs, continue through
individual failures where appropriate, and retain access to the real decoder
environment while sandboxing generated outputs.
