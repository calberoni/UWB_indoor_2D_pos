from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Protocol

from .config import FilterParams


@dataclass(frozen=True)
class FilterOutput:
    value_m: float | None  # distancia filtrada; None si todavía no hay ninguna
    accepted: bool  # False si la muestra de este ciclo se descartó


class DistanceFilter(Protocol):
    """Interfaz de un filtro por distancia; hay una instancia por ancla."""

    def update(self, distance_m: float | None, quality: int) -> FilterOutput: ...

    def reset(self) -> None: ...


class MedianEmaFilter:
    """Mediana de las últimas muestras válidas seguida de media exponencial.

    El salto de una muestra se mide contra la mediana de las aceptadas, no
    contra la última aceptada. Un outlier de algo menos de max_jump_m se acepta
    (de él se ocupa la mediana), y si pasara a ser la referencia haría rechazar
    las muestras buenas que llegan después y aceptar el siguiente outlier.

    Tras un reinicio no hay salida hasta reunir warmup_samples muestras: con
    una sola, un outlier sería la distancia publicada y quedaría dentro de la
    media exponencial durante muchos ciclos.
    """

    def __init__(
        self,
        median_window: int = 5,
        ema_alpha: float = 0.4,
        min_quality: int = 40,
        max_jump_m: float = 1.5,
        max_jump_rejects: int = 5,
        warmup_samples: int = 3,
    ) -> None:
        self._window: deque[float] = deque(maxlen=median_window)
        self._jump_rejected: deque[float] = deque(maxlen=median_window)
        self._alpha = ema_alpha
        self._min_quality = min_quality
        self._max_jump_m = max_jump_m
        self._max_jump_rejects = max_jump_rejects
        self._warmup_samples = min(warmup_samples, median_window)
        self._ema: float | None = None
        self._jump_rejects = 0

    @classmethod
    def from_params(cls, params: FilterParams) -> "MedianEmaFilter":
        return cls(
            median_window=params.median_window,
            ema_alpha=params.ema_alpha,
            min_quality=params.min_quality,
            max_jump_m=params.max_jump_m,
            max_jump_rejects=params.max_jump_rejects,
        )

    def reset(self) -> None:
        self._window.clear()
        self._jump_rejected.clear()
        self._ema = None
        self._jump_rejects = 0

    def update(self, distance_m: float | None, quality: int) -> FilterOutput:
        if distance_m is None or distance_m < 0 or quality < self._min_quality:
            # Un fallo o una muestra de mala calidad no dicen nada sobre dónde está
            # el tag, así que no tocan la cuenta de saltos seguidos.
            return FilterOutput(self._ema, accepted=False)

        warm = len(self._window) >= self._warmup_samples or self._ema is not None
        # Mientras se calienta no hay referencia fiable para medir saltos: de
        # un outlier entre las primeras se ocupa la mediana.
        if warm and abs(distance_m - median(self._window)) > self._max_jump_m:
            if self._jump_rejects < self._max_jump_rejects:
                self._jump_rejects += 1
                self._jump_rejected.append(distance_m)
                return FilterOutput(self._ema, accepted=False)
            # Tantos saltos seguidos ya no son outliers: el tag se ha movido de
            # verdad. La historia vieja se tira y la ventana arranca con las
            # muestras que se habían descartado, que eran buenas. Arrancar solo
            # con la muestra nueva deja la salida a merced de esa única muestra,
            # y si resulta ser un outlier la posición se va varios metros.
            recent = list(self._jump_rejected)
            self.reset()
            self._window.extend(recent)

        self._jump_rejects = 0
        self._jump_rejected.clear()
        self._window.append(distance_m)
        if self._ema is None and len(self._window) < self._warmup_samples:
            return FilterOutput(None, accepted=True)
        centre = median(self._window)
        self._ema = centre if self._ema is None else self._alpha * centre + (1 - self._alpha) * self._ema
        return FilterOutput(self._ema, accepted=True)
