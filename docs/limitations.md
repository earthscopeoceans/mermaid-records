# Limitations

## Manifest and state artifacts depend on mode

`Stateful` and `stateless` runs do not persist the same side artifacts.
Both successful non-dry-run modes write the root
`normalization_manifest.json`, which inventories normalized corpus data but
does not provide incremental state.

Stateful mode:

- writes `manifests/latest.json`
- writes one unique `manifests/runs/<run_id>/...` directory per executed run, using run IDs like `2026-04-21T22:17:31Z-11a3ef`
- writes `state/pruned_records.jsonl`
- persists malformed/skipped-source recovery artifacts in the per-run manifest directory

Stateless mode:

- writes no `manifests/` directory
- writes no `state/`
- does not persist malformed/skipped-source recovery artifacts separately from the normalized JSONL outputs
- cannot target an output tree that already contains `manifests/`

`preflight_status.json` is different from manifests: it is written only when the current run performs BIN decode preflight with a durable instrument output directory, regardless of whether the run is stateful or stateless.

In `stateful` mode, `manifests/latest.json` includes `preflight_status` only for runs that produced that artifact. When no preflight runs, the field is absent rather than `null`, and stale preflight artifacts from earlier runs must not be propagated.

## MER event preservation is structured, not verbatim

MER event normalization does not preserve the full original `<EVENT>...</EVENT>` block as one byte-for-byte field.

Successful normalized event rows preserve structured components instead:

- `raw_info_line`
- `raw_format_line` when a `<FORMAT>` line exists
- `encoded_payload`
- payload accounting fields such as `encoded_payload_byte_count`, `data_payload_nbytes`, and `payload_length_matches_expected`

Important consequences:

- downstream consumers should not expect exact reconstruction of the original event block from one stored verbatim field
- Stanford PSD event blocks that omit `<FORMAT>` are still valid and normalize with `raw_format_line = null`
- payload byte counts measure only the bytes inside `<DATA>...</DATA>` and exclude surrounding framing whitespace
- a length-framed payload is bounded by its declared byte count, so delimiter-like bytes inside it are preserved; a format-less event with multiple DATA close delimiters is quarantined rather than normalized ambiguously
- repeated complete `ENVIRONMENT` or `PARAMETERS` sections are preserved as ordinary metadata records and are also reported in the stateful run's malformed-MER quarantine log

## Allowed transformations

Normalization is conservative, but it is not a raw byte dump. The following transformations are intentionally allowed:

- line-read newline normalization such as stripping trailing `\r\n`
- canonicalizing parsed LOG/MER filename references by replacing `/` with `_`
- canonicalizing parsed LOG rollover targets to normalized `.LOG` filenames
- parsing source text into explicit structured fields without adding inferred interpretation
- resolving canonical `instrument_id` values from recognized serial naming rules when available
- resolving `instrument_serial` from the same dataset identity when available, with raw-prefix fallback for ambiguous stateless/prefix-only inputs
- suffixing normalized JSONL family filenames with `instrument_serial`
- materializing the canonical top-level serial-suffixed JSONL file set as empty files when a family has no rows

No additional interpretation-oriented transformations are part of the current contract. In particular, the normalization layer does not do coordinate conversion, derived intervals, mission inference, or waveform interpretation.

## Mode-specific rerun limits

`Stateful` mode can append, rewrite, noop, and prune because it has persisted source state.

`Stateless` mode cannot do that safely because it has no per-instrument run
manifests or source state. Its root normalization manifest is a
content-addressed corpus inventory, not incremental planning state. The
stateless rerun contract is therefore intentionally narrower:

- reruns rewrite the targeted package-owned family outputs
- reruns do not append to prior stateless JSONL files
- reruns do not silently duplicate rows

## Fixture-backed coverage is partial

The tracked release-facing fixtures exercise important current cases, including:

- older-generation direct `LOG` + `MER` data
- BIN-backed families with decoded `LOG` fixtures
- compact real PSD / Stanford-style raw `MER` examples, including a metadata-only file and event blocks without `<FORMAT>`

They do not prove coverage for every float generation, every external decoder behavior, or every malformed raw artifact pattern seen in the field.

## External decoder boundary

`BIN` handling still depends on an external preprocess/decode workflow. `mermaid-records` does not replace that decoder, and any decoder-environment failures, database update issues, or upstream decode differences remain outside the normalization layer itself.
