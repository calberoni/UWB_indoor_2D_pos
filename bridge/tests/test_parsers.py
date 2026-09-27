import struct

import pytest

from uwb_bridge.parsers import (
    BLE_PAYLOAD_SIZE,
    ParseError,
    is_comment,
    parse_ble_payload,
    parse_log_row,
    parse_serial_line,
    parse_sync_reply,
)
from uwb_bridge.sample import Sample

T_HOST = 1_790_000_000_123
EXPECTED = Sample(t_host_ms=T_HOST, seq=1234, d_a=2940, d_b=3120, q_a=210, q_b=198, t_ms=123456)


# --- Serie ------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "1234,2940,3120,210,198,123456",
        "1234,2940,3120,210,198,123456\n",
        "1234,2940,3120,210,198,123456\r\n",
        b"1234,2940,3120,210,198,123456\r\n",
        bytearray(b"1234,2940,3120,210,198,123456\n"),
        "  1234,2940,3120,210,198,123456  ",
    ],
)
def test_serial_line_from_the_contract(line):
    assert parse_serial_line(line, T_HOST) == EXPECTED


def test_serial_failed_rangings():
    sample = parse_serial_line("7,-1,-1,0,0,500", T_HOST)
    assert (sample.d_a, sample.d_b, sample.q_a, sample.q_b) == (-1, -1, 0, 0)


def test_serial_field_limits():
    sample = parse_serial_line("65535,2147483647,0,255,0,4294967295", T_HOST)
    assert sample.seq == 65535
    assert sample.d_a == 2**31 - 1
    assert sample.q_a == 255
    assert sample.t_ms == 2**32 - 1


@pytest.mark.parametrize(
    "line",
    [
        "# arranque: DEV_ID 0xDECA0302",
        "#",
        "# SYNC 123456",
        "# ANT 16400",
        "# RAW 1234 5678 9012",
        "#1234,2940,3120,210,198,123456",
        "  # con espacios delante",
        b"# bytes\r\n",
        "",
        "\n",
        "\r\n",
    ],
)
def test_comment_and_blank_lines_are_not_records(line):
    assert parse_serial_line(line, T_HOST) is None


def test_is_comment():
    assert is_comment("# RATE 10")
    assert is_comment(b"# RATE 10\r\n")
    assert not is_comment("1234,2940,3120,210,198,123456")


@pytest.mark.parametrize(
    "line",
    [
        "1234,2940,3120,210,198",  # falta un campo
        "1234,2940,3120,210,198,123456,7",  # sobra un campo
        "2940,3120,210,198,123456",  # principio cortado
        "1234,2940,3120,210,198,",  # final cortado
        "1234,2940,,210,198,123456",
        "1234,29x0,3120,210,198,123456",
        "1234,2940.5,3120,210,198,123456",
        "1234;2940;3120;210;198;123456",
        "1234 2940 3120 210 198 123456",
        "12 34,2940,3120,210,198,123456",
        "1_234,2940,3120,210,198,123456",
        "+1234,2940,3120,210,198,123456",
        "-1,2940,3120,210,198,123456",  # seq negativo
        "65536,2940,3120,210,198,123456",  # seq no cabe en uint16
        "1234,-2,3120,210,198,123456",  # solo −1 significa fallo
        "1234,2940,2147483648,210,198,123456",  # no cabe en int32
        "1234,2940,3120,256,198,123456",  # calidad no cabe en uint8
        "1234,2940,3120,210,-1,123456",
        "1234,2940,3120,210,198,4294967296",  # t_ms no cabe en uint32
        "1234,2940,3120,210,198,123456789012345678901234567890",
        "1234,2940,3120,210,198,123456 1235,2941,3121,210,198,123556",  # dos líneas pegadas
        "hola",
        "SYNC",
        "١٢٣٤,2940,3120,210,198,123456",  # dígitos no ASCII
        b"\xff\xfe\x00\x01garbage\x80",
        b"1234,2940,31\x0020,210,198,123456",
    ],
)
def test_corrupt_serial_lines_raise_parse_error(line):
    with pytest.raises(ParseError):
        parse_serial_line(line, T_HOST)


def test_corrupt_lines_never_raise_anything_else():
    # Barrido de bytes arbitrarios: el parser solo puede devolver o lanzar ParseError.
    for value in range(256):
        for line in (bytes([value]) * 3, b"1234,2940," + bytes([value]) + b",210,198,1"):
            try:
                parse_serial_line(line, T_HOST)
            except ParseError:
                pass


@pytest.mark.parametrize(
    "line, expected",
    [
        ("# SYNC 123456", 123456),
        ("# SYNC 0", 0),
        ("#SYNC 42", 42),
        (b"# SYNC 4294967295\r\n", 4294967295),
        ("# SYNC", None),
        ("# SYNC abc", None),
        ("# SYNC 12 34", None),
        ("# SYNC -5", None),
        ("# SYNC 4294967296", None),
        ("# ANT 16385", None),
        ("SYNC 123", None),
        ("1234,2940,3120,210,198,123456", None),
    ],
)
def test_sync_reply(line, expected):
    assert parse_sync_reply(line) == expected


# --- BLE --------------------------------------------------------------------


def test_ble_payload_is_16_bytes():
    assert BLE_PAYLOAD_SIZE == 16


def test_ble_payload_from_the_contract():
    payload = struct.pack("<HiiBBI", 1234, 2940, 3120, 210, 198, 123456)
    assert len(payload) == 16
    assert parse_ble_payload(payload, T_HOST) == EXPECTED


def test_ble_payload_byte_layout():
    # Little-endian, sin relleno: seq(2) d_a(4) d_b(4) q_a(1) q_b(1) t_ms(4).
    payload = bytes.fromhex("d204" "7c0b0000" "300c0000" "d2" "c6" "40e20100")
    assert parse_ble_payload(payload, T_HOST) == EXPECTED


def test_ble_payload_accepts_bytearray():
    payload = bytearray(struct.pack("<HiiBBI", 1234, 2940, 3120, 210, 198, 123456))
    assert parse_ble_payload(payload, T_HOST) == EXPECTED


def test_ble_failed_rangings_and_limits():
    payload = struct.pack("<HiiBBI", 65535, -1, -1, 0, 255, 2**32 - 1)
    sample = parse_ble_payload(payload, T_HOST)
    assert (sample.seq, sample.d_a, sample.d_b, sample.q_b, sample.t_ms) == (65535, -1, -1, 255, 2**32 - 1)


@pytest.mark.parametrize("size", [0, 1, 15, 17, 20, 32])
def test_ble_payload_of_wrong_size(size):
    with pytest.raises(ParseError):
        parse_ble_payload(bytes(size), T_HOST)


def test_ble_payload_with_impossible_distance():
    with pytest.raises(ParseError):
        parse_ble_payload(struct.pack("<HiiBBI", 1, -2, 3000, 200, 200, 1), T_HOST)


# --- Log --------------------------------------------------------------------


def _row(**changes):
    row = {
        "t_host_ms": "1790000000123",
        "seq": "1234",
        "d_a": "2940",
        "d_b": "3120",
        "q_a": "210",
        "q_b": "198",
        "t_ms": "123456",
        "x": "2.310",
        "y": "1.870",
        "zone": "mesa",
    }
    row.update(changes)
    return row


def test_log_row_from_the_contract():
    assert parse_log_row(_row()) == EXPECTED


def test_log_row_ignores_position_and_zone():
    assert parse_log_row(_row(x="", y="", zone="")) == EXPECTED
    assert parse_log_row(_row(x="no", y="importa", zone="???")) == EXPECTED


@pytest.mark.parametrize(
    "changes",
    [
        {"seq": ""},
        {"seq": "70000"},
        {"d_a": "2.94"},
        {"t_host_ms": "ayer"},
        {"t_host_ms": "-5"},
        {"q_b": None},
    ],
)
def test_corrupt_log_rows(changes):
    with pytest.raises(ParseError):
        parse_log_row(_row(**changes))
