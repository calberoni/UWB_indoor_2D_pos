from dataclasses import dataclass

RANGING_FAILED = -1


@dataclass(frozen=True)
class Sample:
    """Registro crudo de un ciclo, igual para los tres transportes."""

    t_host_ms: int  # hora del Mac al recibir (epoch)
    seq: int
    d_a: int  # mm; RANGING_FAILED si el ranging falló
    d_b: int
    q_a: int
    q_b: int
    t_ms: int  # millis() del tag
