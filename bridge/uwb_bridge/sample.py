from dataclasses import dataclass

RANGING_FAILED = -1


@dataclass(frozen=True)
class Sample:
    """Registro crudo de un ciclo, igual para los tres transportes."""

    t_host_ms: int  # hora del Mac al recibir (epoch)
    seq: int
    distances_mm: dict[str, int]  # por id de ancla; RANGING_FAILED si el ranging falló
    qualities: dict[str, int]  # por id de ancla
    t_ms: int  # millis() del tag
