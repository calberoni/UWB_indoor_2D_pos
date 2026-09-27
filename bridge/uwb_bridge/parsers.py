"""Formatos del contrato de datos: línea serie, notificación BLE y fila del log.

Siguen el protocolo del tag, que mide siempre las anclas a, b y c.
"""

import re
import struct

from .config import PROTOCOL_ANCHOR_IDS
from .sample import RANGING_FAILED, Sample

BLE_FORMAT = "<HHHHBBBI"
BLE_PAYLOAD_SIZE = struct.calcsize(BLE_FORMAT)
BLE_RANGING_FAILED = 0xFFFF
MAX_DISTANCE_MM = 65534

RAW_COLUMNS = (
    "seq",
    *(f"d_{i}" for i in PROTOCOL_ANCHOR_IDS),
    *(f"q_{i}" for i in PROTOCOL_ANCHOR_IDS),
    "t_ms",
)
LOG_COLUMNS = ("t_host_ms", *RAW_COLUMNS, "x", "y", "zone")

_COUNT = len(PROTOCOL_ANCHOR_IDS)
# Estricto a propósito: int() aceptaría "1_000", "+5" o dígitos no ASCII, y una
# línea con ruido podría colarse como registro válido.
_RECORD_RE = re.compile(
    r"(\d{1,10})" + r",(-?\d{1,10})" * _COUNT + r",(\d{1,10})" * _COUNT + r",(\d{1,10})",
    re.ASCII,
)
_SYNC_RE = re.compile(r"#\s*SYNC\s+(\d{1,10})", re.ASCII)
_INT_RE = re.compile(r"-?\d{1,15}", re.ASCII)

_UINT32_MAX = 2**32 - 1


class ParseError(ValueError):
    pass


def _to_text(line: str | bytes) -> str:
    if isinstance(line, (bytes, bytearray)):
        line = bytes(line).decode("ascii", errors="replace")
    return line.strip()


def is_comment(line: str | bytes) -> bool:
    return _to_text(line).startswith("#")


def parse_sync_reply(line: str | bytes) -> int | None:
    """Devuelve el t_ms de una respuesta `# SYNC <t_ms>`, o None si la línea es otra cosa."""
    match = _SYNC_RE.fullmatch(_to_text(line))
    if match is None:
        return None
    t_ms = int(match.group(1))
    return t_ms if t_ms <= _UINT32_MAX else None


def parse_serial_line(line: str | bytes, t_host_ms: int) -> Sample | None:
    """Convierte una línea serie en muestra.

    Devuelve None para líneas vacías y para las que empiezan por `#`.
    Lanza ParseError si la línea debería ser un registro y no lo es.
    """
    text = _to_text(line)
    if not text or text.startswith("#"):
        return None
    match = _RECORD_RE.fullmatch(text)
    if match is None:
        raise ParseError(f"línea no reconocida: {text[:80]!r}")
    return _checked_sample(t_host_ms, [int(g) for g in match.groups()])


def parse_ble_payload(payload: bytes, t_host_ms: int) -> Sample:
    if len(payload) != BLE_PAYLOAD_SIZE:
        raise ParseError(f"payload BLE de {len(payload)} bytes; se esperaban {BLE_PAYLOAD_SIZE}")
    values = list(struct.unpack(BLE_FORMAT, bytes(payload)))
    for i in range(1, 1 + _COUNT):
        if values[i] == BLE_RANGING_FAILED:
            values[i] = RANGING_FAILED
    return _checked_sample(t_host_ms, values)


def parse_log_row(row: dict) -> Sample:
    """Convierte una fila del log de sesión; solo usa t_host_ms y las columnas crudas."""
    values = []
    for column in ("t_host_ms", *RAW_COLUMNS):
        text = (row.get(column) or "").strip()
        if _INT_RE.fullmatch(text) is None:
            raise ParseError(f"columna {column} no válida: {text[:20]!r}")
        values.append(int(text))
    if values[0] < 0:
        raise ParseError(f"t_host_ms negativo: {values[0]}")
    return _checked_sample(values[0], values[1:])


def _checked_sample(t_host_ms: int, values: list[int]) -> Sample:
    """values: seq, distancias, calidades y t_ms, en el orden del protocolo."""
    seq, t_ms = values[0], values[-1]
    distances = values[1 : 1 + _COUNT]
    qualities = values[1 + _COUNT : 1 + 2 * _COUNT]
    if not 0 <= seq <= 0xFFFF:
        raise ParseError(f"seq fuera de rango: {seq}")
    for anchor, d in zip(PROTOCOL_ANCHOR_IDS, distances):
        if d != RANGING_FAILED and not 0 <= d <= MAX_DISTANCE_MM:
            raise ParseError(f"d_{anchor} fuera de rango: {d}")
    for anchor, q in zip(PROTOCOL_ANCHOR_IDS, qualities):
        if not 0 <= q <= 0xFF:
            raise ParseError(f"q_{anchor} fuera de rango: {q}")
    if not 0 <= t_ms <= _UINT32_MAX:
        raise ParseError(f"t_ms fuera de rango: {t_ms}")
    return Sample(
        t_host_ms=t_host_ms,
        seq=seq,
        distances_mm=dict(zip(PROTOCOL_ANCHOR_IDS, distances)),
        qualities=dict(zip(PROTOCOL_ANCHOR_IDS, qualities)),
        t_ms=t_ms,
    )
