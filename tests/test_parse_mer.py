# SPDX-License-Identifier: MIT

from pathlib import Path

import pytest

from mermaid_records.parse_mer import parse_mer_file

PSD_MER_FIXTURE_ROOT = Path("data/fixtures/465.152-R-0001/mer")


def test_parse_mer_file_extracts_metadata_and_blocks() -> None:
    path = Path("data/fixtures/467.174-T-0100/mer/0100_685864F3.MER")
    metadata, blocks = parse_mer_file(path)

    assert metadata.board == "452116600-A0"
    assert metadata.software_version == "2.1344"
    assert metadata.dive_id == 8
    assert metadata.dive_event_count == 41
    assert metadata.pool_event_count == 128
    assert metadata.pool_size_bytes == 2411800
    assert metadata.gps_fixes[0]["lat"] == "+3133.6840"
    assert metadata.clock_frequencies_hz[0] == 3686330
    assert metadata.sample_min == -134217728
    assert metadata.sample_max == 134217712
    assert metadata.true_sample_freq_hz == 40.014219
    assert len(blocks) >= 1
    assert blocks[0].date is not None
    assert blocks[0].length_samples == 4448
    assert blocks[0].endianness == "LITTLE"
    assert blocks[0].data_payload is not None


def test_parse_mer_file_extracts_only_payload_bytes_inside_data_framing(tmp_path: Path) -> None:
    path = tmp_path / "0100_framed.MER"
    payload = b"A" * 19328
    path.write_bytes(
        (
            b"<ENVIRONMENT>\n"
            b"\t<BOARD 452116600-A0 />\n"
            b"</ENVIRONMENT>\n"
            b"<PARAMETERS>\n"
            b"\t<MISC UPLOAD_MAX=100kB />\n"
            b"</PARAMETERS>\n"
            b"<EVENT>\n"
            b"\t<INFO DATE=2024-02-07T22:47:22 FNAME=framed.000000 SMP_OFFSET=614054 TRUE_FS=40.014107 />\n"
            b"\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            b"STAGES=5 NORMALIZED=YES LENGTH=4832 />\n"
            b"\t<DATA>\n\r"
            + payload
            + b"\n\r\t</DATA>\n"
            b"</EVENT>\n"
        )
    )

    _metadata, blocks = parse_mer_file(path)

    assert len(blocks) == 1
    assert blocks[0].data_payload == payload
    assert len(blocks[0].data_payload) == 19328


def test_parse_mer_file_preserves_delimiter_bytes_inside_length_framed_payload(
    tmp_path: Path,
) -> None:
    path = tmp_path / "0100_delimiters.MER"
    payload = b"abc</DATA>def</EVENT>ghi"
    path.write_bytes(
        b"<EVENT><INFO DATE=2024-02-07T22:47:22 />"
        + f"<FORMAT BYTES_PER_SAMPLE=1 LENGTH={len(payload)} />".encode()
        + b"<DATA>"
        + payload
        + b"</DATA></EVENT>"
    )

    _metadata, blocks = parse_mer_file(path)

    assert [block.data_payload for block in blocks] == [payload]


def test_parse_mer_file_rejects_ambiguous_formatless_data_delimiters(tmp_path: Path) -> None:
    path = tmp_path / "0100_ambiguous.MER"
    path.write_bytes(
        b"<EVENT><INFO DATE=2024-02-07T22:47:22 />"
        b"<DATA>abc</DATA>def</DATA></EVENT>"
    )

    with pytest.raises(ValueError, match="ambiguous FORMAT-less"):
        parse_mer_file(path)


def test_parse_mer_file_preserves_repeated_complete_metadata_sections(tmp_path: Path) -> None:
    path = tmp_path / "0100_repeated_metadata.MER"
    path.write_bytes(
        b"<ENVIRONMENT><BOARD first /></ENVIRONMENT>"
        b"<ENVIRONMENT><BOARD second /></ENVIRONMENT>"
        b"<PARAMETERS><MISC UPLOAD_MAX=100kB /></PARAMETERS>"
        b"<PARAMETERS><MISC UPLOAD_MAX=200kB /></PARAMETERS>"
    )

    metadata, blocks = parse_mer_file(path)

    assert blocks == []
    assert metadata.raw_environment_lines == ["<BOARD first />", "<BOARD second />"]
    assert metadata.raw_parameter_lines == [
        "<MISC UPLOAD_MAX=100kB />",
        "<MISC UPLOAD_MAX=200kB />",
    ]


def test_parse_mer_file_accepts_stanford_event_without_format(tmp_path: Path) -> None:
    path = tmp_path / "0002_stanford.MER"
    payload = b"\x01\x02\x03\x04"
    path.write_bytes(
        (
            b"<ENVIRONMENT>\n"
            b"\t<BOARD 465152600-75 />\n"
            b"\t<SOFTWARE 2.1377-STANFORD />\n"
            b"</ENVIRONMENT>\n"
            b"<PARAMETERS>\n"
            b"\t<ADC GAIN=1 BUFFER=ON />\n"
            b"\t<STANFORD_PROCESS DURATION_h=168 PROCESS_PERIOD_h=3 WINDOW_LEN=1024 WINDOW_TYPE=Hanning OVERLAP_PERCENT=10 dB_OFFSET=0 />\n"
            b"\t<MISC UPLOAD_MAX=120kB />\n"
            b"</PARAMETERS>\n"
            b"<EVENT>\n"
            b"\t<INFO DATE=2021-10-16T04:31:58.638228 ROUNDS=468 />\n"
            b"\t<DATA>\n\r"
            + payload
            + b"\n\r\t</DATA>\n\r</EVENT>\n"
        )
    )

    metadata, blocks = parse_mer_file(path)

    assert metadata.software == "2.1377-STANFORD"
    assert metadata.adc_gain == 1
    assert metadata.adc_buffer == "ON"
    assert metadata.stanford_process_duration_h == 168
    assert metadata.stanford_process_period_h == 3
    assert metadata.stanford_process_window_len == 1024
    assert metadata.stanford_process_window_type == "Hanning"
    assert metadata.stanford_process_overlap_percent == 10
    assert metadata.stanford_process_db_offset == 0.0
    assert metadata.upload_max == "120kB"
    assert len(blocks) == 1
    assert blocks[0].raw_format_line is None
    assert blocks[0].data_payload == payload


def test_parse_mer_file_extracts_real_psd_metadata_only_fixture() -> None:
    path = PSD_MER_FIXTURE_ROOT / "0001_6255B101.MER"

    metadata, blocks = parse_mer_file(path)

    assert metadata.board == "465152600-74"
    assert metadata.software == "2.1377-STANFORD"
    assert metadata.dive_id == 27
    assert metadata.dive_event_count == 0
    assert metadata.pool_event_count == 0
    assert metadata.pool_size_bytes == 0
    assert metadata.gps_fixes == [
        {"date": "2022-04-12T17:03:34", "lat": "+4219.1150", "lon": "+00459.9350"}
    ]
    assert metadata.drifts == [{"sec": None, "usec": 0}]
    assert metadata.clock_frequencies_hz == [3686352]
    assert metadata.stanford_process_duration_h == 168
    assert metadata.stanford_process_period_h == 3
    assert metadata.stanford_process_window_len == 1024
    assert metadata.stanford_process_window_type == "Hanning"
    assert metadata.stanford_process_overlap_percent == 10
    assert metadata.stanford_process_db_offset == 0.0
    assert metadata.upload_max == "120kB"
    assert blocks == []


def test_parse_mer_file_extracts_real_psd_event_blocks_without_format() -> None:
    path = PSD_MER_FIXTURE_ROOT / "0001_625CB0C0.MER"

    metadata, blocks = parse_mer_file(path)

    assert metadata.board == "465152600-74"
    assert metadata.software == "2.1377-STANFORD"
    assert metadata.pool_event_count == 10
    assert metadata.pool_size_bytes == 6060
    assert [fix["date"] for fix in metadata.gps_fixes] == [
        "2022-04-17T19:29:57",
        "2022-04-17T19:40:37",
        "2022-04-17T19:44:10",
        "2022-04-18T00:28:22",
    ]
    assert metadata.drifts[-1] == {"sec": None, "usec": -671}
    assert metadata.clock_frequencies_hz == [3686352, 3686352, 3686352, 3686352]
    assert len(blocks) == 10
    assert all(block.raw_format_line is None for block in blocks)
    assert {len(block.data_payload or b"") for block in blocks} == {512}
    assert blocks[0].date is not None
    assert blocks[0].date.isoformat() == "2022-04-12T10:02:58.273497+00:00"
    assert blocks[0].raw_info_line == "<INFO DATE=2022-04-12T10:02:58.273497 ROUNDS=237 />"
    assert blocks[-1].date is not None
    assert blocks[-1].date.isoformat() == "2022-04-11T07:03:03.177795+00:00"
    assert blocks[-1].raw_info_line == "<INFO DATE=2022-04-11T07:03:03.177795 ROUNDS=468 />"


def test_parse_mer_file_rejects_incomplete_event_block(tmp_path: Path) -> None:
    path = tmp_path / "0100_incomplete.MER"
    path.write_bytes(
        (
            b"<ENVIRONMENT>\n"
            b"\t<BOARD 452116600-A0 />\n"
            b"</ENVIRONMENT>\n"
            b"<PARAMETERS>\n"
            b"\t<MISC UPLOAD_MAX=100kB />\n"
            b"</PARAMETERS>\n"
            b"<EVENT>\n"
            b"\t<INFO DATE=2024-02-07T22:47:22 FNAME=broken.000000 SMP_OFFSET=614054 TRUE_FS=40.014107 />\n"
            b"\t<FORMAT ENDIANNESS=LITTLE BYTES_PER_SAMPLE=4 SAMPLING_RATE=20.000000 "
            b"STAGES=5 NORMALIZED=YES LENGTH=4832 />\n"
            b"\t<DATA>\n\rABCDEF\n"
            b"</EVENT>\n"
        )
    )

    with pytest.raises(ValueError, match="missing </DATA>"):
        parse_mer_file(path)
