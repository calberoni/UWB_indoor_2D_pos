import struct

import pytest

from uwb_bridge.parsers import (
    BLE_PAYLOAD_SIZE,
    LOG_COLUMNS,
    ParseError,
    is_comment,
    parse_ble_payload,
    parse_log_row,
    parse_serial_line,
    parse_sync_reply,
)
from uwb_bridge.sample import Sample

T_HOST = 1_790_000_000_123
# El ejemplo del contrato: C ha fallado.
EXPECTED = Sample(
    t_host_ms=T_HOST,
    seq=1234,
    distances_mm={"a": 2940, "b": 3120, "c": -1},
    qualities={"a": 210, "b": 198, "c": 0},
    t_ms=123456,
)
LINE = "1234,2940,3120,-1,210,198,0,123456"


# --- Serie ------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [LINE, LINE + "\n", LINE + "\r\n", (LINE + "\r\n").encode(), bytearray(LINE.encode()), f"  {LINE}  "],
)
def test_serial_line_from_the_contract(line):
    assert parse_serial_line(line, T_HOST) == EXPECTED


def test_serial_all_anchors():
    sample = parse_serial_line("7,1000,2000,3000,11,22,33,500", T_HOST)
    assert sample.distances_mm == {"a": 1000, "b": 2000, "c": 3000}
    assert sample.qualities == {"a": 11, "b": 22, "c": 33}


def test_serial_field_limits():
    sample = parse_serial_line("65535,65534,0,-1,255,0,255,4294967295", T_HOST)
    assert sample.seq == 65535
    assert sample.distances_mm == {"a": 65534, "b": 0, "c": -1}
    assert sample.t_ms == 2**32 - 1


@pytest.mark.parametrize(
    "line",
    [
        "# arranque: DEV_ID 0xDECA0302",
        "#",
        "# SYNC 123456",
        "# ANT 16400",
        "# INFO rol=tag dir=0x0001 dev_id=0xDECA0302 ant=16385 radio=canal 5, 6.8 Mbps",
        "# STAT rol=tag ok=100 timeout=2 error=0 late=0",
        "# RAW c 1 2 3 0 0 0 2900",
        "#" + LINE,
        "  # con espacios delante",
        b"# bytes\r\n",
        "",
        "\r\n",
    ],
)
def test_comment_and_blank_lines_are_not_records(line):
    assert parse_serial_line(line, T_HOST) is None


def test_is_comment():
    assert is_comment("# RATE 10")
    assert is_comment(b"# RATE 10\r\n")
    assert not is_comment(LINE)


@pytest.mark.parametrize(
    "line",
    [
        "1234,2940,3120,210,198,123456",  # formato antiguo de dos anclas
        "1234,2940,3120,-1,210,198,0",  # falta un campo
        "1234,2940,3120,-1,210,198,0,123456,7",  # sobra un campo
        "2940,3120,-1,210,198,0,123456",  # principio cortado
        "1234,2940,3120,-1,210,198,0,",  # final cortado
        "1234,2940,,-1,210,198,0,123456",
        "1234,29x0,3120,-1,210,198,0,123456",
        "1234,2940.5,3120,-1,210,198,0,123456",
        "1234;2940;3120;-1;210;198;0;123456",
        "1_234,2940,3120,-1,210,198,0,123456",
        "+1234,2940,3120,-1,210,198,0,123456",
        "-1,2940,3120,-1,210,198,0,123456",  # seq negativo
        "65536,2940,3120,-1,210,198,0,123456",  # seq no cabe en uint16
        "1234,-2,3120,-1,210,198,0,123456",  # solo −1 significa fallo
        "1234,2940,65535,-1,210,198,0,123456",  # 65535 solo existe en binario
        "1234,2940,3120,70000,210,198,0,123456",
        "1234,2940,3120,-1,256,198,0,123456",  # calidad no cabe en uint8
        "1234,2940,3120,-1,210,198,-1,123456",
        "1234,2940,3120,-1,210,198,0,4294967296",  # t_ms no cabe en uint32
        LINE + " " + LINE,  # dos líneas pegadas
        "hola",
        "SYNC",
        "١٢٣٤,2940,3120,-1,210,198,0,123456",  # dígitos no ASCII
        b"\xff\xfe\x00\x01garbage\x80",
        b"1234,2940,31\x0020,-1,210,198,0,123456",
    ],
)
def test_corrupt_serial_lines_raise_parse_error(line):
    with pytest.raises(ParseError):
        parse_serial_line(line, T_HOST)


def test_corrupt_lines_never_raise_anything_else():
    # Barrido de bytes arbitrarios: el parser solo puede devolver o lanzar ParseError.
    for value in range(256):
        for line in (bytes([value]) * 3, b"1234,2940," + bytes([value]) + b",-1,210,198,0,1"):
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
        (LINE, None),
    ],
)
def test_sync_reply(line, expected):
    assert parse_sync_reply(line) == expected


# --- BLE --------------------------------------------------------------------


def test_ble_payload_is_15_bytes():
    assert BLE_PAYLOAD_SIZE == 15


def test_ble_payload_from_the_contract():
    payload = struct.pack("<HHHHBBBI", 1234, 2940, 3120, 0xFFFF, 210, 198, 0, 123456)
    assert len(payload) == 15
    assert parse_ble_payload(payload, T_HOST) == EXPECTED


def test_ble_payload_byte_layout():
    # Little-endian, sin relleno: seq(2) d_a(2) d_b(2) d_c(2) q_a q_b q_c t_ms(4).
    payload = bytes.fromhex("d204" "7c0b" "300c" "ffff" "d2" "c6" "00" "40e20100")
    assert parse_ble_payload(payload, T_HOST) == EXPECTED


def test_ble_payload_accepts_bytearray():
    payload = bytearray(struct.pack("<HHHHBBBI", 1234, 2940, 3120, 0xFFFF, 210, 198, 0, 123456))
    assert parse_ble_payload(payload, T_HOST) == EXPECTED


def test_ble_failed_rangings_and_limits():
    payload = struct.pack("<HHHHBBBI", 65535, 0xFFFF, 65534, 0, 0, 255, 7, 2**32 - 1)
    sample = parse_ble_payload(payload, T_HOST)
    assert sample.seq == 65535
    assert sample.distances_mm == {"a": -1, "b": 65534, "c": 0}
    assert sample.qualities == {"a": 0, "b": 255, "c": 7}
    assert sample.t_ms == 2**32 - 1


@pytest.mark.parametrize("size", [0, 1, 14, 16, 20])
def test_ble_payload_of_wrong_size(size):
    with pytest.raises(ParseError):
        parse_ble_payload(bytes(size), T_HOST)


# --- Log --------------------------------------------------------------------


def test_log_columns_follow_the_contract():
    assert ",".join(LOG_COLUMNS) == "t_host_ms,seq,d_a,d_b,d_c,q_a,q_b,q_c,t_ms,x,y,zone"


def _row(**changes):
    row = dict(zip(LOG_COLUMNS, "1790000000123,1234,2940,3120,-1,210,198,0,123456,2.310,1.870,mesa".split(",")))
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
        {"d_c": "-2"},
        {"t_host_ms": "ayer"},
        {"t_host_ms": "-5"},
        {"q_c": None},
    ],
)
def test_corrupt_log_rows(changes):
    with pytest.raises(ParseError):
        parse_log_row(_row(**changes))
