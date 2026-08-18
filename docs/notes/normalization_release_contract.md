# SPDX-License-Identifier: MIT

# Normalization Release Contract

This document is the release-facing contract for `mermaid-records`.
It describes the behavior that downstream callers, fixture audits, and future hardening work should treat as stable unless a deliberate breaking change is made.

Version 2.0.0 deliberately breaks the post-v1 output contract by adding `instrument_serial` to normalized rows, suffixing JSONL family filenames with that serial, and using separator-rich UTC run IDs.

Every normalized LOG and MER JSONL row includes top-level
`mermaid_records_version`, populated from the canonical package
`mermaid_records.__version__`.

## Scope

`mermaid-records` is scoped strictly to canonical normalization of raw MERMAID data:

- external `BIN -> LOG` decode
- `LOG -> JSONL`
- `MER -> JSONL`

Supported raw input file types are intentionally limited to:

- `BIN`
- `LOG`
- `MER`

Explicitly not supported:

- `S41`
- `S61`
- `RBR`
- other profile or auxiliary formats

Out of scope:

- analysis or timeline interpretation
- unit or coordinate conversions
- inferred durations from sample counts or rates
- inferred acquisition windows beyond explicit transition lines
- DET / REQ logic
- `CYCLE` / `CYCLE.h`

## Critical Source Authority: BIN-Decoded LOG Wins

When a `BIN` and an existing `LOG` have the same filename stem, the LOG
decoded from the BIN is the sole authoritative normalization source. The
existing same-stem LOG must be excluded before manifests, source diffs,
incremental planning, normalization, and audits are built.

An existing LOG remains authoritative when no same-stem BIN exists, preserving
support for earlier LOG-only instruments. Source precedence never authorizes
deleting or modifying either raw file.

Normalized LOG-family `source_file` values name the basename of that
authoritative original input: the native `.LOG` for LOG-only input, or the
`.BIN` for BIN-derived LOG content. Temporary decoded LOG filenames are never
recorded as authoritative provenance.

## Public CLI

Installed CLI surface:

- `mermaid-records normalize`

The `normalize` subcommand accepts exactly one input mode selector:

- `--input-root`
  - stateful mode
- `--input-file`
  - stateless mode

Stateful `--input-root` may additionally be scoped with
`--instrument-serial <full-serial>`. This is not a third execution mode. It
must isolate discovery, decoding, manifests, diffs, pruning, force cleanup,
and dry-run planning to the requested serial without touching other instrument
outputs.

Shared options:

- `--output-dir`
- `--instrument-serial` (only with `--input-root`)
- `--decoder-python`
- `--decoder-script`
- `--preflight-mode {strict,cached}`
- `--dry-run`
- `-f`, `--force`
- `--json` (only with `--dry-run`)
- `--verbose`

`--decoder-python` and `--decoder-script` must be supplied together when any `BIN` inputs are present.

## Python Import Surface

The supported Python package-root surface is intentionally limited to conservative metadata such as `mermaid_records.__version__`, authorship, and license metadata.

Functional modules and helper functions are implementation details unless a future contract explicitly promotes them. Downstream integrations should prefer the CLI and normalized file contracts over deep Python imports.

## Execution Modes

### Stateful

Stateful mode is triggered by `--input-root`.

Contract:

- manifests are read and written
- incremental rerun logic is enabled
- pruning is enabled
- instrument output directories use full serial names from `<serial>.vit` when available
- dry-run reuses the same planning and diff logic but writes nothing

### Stateless

Stateless mode is triggered by `--input-file`.

Contract:

- input is an explicit raw file list
- manifests are ignored and not written
- incremental rerun logic is disabled
- pruning is disabled
- the target output tree must not already contain `manifests/`
- dry-run writes no outputs, manifests, state files, or status files

## BIN Decode Preflight

BIN decode preflight has exactly two requested modes:

- `strict`
  - default
  - live `database_update(...)` refresh must succeed
  - failure is terminal
- `cached`
  - live `database_update(...)` refresh is still attempted
  - refresh failure may continue in degraded cached mode

When the current run performs BIN decode preflight with a durable output directory, `preflight_status.json` records:

- requested mode
- effective mode
- whether the refresh was attempted
- whether it succeeded
- whether the run continued after failure
- failure detail, if any
- decoder executable/script identity and write time

## Instrument Identity

Canonical `instrument_id` and `instrument_serial` resolution are centralized inside the normalization package.

Examples:

- `452.020-P-08` -> `instrument_id = P0008`
- `467.174-T-0100` -> `instrument_id = T0100`

`instrument_id` is the station/instrument identifier analogous to `kstnm`. `instrument_serial` is the full hardware/dataset serial such as `452.020-P-08`.

When a full serial is unavailable, the pipeline falls back conservatively to the raw file prefix for both the output directory and `instrument_serial`. Stateless `--input-file` runs use nearby full serial path context or LOG serial lines when available; otherwise they use that same raw-prefix fallback.

## Output Layout

At the normalized output root and per instrument:

```text
<output_root>/
  normalization_manifest.json
  <instrument-serial>/
    log_acquisition_records.<instrument-serial>.jsonl
    log_ascent_request_records.<instrument-serial>.jsonl
    log_gps_records.<instrument-serial>.jsonl
    log_pressure_temperature_records.<instrument-serial>.jsonl
    log_battery_records.<instrument-serial>.jsonl
    log_parameter_records.<instrument-serial>.jsonl
    log_testmode_records.<instrument-serial>.jsonl
    log_ctd_records.<instrument-serial>.jsonl
    log_iridium_records.<instrument-serial>.jsonl
    log_unclassified_records.<instrument-serial>.jsonl
    mer_environment_records.<instrument-serial>.jsonl
    mer_parameter_records.<instrument-serial>.jsonl
    mer_event_records.<instrument-serial>.jsonl
    preflight_status.json  # only when the current run's BIN decode preflight ran
    manifests/
      latest.json
      runs/
        <run_id>/
          run.json
          outputs.json
          source_state.json
          input_file_diffs.jsonl
          malformed_log_lines.jsonl
          skipped_log_files.jsonl
          malformed_mer_blocks.jsonl
          skipped_mer_files.jsonl
          preflight_status.json
    state/
      pruned_records.jsonl
```

Notes:

- `normalization_manifest.json` is written atomically after a successful
  non-dry-run pipeline and content-addresses all normalized JSONL files under
  the output root. See `docs/normalization_manifest.md` for its schema and
  exact digest recipe.
- JSONL field ordering is frozen semantically: provenance/identity, then time, then family metadata, then payload/accounting, then raw fallback fields.
- Per-instrument `manifests/` and `state/` directories are stateful-mode
  artifacts; stateless mode writes neither. Both modes write the root
  `normalization_manifest.json`.
- `preflight_status.json` at instrument root is present only when the current run performed BIN preflight with a durable output directory.
- `latest.json` points to the most recent run for that instrument.
- `latest.json` includes `preflight_status` only when the current run produced that artifact.
- When no preflight runs, `latest.json` omits `preflight_status` rather than storing `null`.
- Stale preflight artifacts from earlier runs must not be propagated to a new run.
- `run.json` stores run metadata and status.
- `outputs.json` stores output inventory, row counts, and `instrument_serial`.
- `source_state.json` stores raw source identity, `instrument_serial`, and decoder-state identity.
- `input_file_diffs.jsonl` stores one row per raw source file with file-level diff fields only.
- `malformed_log_lines.jsonl`, `skipped_log_files.jsonl`, `malformed_mer_blocks.jsonl`, and `skipped_mer_files.jsonl` store per-run recovery/reporting artifacts for stateful runs.
- `state/pruned_records.jsonl` stores removed-source observations from stateful reruns.

## JSONL Filenames

LOG families:

- `log_acquisition_records.<instrument_serial>.jsonl`
- `log_ascent_request_records.<instrument_serial>.jsonl`
- `log_gps_records.<instrument_serial>.jsonl`
- `log_pressure_temperature_records.<instrument_serial>.jsonl`
- `log_battery_records.<instrument_serial>.jsonl`
- `log_parameter_records.<instrument_serial>.jsonl`
- `log_testmode_records.<instrument_serial>.jsonl`
- `log_ctd_records.<instrument_serial>.jsonl`
- `log_iridium_records.<instrument_serial>.jsonl`
- `log_unclassified_records.<instrument_serial>.jsonl`

MER families:

- `mer_environment_records.<instrument_serial>.jsonl`
- `mer_parameter_records.<instrument_serial>.jsonl`
- `mer_event_records.<instrument_serial>.jsonl`

## JSONL Record Schemas

### LOG

LOG record families are mutually exclusive. Each ordinary LOG source line is represented exactly once in normalized output, either by a dedicated record derived from that line or as part of a grouped/contextual record whose provenance includes that source line. No ordinary LOG source line may contribute to more than one LOG family, and no ordinary LOG source line may be omitted from all LOG families.

Shared direct LOG record fields:

- `source_file` is the basename-only authoritative raw input (`.LOG` or `.BIN`);
  same-stem BIN+LOG input records the `.BIN`
- `source_line_number`
- `record_time`
- `log_epoch_time`
- `instrument_id`
- `instrument_serial`
- `mermaid_records_version`
- `source_container`
- `source_file`
- `subsystem`
- `code`
- `message`
- `raw_line`
- `severity`
- `message_kind`
- `switched_to_log_file` (only for parsed rollover banner rows such as `*** switching to ... ***`)

`log_acquisition_records.<instrument_serial>.jsonl`

- all operational provenance/source fields
- `acquisition_state`
- `acquisition_evidence_kind`

`log_ascent_request_records.<instrument_serial>.jsonl`

- all operational provenance/source fields
- `ascent_request_state`

`log_gps_records.<instrument_serial>.jsonl`

- all operational provenance/source fields
- `gps_record_kind`
- `raw_values`

`log_parameter_records.<instrument_serial>.jsonl`

- grouped startup/dive-parameter episodes preserved from LOG continuation lines
- `instrument_id`
- `instrument_serial`
- `source_file`
- `episode_index`
- `line_start_index`
- `line_end_index`
- `source_line_numbers`
- `start_record_time`
- `end_record_time`
- `start_log_epoch_time`
- `end_log_epoch_time`
- `raw_lines`

`log_testmode_records.<instrument_serial>.jsonl`

- grouped test-mode sessions preserved from LOGs
- `instrument_id`
- `instrument_serial`
- `source_file`
- `episode_index`
- `line_start_index`
- `line_end_index`
- `source_line_numbers`
- `start_record_time`
- `end_record_time`
- `start_log_epoch_time`
- `end_log_epoch_time`
- `raw_lines`

`log_ctd_records.<instrument_serial>.jsonl`

- grouped CTD/profil operational episodes preserved from LOGs
- currently classified from legacy SBE/PROFIL/STAGE source tags, but named for the CTD output product rather than one sensor manufacturer
- `instrument_id`
- `instrument_serial`
- `source_file`
- `episode_index`
- `line_start_index`
- `line_end_index`
- `source_line_numbers`
- `start_record_time`
- `end_record_time`
- `start_log_epoch_time`
- `end_log_epoch_time`
- `raw_lines`
- `ctd_sample_count`
- `ctd_samples`
  - `source_line_number`
  - `raw_values`
  - `pressure_cbar_tenths`
  - `temperature_mdegc_tenths`
  - `salinity_psu_thousandths`

`log_iridium_records.<instrument_serial>.jsonl`

- grouped session provenance/source fields
- `session_kind`
- `iridium_event_count`
- `iridium_events`
- nested `iridium_event_kind`
- nested upload fields such as `referenced_artifact`, `rate_bytes_per_s`, `byte_count`, `byte_offset`, `artifact_size_bytes`, and `uploaded_file_count`
- `disconnect_duration_s`

`log_pressure_temperature_records.<instrument_serial>.jsonl`

- all operational provenance/source fields
- `pressure_mbar`
- `temperature_mdegc`
- `pressure_dbar`
- `temperature_degc`
- `internal_pressure_pa`
- `external_pressure_mbar`
- `external_pressure_range_mbar`

`log_battery_records.<instrument_serial>.jsonl`

- all operational provenance/source fields
- `battery_record_kind`
- `voltage_mv`
- `current_ua`
- `minimum_voltage_mv`

`log_unclassified_records.<instrument_serial>.jsonl`

- Contains ordinary LOG rows that do not match a specific LOG family.
- all operational provenance/source fields
- `severity`
- `unclassified_reason`

### MER

Shared MER provenance fields:

- `instrument_id`
- `instrument_serial`
- `mermaid_records_version`
- `source_container`
- `source_file` (basename only in normalized JSONL outputs; full paths remain in manifest/run artifacts)

`mer_environment_records.<instrument_serial>.jsonl`

- shared MER provenance fields
- `environment_kind`
- `gpsinfo_date`
- `raw_values`
- `line`

`mer_parameter_records.<instrument_serial>.jsonl`

- shared MER provenance fields
- `parameter_kind`
- `raw_values`
- `line`

`mer_event_records.<instrument_serial>.jsonl`

- shared MER provenance fields
- `block_index`
- `event_index`
- `event_info_date`
- `event_rounds`
- `date`
- `rounds`
- `pressure`
- `temperature`
- `criterion`
- `snr`
- `trig`
- `detrig`
- `fname`
- `smp_offset`
- `true_fs`
- `endianness`
- `bytes_per_sample`
- `sampling_rate`
- `stages`
- `normalized`
- `length`
- `encoded_payload`
- `encoded_payload_byte_count`
- `data_payload_nbytes`
- `expected_payload_nbytes`
- `payload_length_matches_expected`
- optional `stanford_psd_processing` (file-level `STANFORD_PROCESS` values
  copied onto every event only when the source declares Stanford software and
  exactly one such parameter line)
- `raw_info_line`
- `raw_format_line`

### MER event context: ordinary acoustic versus Stanford PSD

Ordinary acoustic MER events are self-describing at the event level. Their
source `<INFO>` and `<FORMAT>` headers are repeated with every `<DATA>`
payload, so the normalized event row preserves its own trigger-related fields,
sample format, sampling rate, and declared payload length. Those header values
may be identical across a file, but each event carries its own source-declared
copy and the normalizer does not assume that they are invariant.

Stanford PSD event blocks are structurally different: they have `<INFO>` and
`<DATA>` but no per-event `<FORMAT>`. Their common processing configuration is
declared by a file-level `<STANFORD_PROCESS ... />` parameter line. When the
file declares Stanford software and exactly one such parameter line, its
literal values are copied into optional `stanford_psd_processing` on each PSD
event. This lets a consumer understand a PSD event and its `ROUNDS` value
without joining it back to a separate parameter-family row.

This is provenance-preserving context attachment, not waveform or mission
interpretation. If the relevant file-level parameter is absent or repeated,
the normalizer does not select one or infer an association; the event remains
without `stanford_psd_processing`, while all parameter lines are preserved in
`mer_parameter_records`.

## Persisted Manifest And State Schemas

`run_id` values use UTC ISO8601-style timestamps with separators plus a short random suffix, for example `2026-04-21T22:17:31Z-11a3ef`.

`manifests/latest.json`

- `run_id`
- `instrument_serial`
- `status`
- `started_at`
- `completed_at`
- `run_manifest`
- `outputs_manifest`
- `source_state_manifest`
- `preflight_status` when the current run produced `preflight_status.json`

`manifests/runs/<run_id>/run.json`

- `run_id`
- `instrument_serial`
- `started_at`
- `completed_at`
- `input_root`
- `output_root`
- `normalization_version`
- `preflight_mode`
- `status`

`manifests/runs/<run_id>/outputs.json`

- `instrument_serial`
- `jsonl_outputs`
  - each row contains `path` and `size_bytes`
- `counts`
  - object keyed by JSONL basename without `.jsonl`

`manifests/runs/<run_id>/source_state.json`

- `input_root`
- `instrument_serial`
- `normalization_version`
- `raw_sources`
  - each row contains `source_file`, `source_kind`, `size_bytes`, and `content_hash`
- `decoder_state`
  - `null` when no BIN-dependent decoder state applies
  - otherwise contains `decoder_python`, `decoder_python_version`, `decoder_script`, `decoder_script_hash`, `preflight_mode`, `database_bundle_hash`, `database_files`, and `decoder_git_commit`

`manifests/runs/<run_id>/input_file_diffs.jsonl`

- one row per raw source file
- fields:
  - `source_file`
  - `source_kind`
  - `instrument_id`
  - `instrument_serial`
  - `previous_exists`
  - `current_exists`
  - `previous_size_bytes`
  - `current_size_bytes`
  - `previous_hash`
  - `current_hash`
  - `change_kind`
  - `decoder_state_changed`
  - `run_id`

This file is strictly file-level. It does not store append/rewrite/noop decisions and does not contain standalone non-file invalidation rows.

`manifests/runs/<run_id>/malformed_log_lines.jsonl`

- one row per recoverably malformed LOG line recorded during a stateful run, including `instrument_id` and `instrument_serial`

`manifests/runs/<run_id>/skipped_log_files.jsonl`

- one row per skipped LOG file recorded during a stateful run, including `instrument_id` and `instrument_serial`

`manifests/runs/<run_id>/malformed_mer_blocks.jsonl`

- one row per recoverably malformed MER block or metadata line recorded during a stateful run, including `instrument_id` and `instrument_serial`

`manifests/runs/<run_id>/skipped_mer_files.jsonl`

- one row per skipped MER file recorded during a stateful run, including `instrument_id` and `instrument_serial`

`preflight_status.json`

- `requested_mode`
- `effective_mode`
- `database_update_attempted`
- `database_update_succeeded`
- `continued_after_failure`
- `failure_detail`
- `decoder_python`
- `decoder_script`
- `written_at`

`preflight_status.json` remains decoder-run status, not a normalized record stream; instrument identity for that artifact is provided by its containing instrument directory and the run/latest manifests.

`state/pruned_records.jsonl`

- `source_file`
- `source_kind`
- `instrument_id`
- `instrument_serial`
- `removed_at`

`removed_at` is the UTC timestamp when the pipeline detected and recorded the removal, not the underlying filesystem deletion time.

## Incremental Rerun Model

Incremental rerun decisions are per instrument and per family.

Behavior:

- append only when the only change is newly added raw source files
- rewrite when any previously seen raw source changes
- rewrite when any previously seen raw source is removed
- BIN-derived LOG outputs also rewrite when decoder state changes
- `noop` only when no relevant source or invalidation change is detected
- `--force` overrides incremental planning and forces targeted instrument families to rewrite

Decoder-state invalidation:

- decoder-state changes invalidate BIN-derived outputs only
- LOG/MER-only floats must not be invalidated solely by decoder-state changes

JSONL safety model:

- outputs are source-ordered, not time-sorted
- no in-place mutation of existing JSONL lines
- safe modification paths are:
  - append new records
  - full rewrite of the affected family outputs

## Pruning Behavior

When a previously tracked raw source file is missing in a later stateful run:

- the affected family is rewritten from the remaining current sources
- records from the removed source disappear because they are not re-emitted
- the removal is recorded in `state/pruned_records.jsonl`

## Dry-Run Behavior

Dry-run behavior:

- compute the same rerun decisions as a real stateful run
- report per-instrument and per-family actions such as `append`, `rewrite`, and `noop`
- be completely side-effect free
- support both human-readable and JSON dry-run output
- dry-run must not write files of any kind, including manifests, status files, or reports in the output tree
