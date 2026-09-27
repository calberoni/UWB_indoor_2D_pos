from abc import ABC, abstractmethod
from collections.abc import Callable

from ..sample import Sample

SampleHandler = Callable[[Sample], None]
# (t0_ms, t1_ms, t_tag_ms): hora de envío y de respuesta del SYNC en el reloj del
# Mac, y el millis() que devolvió el tag.
SyncHandler = Callable[[float, float, int], None]


class TransportError(Exception):
    """Fallo sin remedio (por ejemplo, un log que no existe); no se reintenta."""


class Transport(ABC):
    """Fuente de muestras. Todas entregan el mismo objeto Sample."""

    source: str  # valor del campo `source` del mensaje `config`

    def __init__(self) -> None:
        self.discarded = 0  # registros ilegibles que se han ignorado

    @abstractmethod
    async def run(self, on_sample: SampleHandler, on_sync: SyncHandler | None = None) -> None:
        """Entrega muestras hasta que se cancela la tarea o se agota la fuente.

        Las llamadas a on_sample y on_sync se hacen siempre desde el bucle de
        eventos. Solo el transporte serie produce medidas SYNC.
        """
