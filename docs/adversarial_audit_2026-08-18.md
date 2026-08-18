# Adversarial Read-Only Audit — 2026-08-18

## Scope and status

This is a deep, read-only audit of `mermaid-records` as inspected on 2026-08-14.
It focuses on preservation, provenance, correctness, malformed historical data,
determinism, and test gaps—not general Python style. No implementation files,
fixtures, or tests were modified during the audit.

Verification performed:

```text
.venv/bin/python -m pytest -q
174 passed, 1 skipped
```

Priority uses the following meaning:

- **P1**: can silently corrupt or misroute scientific/operational information.
- **P2**: material preservation, provenance, reproducibility, or interface gap.
- **P3**: compatibility or consistency issue with lower immediate data risk.

The checkboxes are intentionally unchecked. They are a review and remediation
tracker, not evidence that a finding has been accepted or fixed.

The three P1 findings were remediated after this read-only audit. Concrete
before/after examples are in [P1 preservation hardening examples](p1_hardening_examples.md).

## Priority checklist

- [ ] **P1 — Preserve arbitrary MER DATA bytes.** Literal `</DATA>` and
  `</EVENT>` byte sequences inside a waveform payload must not silently end an
  event or truncate its payload.
- [ ] **P1 — Prevent incomplete Iridium sessions from absorbing unrelated LOG
  lines.** Define and test a deterministic boundary policy, including files
  ending before disconnect and sessions spanning file boundaries.
- [ ] **P1 — Preserve or explicitly diagnose repeated MER sections.** Duplicate
  or reordered complete `<ENVIRONMENT>` / `<PARAMETERS>` sections cannot be
  silently ignored.
- [ ] **P2 — Require standards-compliant JSON numeric values.** Reject or
  preserve non-finite source values without emitting `NaN` or `Infinity` JSON
  tokens.
- [ ] **P2 — Make malformed-source loss explicit in stateless mode.** A
  recoverable malformed line/block must be represented durably, or stateless
  mode must fail rather than silently omit it.
- [ ] **P2 — Restrict snapshot inventories to package-owned canonical output
  files.** Unrelated user `.jsonl` files must not alter corpus identity.
- [ ] **P2 — Strengthen BIN decoder provenance/invalidation.** A stateful no-op
  must not retain output produced by materially different decoder behavior.
- [ ] **P2 — Make normalized source provenance unambiguous.** A basename-only
  `source_file` cannot uniquely identify duplicate basenames in different raw
  directories or link a row to a particular source hash.
- [ ] **P2 — Decide whether DET/REQ needs an explicit structural field.** The
  current output preserves relevant INFO fields but does not normalize a DET or
  REQ discriminator.
- [ ] **P3 — Align the public LOG parser with pipeline LOG syntax.** Wrapped
  severity-tagged lines are accepted by normalization but not by
  `iter_operational_log_entries`.

## Data flow and information boundaries

```text
raw BIN -- manufacturer decoder --> temporary decoded LOG --+
                                                          |
native LOG -- LOG source-unit parser --------------------+--> 10 LOG JSONL families
                                                          |
raw MER -- section/event parser --> metadata + blocks ------> 3 MER JSONL families

all generated JSONL -- sorted relative paths, sizes, SHA-256 --> root snapshot manifest
```

### Accepted raw families

| Raw family | Parsing / intermediate form | Normalized products | Important transformations |
| --- | --- | --- | --- |
| `BIN` | External `preprocess.py` decoder emits temporary LOG artifacts. | LOG-derived JSONL rows. | Same-stem BIN wins over native LOG; row `source_file` is mapped back to BIN basename. Raw BIN is copied into a temporary workspace before decode. |
| `LOG` | Ordinary tagged entries, grouped episodes, rollover entries, malformed-line callbacks. | Acquisition, ascent request, GPS, pressure/temperature, battery, parameter, testmode, CTD, Iridium, unclassified. | Trailing newline removed; tagged timestamps normalized to UTC; source literal timestamp retained; selected values parsed; grouped raw lines retained. |
| `MER` | `MerFileMetadata` plus `MerEventBlock` objects. | Environment, parameter, event. | Metadata/tag text is decoded from ASCII; event payload is base64 encoded; selected timestamps normalized to UTC; raw INFO/FORMAT lines retained. |

### Authority, ordering, and snapshot behavior

- BIN-over-LOG authority is applied before state calculation. A native LOG with
  the same case-insensitive stem as a BIN is excluded from normalization,
  manifests, and incremental planning.
- Input paths are sorted before writer processing. JSONL rows are written in
  that source order and source-line order; grouped family ordering is episode
  order within each input file.
- The root snapshot hashes normalized JSONL relative paths, byte sizes, and
  SHA-256 values in sorted path order. It excludes `manifests` and `state`.
  Generation time, input root, command spelling, and filesystem timestamps do
  not enter `snapshot_id`.
- State manifests retain absolute raw source paths and source hashes, but those
  files do not contribute to the corpus snapshot. Stateless mode has no such
  per-source state.

## Detailed findings

### [ ] P1 — Literal closing-tag bytes can silently truncate MER payloads

**Location:** `src/mermaid_records/parse_mer.py`, `_EVENT_RE` and
`_extract_payload` (notably the first `find(b"</DATA>")`).

`DATA` is arbitrary binary, but the parser finds the first delimiter-looking
byte sequence rather than using a payload length or a structural framing rule.
The enclosing event regex has the analogous first-`</EVENT>` problem. A valid
payload containing these bytes can therefore be shortened or structurally split
without an error.

Observed reproduction: a DATA body containing `abc</DATA>def` produced payload
`b"abc"`, length 3, with no malformed-block diagnostic. A FORMAT-derived length
mismatch is only a warning-like output field; it does not reject the event, and
PSD events may legitimately lack FORMAT entirely.

**Information impact:** payload bytes after the accidental delimiter are absent
from `encoded_payload`; the normalized record alone cannot reconstruct the raw
event.

**Minimum regression fixture:** one complete event with a declared payload
length and delimiter-looking bytes inside the payload, plus a FORMAT-less PSD
event with the same property. Assert byte-perfect preservation or an explicit,
durable rejection.

### [ ] P1 — Unterminated explicit Iridium sessions swallow unrelated records

**Location:** `src/mermaid_records/normalize_log.py`, `_iter_log_source_units`.

Once an `Iridium...` start line opens an `explicit_session`, every later tagged
line is appended until a disconnect or `no connection` line. The parser does
not use subsystem, timing, rollover, or file boundary as a limit. Each LOG file
is independently flushed at EOF, so a logical session cannot continue into the
next file either.

Observed reproduction: `Iridium...` followed by a valid GPS position and EOF
produced one Iridium row containing both raw lines and no GPS row.

**Information impact:** a valid event is assigned to the wrong family. Although
the raw line remains nested in the Iridium row, downstream consumers looking at
GPS records lose it, and session timing is false.

**Minimum regression fixtures:** clean session; missing start; missing end;
explicit start followed by GPS/acquisition/CTD; a session across two LOG files;
two starts; repeated attempts; commands before and after uploads; failed upload;
and out-of-order source timestamps. Define whether an interrupted session is
closed as incomplete or can span a file boundary.

### [ ] P1 — Repeated or reordered complete MER metadata sections are ignored

**Location:** `src/mermaid_records/parse_mer.py`, `_extract_section`.

The helper returns on its first matching complete section. Later complete
`ENVIRONMENT` or `PARAMETERS` sections are not emitted, compared, or reported,
including in recoverable mode. Individual repeated tags within the first
section are generally retained as individual normalized rows, but repeated
sections are not.

**Information impact:** later metadata is absent from both normalized records
and malformed diagnostics. This is particularly risky if a partially written
file is resumed by appending a second section.

**Minimum regression fixture:** two complete sections with distinct BOARD,
SOFTWARE, GPSINFO, and parameter values, including a reordered layout. Assert
the agreed preservation or explicit conflict/error policy.

### [ ] P2 — Non-finite MER numeric values produce invalid JSON

**Location:** `src/mermaid_records/normalize_mer.py`, `_attr_float` and
`_write_jsonl_line`; generic `json.dumps` is also used in LOG and manifest
writes.

Python accepts `float("NaN")` and `float("Infinity")`; default `json.dumps`
then writes `NaN`/`Infinity`, which are not valid JSON according to the JSON
standard. Reproduction: `<TRUE_SAMPLE_FREQ FS_Hz=NaN />` emitted
`"true_sample_freq_hz": NaN`.

**Information impact:** parsers that correctly require JSON reject the corpus;
different dependency versions may handle these tokens differently. Negative or
otherwise impossible FORMAT values are also admitted into expected payload-byte
calculations without validation.

**Minimum regression fixture:** NaN, positive/negative infinity, very large
numeric text, and negative `LENGTH` / `BYTES_PER_SAMPLE`. Assert valid JSON and
an explicit raw-value/error policy.

### [ ] P2 — Recoverable malformed source content disappears in stateless runs

**Location:** `src/mermaid_records/normalize_log.py`,
`src/mermaid_records/parse_mer.py`, and `docs/limitations.md`.

Malformed LOG lines are callback-reported and then skipped. Recoverable MER
blocks/metadata lines can likewise be skipped. Stateful mode places callback
records under `manifests/runs/...`, which snapshot inventory intentionally
excludes. Stateless mode writes no diagnostic artifact at all.

**Information impact:** normalized records alone cannot show that a source line
or block existed and was not normalized. Normalized records plus the stateless
snapshot cannot show it either. Invalid UTF-8 LOG text is additionally decoded
with replacement, while MER metadata/tag text is decoded with ASCII `ignore`.

**Minimum regression fixture:** malformed direct LOG and MER in stateless mode,
including invalid UTF-8. Assert either a durable diagnostic output tied to the
raw source hash or a fail-closed result.

### [ ] P2 — The corpus snapshot includes unrelated JSONL files

**Location:** `src/mermaid_records/manifest.py`, `_normalized_file_inventory`.

The inventory recursively includes every `.jsonl` outside path components named
`manifests` or `state`. This includes an unknown user JSONL in an instrument
directory, even though package cleanup deliberately preserves unknown files.

**Information impact:** two otherwise identical normalized corpora can have
different snapshot identifiers because one output tree contains a user file.
The manifest then describes more than package-produced normalized records.

**Minimum regression fixture:** create a canonical output, add an unrelated
JSONL, rewrite the manifest, and assert the chosen policy. A package-owned
filename allow-list is the most direct definition of the intended corpus.

### [ ] P2 — Decoder state does not fully capture decoder behavior

**Location:** `src/mermaid_records/manifest.py`, `_decoder_state`.

State records the decoder executable path/version, decoder script hash, selected
database JSON hashes, preflight mode, and optional decoder Git commit. It does
not capture installed decoder dependencies, non-JSON database assets, locale,
or other external inputs the manufacturer script may use.

**Information impact:** stateful incremental planning can choose `noop` after a
material decoder-environment change, retaining an old decoded representation
when a clean run elsewhere would produce different rows. The output snapshot
faithfully identifies the old bytes but cannot establish decoder equivalence.

**Minimum regression fixture:** change a decoder dependency or declared external
input while leaving the script and selected database files unchanged; assert
re-decode or an explicit reproducibility limitation.

### [ ] P2 — Row-level source provenance is ambiguous for duplicate basenames

**Location:** `src/mermaid_records/normalize_log.py`,
`src/mermaid_records/normalize_mer.py`, and `src/mermaid_records/manifest.py`.

Rows expose only the authoritative source basename. Stateful source state has
absolute paths and hashes, but a row does not carry a source hash, source
relative path, or stable source identifier. Two source files with the same name
in different directories can therefore produce indistinguishable row provenance
apart from content and line number.

**Information impact:** normalized records plus snapshot metadata cannot
unambiguously link a row to one particular raw file/checksum.

**Minimum regression fixture:** normalize two same-basename LOG or MER files in
distinct allowed source directories for one instrument; assert an explicit
collision policy and record-to-source linkage.

### [ ] P2 — DET and REQ are preserved but not structurally distinguished

**Location:** `src/mermaid_records/normalize_mer.py`, `_build_event_record`.

Event records preserve `FNAME`, `ROUNDS`, trigger fields, raw INFO, raw FORMAT,
and payload. They do not emit an `event_kind` or equivalent DET/REQ
discriminator. Thus no current classifier can confuse DET and REQ because no
such classification exists; however, consumers must infer the distinction from
`FNAME` or raw INFO and confront malformed/missing-field ambiguity themselves.

This may be intentional under the repository's “normalize structure, not
meaning” boundary. It should nevertheless be an explicit schema decision,
because the audit requirements identify DET (`FNAME` historically null) and REQ
(`FNAME` historically non-null) as distinct semantics.

**Minimum regression fixture:** DET-like, REQ-like, missing-FNAME, empty-FNAME,
and malformed-FNAME events. Assert the documented structural representation and
that no implicit semantic classification is introduced accidentally.

### [ ] P3 — Public LOG parser does not accept wrapped severity syntax

**Location:** `src/mermaid_records/parse_log.py` versus
`src/mermaid_records/normalize_log.py`, `_parse_tagged_log_line`.

The pipeline accepts `timestamp:<ERR>[TAG]message`, `<WARN>`, and `<WRN>`
wrapped tagged lines, preserving the prefix in `message`. The public
`iter_operational_log_entries` parser only accepts the ordinary
`timestamp:[TAG]message` form and treats wrapped lines as malformed.

**Information impact:** callers using the parser module directly can lose a LOG
syntax that the normalizer recognizes. This is an interface consistency issue;
pipeline output itself preserves the prefix text.

**Minimum regression fixture:** run the same wrapped ERR/WARN/WRN lines through
both interfaces and assert identical parsed fields or document their intentionally
different contracts.

## Additional audit observations

### MER parser branches and loss points

- Empty MER and metadata-only PSD MER are accepted and result in zero event
  records; existing real PSD fixtures cover this.
- INFO+DATA without FORMAT is accepted, as required for PSD data.
- Unknown INFO/FORMAT keys cause a normalization failure after being detected;
  unknown ENVIRONMENT/PARAMETERS tags are represented as `unknown` records.
- Attribute regexes collapse duplicate keys to one dictionary value. Quoted or
  whitespace-containing values are not parsed as a general XML grammar.
- Non-ASCII bytes in MER metadata, INFO, FORMAT, and malformed-event diagnostic
  text are decoded with `ascii`, `ignore`, dropping bytes rather than retaining
  an escaped/raw representation.
- The low-level non-recoverable event parser can emit an event with missing INFO
  or DATA as nullable fields, whereas the stateful pipeline uses the recoverable
  parser and skips such a block with a diagnostic. This is a public-interface
  semantic mismatch.

### LOG parser and routing branches

- Normal tagged lines and wrapped ERR/WARN/WRN tagged lines are accepted by the
  normalizer. Blank lines close most grouped episodes; testmode retains blanks.
- Parameter, testmode, CTD, and Iridium grouping occur before ordinary
  single-line classification. Ordinary lines match at most one of acquisition,
  ascent request, GPS, pressure/temperature, and battery; zero matches route to
  unclassified and multiple matches fail loudly.
- `<ERR>/<WARN>/<WRN>` text is preserved in `message`. Single-line unclassified
  rows also contain normalized `severity`; grouped family events retain the
  literal text but not a separate severity field.
- Malformed lines are outside the source-line assignment invariant, so the
  invariant proves exclusivity only for successfully parsed/grouped units.
- Naive ISO timestamps are explicitly assigned UTC. Offset timestamps are
  converted to UTC and formatted with six fractional digits. Very large epoch
  timestamps can raise platform-dependent datetime errors; grouped parsing only
  catches `ValueError` at one boundary.

### Schema representability

The repository emits 13 canonical families: ten LOG and three MER. All rows are
generic dictionaries serialized without a runtime JSON Schema. This permits
scientifically impossible or internally inconsistent source-shaped states, such
as non-finite numeric metadata and negative expected payload byte counts.

MER event rows preserve structured metadata, raw INFO/FORMAT lines, and base64
payload; they intentionally do not preserve a byte-for-byte original EVENT
block. LOG grouped records preserve the episode's raw lines. Single-line LOG
records preserve `raw_line`. Newline spelling is intentionally normalized.

### Snapshot and clean-machine result

For a clean canonical output tree produced with equivalent decoder behavior, the
snapshot recipe is deterministic and existing tests cover relocation, creation
order, and filesystem timestamp changes. Absolute input paths and generation
metadata do not enter the digest.

The stronger statement—identical raw bytes necessarily yield identical corpus
bytes on all clean machines—is not established because BIN decoding is external
and its full execution environment is not captured. Unknown JSONL files in the
output tree are a separate snapshot-boundary problem.

## Existing coverage and missing tests

Existing tests substantively cover:

- same-stem BIN-over-LOG precedence in stateful and stateless modes;
- authoritative BIN basename provenance for decoded LOG rows;
- basic malformed-line/block recovery in stateful runs;
- empty and PSD-only MER files plus PSD INFO+DATA without FORMAT;
- LOG-family mutual exclusion, documented classifier hits, severity-wrapped
  normalizer inputs, acquisition/ascent/GPS/pressure/battery routing;
- basic Iridium session shapes and negative epoch times; and
- snapshot relocation, ordering, timestamp independence, and checksum changes.

The important missing tests are the minimum fixtures listed under each finding,
especially binary delimiter collisions, repeated sections, session interruption
and cross-file behavior, invalid/non-finite numbers, duplicate attributes,
malformed stateless provenance, user JSONL snapshot contamination, and
decoder-environment invalidation.

## Findings not raised as defects

- BIN-over-same-stem-LOG authority is clearly stated, implemented before
  incremental planning, and tested. The invariant is correct for the documented
  same-stem case.
- Empty MER, metadata-only PSD MER, and valid INFO+DATA events without FORMAT
  are accepted by the pipeline and test fixtures.
- The snapshot digest's sorted relative-path/hash algorithm does not depend on
  modification time, traversal order, absolute paths, generation time, or the
  manifest's own bytes.
- No specific Python 3.12/3.13/3.14 behavioral failure was found beyond the
  concrete datetime overflow and permissive JSON-number concerns above.
