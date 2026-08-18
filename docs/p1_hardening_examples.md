# P1 Preservation Hardening Examples

These are small, synthetic examples based on the raw MER and LOG syntax used
by the normalizer. They document the three P1 issues identified in the
2026-08-18 adversarial audit and the resulting behavior. The fixture-backed
tests are the executable specification; the examples here are for inspection.

## 1. DATA delimiters inside a length-framed MER payload

The bytes inside `DATA` are waveform payload, not XML text. In a normal MER
event, `LENGTH * BYTES_PER_SAMPLE` gives the payload boundary. The following
example uses a one-byte sample width solely to keep the edge case readable:

```text
<EVENT>
  <INFO DATE=2024-02-07T22:47:22 />
  <FORMAT BYTES_PER_SAMPLE=1 LENGTH=24 />
  <DATA>abc</DATA>def</EVENT>ghi</DATA>
</EVENT>
```

The payload is exactly these 24 bytes:

```text
abc</DATA>def</EVENT>ghi
```

The inner closing-tag byte sequences are included in `encoded_payload`; they
do not end the event. The final `</DATA>` is found at byte offset 24 after the
DATA payload begins, using the declared FORMAT length.

A format-less PSD event has no such boundary. This input is therefore
quarantined rather than guessing which DATA delimiter is structural:

```text
<EVENT><INFO DATE=2024-02-07T22:47:22 />
<DATA>abc</DATA>def</DATA></EVENT>
```

In a stateful run, `manifests/runs/<run_id>/malformed_mer_blocks.jsonl`
contains an `event_data` record with the error
`ambiguous FORMAT-less DATA block: multiple </DATA> delimiters`. No partial
event record is emitted.

## 2. Interrupted Iridium session followed by GPS

An explicit Iridium session is no longer a catch-all for every later tagged
line. Only recognized Iridium event lines and literal `$COMMAND...;` lines
remain in the session. For example:

```text
1700000000:[SURF  ,0025]Iridium...
1700000001:[SURF  ,0311]connected in 37s, signal quality 5
1700000002:[SURF  ,0394]S23deg29.970mn, W132deg30.444mn
```

This produces two products:

```text
log_iridium_records: source_line_numbers = [1, 2]
log_gps_records:     source_line_number = 3
```

The Iridium record is an incomplete session, flushed before the GPS line.
The GPS position is not nested inside `iridium_events` and remains discoverable
through the GPS record family. A session also flushes at a blank/non-tagged
line or end of the source file; it never spans files.

## 3. Repeated complete MER metadata sections

MER files occasionally contain a repeated complete metadata section, such as
a file resumed after an interrupted write:

```text
<ENVIRONMENT><BOARD 452116600-A0 /></ENVIRONMENT>
<ENVIRONMENT><BOARD 452116600-A1 /></ENVIRONMENT>
<PARAMETERS><MISC UPLOAD_MAX=100kB /></PARAMETERS>
<PARAMETERS><MISC UPLOAD_MAX=200kB /></PARAMETERS>
```

All four source tags are preserved, in source order, rather than silently
retaining only the first section:

```text
mer_environment_records: <BOARD 452116600-A0 />
mer_environment_records: <BOARD 452116600-A1 />
mer_parameter_records:   <MISC UPLOAD_MAX=100kB />
mer_parameter_records:   <MISC UPLOAD_MAX=200kB />
```

Each repeated complete section is also quarantined for review in the stateful
run's `malformed_mer_blocks.jsonl` with
`block_kind = "repeated_metadata_section"`. The end-of-run summary includes
these in its `malformed mer blocks` count; the raw input remains unchanged.
