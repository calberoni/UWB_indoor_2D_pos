import re
import struct

from .sample import RANGING_FAILED, Sample

BLE_FORMAT = "<HiiBBI"
BLE_PAYLOAD_SIZE = struct.calcsize(BLE_FORMAT)

LOG_COLUMNS = ("t_host_ms", "seq", "d_a", "d_b", "q_a", "q_b", "t_ms", "x", "y", "zone")

# Estricto a propósito: int() aceptaría "1_000", "+5" o dígitos no ASCII, y una
# línea con ruido podría colarse como registro válido.
_RECORD_RE = re.compile(r"(\d{1,10}),(-?\d{1,10}),(-?\d{1,10}),(\d{1,10}),(\d{1,10}),(\d{1,10})", re.ASCII)
_SYNC_RE = re.compile(r"#\s*SYNC\s+(\d{1,10})", re.ASCII)
_INT_RE = re.compile(r"-?\d{1,15}", re.ASCII)

_INT32_MAX = 2**31 - 1
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
        raise ParseError(f"línea no reconocida: {text[:60]!r}")
    seq, d_a, d_b, q_a, q_b, t_ms = (int(g) for g in match.groups())
    return _checked_sample(t_host_ms, seq, d_a, d_b, q_a, q_b, t_ms)


def parse_ble_payload(payload: bytes, t_host_ms: int) -> Sample:
    if len(payload) != BLE_PAYLOAD_SIZE:
        raise ParseError(f"payload BLE de {len(payload)} bytes; se esperaban {BLE_PAYLOAD_SIZE}")
    seq, d_a, d_b, q_a, q_b, t_ms = struct.unpack(BLE_FORMAT, bytes(payload))
    return _checked_sample(t_host_ms, seq, d_a, d_b, q_a, q_b, t_ms)


def parse_log_row(row: dict) -> Sample:
    """Convierte una fila del log de sesión; solo usa t_host_ms y las columnas crudas."""
    values = []
    for column in LOG_COLUMNS[:7]:
        text = (row.get(column) or "").strip()
        if _INT_RE.fullmatch(text) is None:
            raise ParseError(f"columna {column} no válida: {text[:20]!r}")
        values.append(int(text))
    t_host_ms, seq, d_a, d_b, q_a, q_b, t_ms = values
    if t_host_ms < 0:
        raise ParseError(f"t_host_ms negativo: {t_host_ms}")
    return _checked_sample(t_host_ms, seq, d_a, d_b, q_a, q_b, t_ms)


def _checked_sample(t_host_ms: int, seq: int, d_a: int, d_b: int, q_a: int, q_b: int, t_ms: int) -> Sample:
    if not 0 <= seq <= 0xFFFF:
        raise ParseError(f"seq fuera de rango: {seq}")
    for name, d in (("d_a", d_a), ("d_b", d_b)):
        if not RANGING_FAILED <= d <= _INT32_MAX:
            raise ParseError(f"{name} fuera de rango: {d}")
    for name, q in (("q_a", q_a), ("q_b", q_b)):
        if not 0 <= q <= 0xFF:
            raise ParseError(f"{name} fuera de rango: {q}")
    if not 0 <= t_ms <= _UINT32_MAX:
        raise ParseError(f"t_ms fuera de rango: {t_ms}")
    return Sample(t_host_ms=t_host_ms, seq=seq, d_a=d_a, d_b=d_b, q_a=q_a, q_b=q_b, t_ms=t_ms)
