# Changelog

All notable changes from this point forward should be recorded here.

## Unreleased

### Changed

- Changed `normalize --force-rewrite` to `normalize -f, --force` for targeted family rewrites.
- Added top-level `mermaid_records_version` package-version provenance to
  every normalized LOG and MER JSONL record.

### Added

- Added the unambiguous file-level `STANFORD_PROCESS` parameter set to each
  Stanford PSD event record, linking its source `ROUNDS` value to the
  applicable processing parameters without waveform interpretation.
- Added top-level `-v` as the short form of `--version`.
- Added a root `normalization_manifest.json` with per-file SHA-256 checksums
  and a deterministic content-addressed normalized-corpus snapshot identifier.
- Added `normalize --instrument-serial <full-serial>` to scope stateful
  `--input-root` normalization, decoder work, manifests, force cleanup, and
  dry-run planning to one instrument.
- Warn during stateful CLI normalization when an input root contains no `.BIN`, `.LOG`, or `.MER` source files.
- Initialized this changelog for forward-looking release notes.

### Release preparation

- Advanced the package version to `2.1.0-rc3`.
- Advanced the package version to `2.1.0-rc1`.

### Fixed

- Preserve delimiter-looking bytes inside length-framed MER DATA payloads;
  quarantine ambiguous format-less payloads instead of truncating them.
- Preserve repeated complete MER metadata sections and record a stateful-run
  quarantine diagnostic for user review.
- Prevent incomplete Iridium sessions from absorbing unrelated tagged LOG
  lines; those lines now return to their ordinary LOG-family routing.
- Prevent duplicate or conflicting LOG-derived rows by treating a BIN-decoded
  LOG as authoritative over an existing same-stem LOG. Shadowed native LOGs
  are excluded from normalization and source-state planning without being
  deleted or modified.
- Preserve the authoritative raw input basename in LOG-family `source_file`:
  native `.LOG` inputs retain their basename and BIN-derived records name the
  originating `.BIN` instead of a temporary decoded LOG.
- Preserve signed negative LOG epoch timestamps as source timestamps, including in grouped Iridium session timing.

## 2.0.0

Breaking output contract changes:

- Added `instrument_serial` to every normalized LOG and MER JSONL record.
- Renamed normalized JSONL family files to `<family>.<instrument_serial>.jsonl`.
- Added `instrument_serial` to relevant manifests and state/diagnostic rows that already carry instrument context.
- Changed stateful run directory IDs to UTC ISO8601-style timestamps such as `2026-04-21T22:17:31Z-11a3ef`.
- Removed `log_operational_records`; LOG families are now mutually exclusive by source-line assignment.
- Added LOG source-line provenance fields for exact assignment accounting.
- Fixed LOG assignment accounting to distinguish same-named source files by full source path internally.
- Changed the CLI LOG assignment report to accumulate writer summaries instead of rereading generated JSONL outputs.
- Kept LOG assignment summary count keys aligned with generated serial-suffixed output filenames.
- Added a developer reference for LOG record family schemas and classifier hit rules.
- Added tests that audit documented LOG family hit examples against the normalizer.
- Renamed `log_sbe_records` to `log_ctd_records` and added parsed CTD sample fields.
