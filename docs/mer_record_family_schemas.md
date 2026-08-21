# MER Record Family Schemas

This is a developer-facing reference for MER-derived JSONL families emitted by
`mermaid-records`. Filename examples below use base family filenames such as
`mer_event_records.jsonl`; pipeline outputs normally include the instrument
serial before `.jsonl`, for example
`mer_event_records.467.174-T-0100.jsonl`.

The source of truth is `src/mermaid_records/normalize_mer.py`. Update this
document whenever MER family filenames, emitted fields, tag routing, event
block handling, or payload preservation behavior changes.

MER normalization preserves source structure rather than interpreting
scientific or operational meaning. In particular, it does not associate event
blocks with dives, interpret waveform samples, derive durations, convert
coordinates, or infer relationships between metadata and events.

Every record has `source_file`, the basename of its original `.MER` input, and
content-addressed source provenance (`source_sha256` and
`source_id = "sha256:<digest>"`). No temporary files or local paths are
exposed in normalized records.

## Shared MER Fields

These fields appear in all three MER-derived families.

| Field | Type | Nullable? | Meaning | Units | Source / derivation |
| --- | --- | --- | --- | --- | --- |
| `instrument_id` | string | no | Canonical station/instrument identifier. | n/a | Pipeline context or fallback from MER path. |
| `instrument_serial` | string | no | Full hardware/dataset serial used in output filenames. | n/a | Pipeline context or fallback from MER path. |
| `mermaid_records_version` | string | no | Package version that emitted the normalized row. | n/a | Canonical `mermaid_records.__version__`. |
| `source_file` | string | no | Basename of the original `.MER` source artifact. | n/a | Input basename; never a full path. |
| `source_id` | string | no | Content-addressed identifier for the source file. | n/a | `sha256:<source_sha256>`. |
| `source_sha256` | string | no | SHA-256 checksum of original `.MER` bytes. | n/a | Raw source file. |
| `source_container` | string | no | Source container kind. Always `mer`. | n/a | Constant. |

## MER Structure and Routing Contract

The parser reads complete `<ENVIRONMENT>` and `<PARAMETERS>` sections as
file-level metadata, and zero or more `<EVENT>` blocks. A valid MER file may
contain metadata with no event blocks, including Stanford PSD MER files.

Every preserved environment tag produces one
`mer_environment_records.jsonl` row. Every preserved parameter tag produces
one `mer_parameter_records.jsonl` row. Every parsed `<EVENT>` block produces
one `mer_event_records.jsonl` row. Event blocks may omit `<FORMAT>` when they
contain valid `<INFO>` and `<DATA>` elements.

Environment and parameter tag classification is stage-specific. Known tags
have the following `*_kind` values; any unregistered tag is retained with
`environment_kind` or `parameter_kind` equal to `unknown`. A tag registered in
both stage maps is a normalization error (`MER derived-family multi-match`).

| Stage | Source tag | Kind |
| --- | --- | --- |
| environment | `BOARD` | `board` |
| environment | `SOFTWARE` | `software` |
| environment | `DIVE` | `dive` |
| environment | `POOL` | `pool` |
| environment | `GPSINFO` | `gpsinfo` |
| environment | `DRIFT` | `drift` |
| environment | `CLOCK` | `clock` |
| environment | `SAMPLE` | `sample` |
| environment | `TRUE_SAMPLE_FREQ` | `true_sample_freq` |
| parameter | `ADC` | `adc` |
| parameter | `INPUT_FILTER` | `input_filter` |
| parameter | `STALTA` | `stalta` |
| parameter | `EVENT_LEN` | `event_len` |
| parameter | `RATING` | `rating` |
| parameter | `CDF24` | `cdf24` |
| parameter | `MODEL` | `model` |
| parameter | `ASCEND_THRESH` | `ascend_thresh` |
| parameter | `STANFORD_PROCESS` | `stanford_process` |
| parameter | `MISC` | `misc` |

Malformed structures can be either fail-closed or, in recoverable runs,
reported in run-scoped malformed-block bookkeeping. Complete repeated metadata
sections are preserved as records and reported. Unrecognized `<INFO>` or
`<FORMAT>` attribute names cause normalization to fail after the source file is
processed; this prevents silent loss of event metadata.

## `mer_environment_records.jsonl`

Purpose / scope: one row for each raw tag line in an `<ENVIRONMENT>` section.
The original line and literal attribute values are retained; selected known
fields are conservatively extracted without coordinate or clock conversion.

Representative object:

```json
{"instrument_id":"T0100","instrument_serial":"467.174-T-0100","mermaid_records_version":"<package-version>","source_file":"0100_sample.MER","source_container":"mer","environment_kind":"gpsinfo","board":null,"software":null,"dive_id":null,"dive_declared_event_count":null,"pool_declared_event_count":null,"pool_declared_size_bytes":null,"sample_min":null,"sample_max":null,"true_sample_freq_hz":null,"gpsinfo_date":"2024-02-07T22:47:22.000000Z","raw_values":{"date":"2024-02-07T22:47:22","lat":"+2845.7300","lon":"+13848.3010"},"line":"\t<GPSINFO DATE=2024-02-07T22:47:22 LAT=+2845.7300 LON=+13848.3010 />"}
```

Field table: shared MER fields plus:

| Field | Type | Nullable? | Meaning | Units | Source / derivation |
| --- | --- | --- | --- | --- | --- |
| `environment_kind` | string | no | Registered environment tag kind, or `unknown`. | n/a | Stage-specific tag map. |
| `board` | string | yes | Board identifier. | source literal | Bare value of `BOARD`; otherwise `null`. |
| `software` | string | yes | Software identifier/version. | source literal | Bare value of `SOFTWARE`; otherwise `null`. |
| `dive_id` | integer | yes | Explicit source dive ID. | n/a | `DIVE ID`; otherwise `null`. |
| `dive_declared_event_count` | integer | yes | Event count declared by a `DIVE` tag. | events | `DIVE EVENTS`; otherwise `null`. |
| `pool_declared_event_count` | integer | yes | Event count declared by a `POOL` tag. | events | `POOL EVENTS`; otherwise `null`. |
| `pool_declared_size_bytes` | integer | yes | Size declared by a `POOL` tag. | bytes | `POOL SIZE`; otherwise `null`. |
| `sample_min` | integer | yes | Source sample minimum. | source sample units | `SAMPLE MIN`; otherwise `null`. |
| `sample_max` | integer | yes | Source sample maximum. | source sample units | `SAMPLE MAX`; otherwise `null`. |
| `true_sample_freq_hz` | number | yes | Explicit source frequency. | Hz | `TRUE_SAMPLE_FREQ FS_Hz`; otherwise `null`. |
| `gpsinfo_date` | string | yes | UTC ISO-8601 timestamp from `GPSINFO DATE`. | UTC time | Parsed source timestamp; otherwise `null`. |
| `raw_values` | object or null | yes | Lowercase literal attributes, or the bare tag value keyed by tag name. | source literal | Parsed tag attributes; values are strings. |
| `line` | string | no | Original metadata line, including any leading whitespace. | n/a | Raw source line. |

Known gaps / edge cases: `GPSINFO` latitude and longitude remain literal
strings in `raw_values`; no coordinate conversion occurs. `DRIFT` and `CLOCK`
attributes are similarly preserved only in `raw_values`. Repeated `GPSINFO`,
`DRIFT`, and `CLOCK` lines each emit a separate record.

## `mer_parameter_records.jsonl`

Purpose / scope: one row for each raw tag line in a `<PARAMETERS>` section.
All attributes remain available as literal strings in `raw_values`; only the
fields listed below receive conservative typed extraction.

Representative object:

```json
{"instrument_id":"T0100","instrument_serial":"467.174-T-0100","mermaid_records_version":"<package-version>","source_file":"0100_sample.MER","source_container":"mer","parameter_kind":"stanford_process","adc_gain":null,"adc_buffer":null,"stanford_process_duration_h":168,"stanford_process_period_h":3,"stanford_process_window_len":1024,"stanford_process_window_type":"Hanning","stanford_process_overlap_percent":10,"stanford_process_db_offset":0.0,"upload_max":null,"raw_values":{"duration_h":"168","process_period_h":"3","window_len":"1024","window_type":"Hanning","overlap_percent":"10","dB_OFFSET":"0"},"line":"\t<STANFORD_PROCESS DURATION_h=168 PROCESS_PERIOD_h=3 WINDOW_LEN=1024 WINDOW_TYPE=Hanning OVERLAP_PERCENT=10 dB_OFFSET=0 />"}
```

Field table: shared MER fields plus:

| Field | Type | Nullable? | Meaning | Units | Source / derivation |
| --- | --- | --- | --- | --- | --- |
| `parameter_kind` | string | no | Registered parameter tag kind, or `unknown`. | n/a | Stage-specific tag map. |
| `adc_gain` | integer | yes | ADC gain. | source-defined | `ADC GAIN`; otherwise `null`. |
| `adc_buffer` | string | yes | ADC buffer setting. | source literal | `ADC BUFFER`; otherwise `null`. |
| `stanford_process_duration_h` | integer | yes | Stanford processing duration. | h | `STANFORD_PROCESS DURATION_h` or `DURATION_H`; otherwise `null`. |
| `stanford_process_period_h` | integer | yes | Stanford processing period. | h | `STANFORD_PROCESS PROCESS_PERIOD_h` or `PROCESS_PERIOD_H`; otherwise `null`. |
| `stanford_process_window_len` | integer | yes | Stanford processing window length. | source samples | `STANFORD_PROCESS WINDOW_LEN`; otherwise `null`. |
| `stanford_process_window_type` | string | yes | Stanford processing window type. | source literal | `STANFORD_PROCESS WINDOW_TYPE`; otherwise `null`. |
| `stanford_process_overlap_percent` | integer | yes | Stanford processing window overlap. | percent | `STANFORD_PROCESS OVERLAP_PERCENT`; otherwise `null`. |
| `stanford_process_db_offset` | number | yes | Stanford processing dB offset. | dB | `STANFORD_PROCESS dB_OFFSET` or `DB_OFFSET`; otherwise `null`. |
| `upload_max` | string | yes | Maximum upload setting. | source literal | `MISC UPLOAD_MAX`; otherwise `null`. |
| `raw_values` | object or null | yes | Lowercase attribute keys and literal values. | source literal | Parsed tag attributes. |
| `line` | string | no | Original parameter line, including any leading whitespace. | n/a | Raw source line. |

Known gaps / edge cases: fields for `INPUT_FILTER`, `STALTA`, `EVENT_LEN`,
`RATING`, `CDF24`, `MODEL`, and `ASCEND_THRESH` are intentionally retained in
`raw_values` only. No parameter values are used to interpret event payloads.

## `mer_event_records.jsonl`

Purpose / scope: one row per parsed `<EVENT>` block. The row preserves `<INFO>`
and `<FORMAT>` attributes, plus the exact bytes within `<DATA>`, without
waveform decoding or event-to-dive association.

Representative object:

```json
{"instrument_id":"T0100","instrument_serial":"467.174-T-0100","mermaid_records_version":"<package-version>","source_file":"0100_sample.MER","source_container":"mer","block_index":0,"event_index":0,"event_info_date":"2024-02-07T22:47:22.000000Z","event_rounds":null,"date":"2024-02-07T22:47:22.000000Z","rounds":null,"pressure":null,"temperature":null,"criterion":null,"snr":null,"trig":null,"detrig":null,"fname":"2024-02-07T22_47_22.000000","smp_offset":"614054","true_fs":"40.014107","endianness":"LITTLE","bytes_per_sample":"4","sampling_rate":"20.000000","stages":"5","normalized":"YES","length":"4832","encoded_payload":"QUJD","encoded_payload_byte_count":3,"data_payload_nbytes":3,"expected_payload_nbytes":19328,"payload_length_matches_expected":false,"raw_info_line":"<INFO DATE=2024-02-07T22:47:22 FNAME=2024-02-07T22_47_22.000000 SMP_OFFSET=614054 TRUE_FS=40.014107 />","raw_format_line":"<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 STAGES=5 NORMALIZED=YES LENGTH=4832 />"}
```

Field table: shared MER fields plus:

| Field | Type | Nullable? | Meaning | Units | Source / derivation |
| --- | --- | --- | --- | --- | --- |
| `block_index` | integer | no | Zero-based EVENT block number within its source file. | n/a | Parser enumeration. |
| `event_index` | integer | no | Alias of `block_index`. | n/a | Parser enumeration. |
| `event_info_date` | string | yes | UTC ISO-8601 timestamp from `INFO DATE`. | UTC time | Parsed source timestamp. |
| `event_rounds` | string | yes | Literal `INFO ROUNDS` value. | source literal | INFO attribute. |
| `date` | string | yes | Alias of `event_info_date`. | UTC time | `INFO DATE`. |
| `rounds` | string | yes | Alias of `event_rounds`. | source literal | `INFO ROUNDS`. |
| `pressure` | string | yes | Literal `INFO PRESSURE` value. | source-defined | INFO attribute. |
| `temperature` | string | yes | Literal `INFO TEMPERATURE` value. | source-defined | INFO attribute. |
| `criterion` | string | yes | Literal `INFO CRITERION` value. | source-defined | INFO attribute. |
| `snr` | string | yes | Literal `INFO SNR` value. | source-defined | INFO attribute. |
| `trig` | string | yes | Literal `INFO TRIG` value. | source-defined | INFO attribute. |
| `detrig` | string | yes | Literal `INFO DETRIG` value. | source-defined | INFO attribute. |
| `fname` | string | yes | Literal `INFO FNAME` value. | source literal | INFO attribute. |
| `smp_offset` | string | yes | Literal `INFO SMP_OFFSET` value. | source-defined | INFO attribute. |
| `true_fs` | string | yes | Literal `INFO TRUE_FS` value. | source-defined | INFO attribute. |
| `endianness` | string | yes | Literal `FORMAT ENDIANNESS` value. | source literal | FORMAT attribute. |
| `bytes_per_sample` | string | yes | Literal `FORMAT BYTES_PER_SAMPLE` value. | bytes/sample | FORMAT attribute. |
| `sampling_rate` | string | yes | Literal `FORMAT SAMPLING_RATE` value. | source-defined | FORMAT attribute. |
| `stages` | string | yes | Literal `FORMAT STAGES` value. | source-defined | FORMAT attribute. |
| `normalized` | string | yes | Literal `FORMAT NORMALIZED` value. | source literal | FORMAT attribute. |
| `length` | string | yes | Literal `FORMAT LENGTH` value. | source samples | FORMAT attribute. |
| `encoded_payload` | string | yes | Base64 representation of exact DATA payload bytes. | base64 | Bytes inside `<DATA>`. |
| `encoded_payload_byte_count` | integer | no | Number of DATA payload bytes. | bytes | Length of extracted payload. |
| `data_payload_nbytes` | integer | no | Alias of `encoded_payload_byte_count`. | bytes | Length of extracted payload. |
| `expected_payload_nbytes` | integer | yes | Product of `FORMAT LENGTH` and `BYTES_PER_SAMPLE`. | bytes | FORMAT attributes; `null` if either is absent. |
| `payload_length_matches_expected` | boolean | yes | Whether actual and expected payload lengths agree. | n/a | Payload length comparison; `null` when expected length is unknown. |
| `raw_info_line` | string | yes | Raw INFO tag line. | n/a | Source EVENT block. |
| `raw_format_line` | string | yes | Raw FORMAT tag line. | n/a | Source EVENT block; may be `null`. |
| `stanford_psd_processing` | object | yes, omitted when unavailable | Sole unambiguous file-level Stanford processing parameter set. | mixed | Added only for Stanford software with exactly one `STANFORD_PROCESS` line. |

`stanford_psd_processing`, when present, contains `duration_h`,
`process_period_h`, `window_len`, `window_type`, `overlap_percent`,
`db_offset`, and `raw_parameter_line`.

Known gaps / edge cases: `<DATA>` framing bytes (`\n\r` immediately after the
opening tag and `\n\r\t` immediately before the closing tag) are excluded;
only payload bytes are encoded. Payload bytes are not interpreted as samples.
Missing `<FORMAT>` is valid, resulting in null FORMAT fields and null expected
payload-length fields. A payload-size mismatch is reported in the row but does
not alter the payload.
