from collections.abc import Callable
from dataclasses import dataclass

from .config import Anchor, Config, FilterParams
from .filters import DistanceFilter, MedianEmaFilter
from .metrics import LatencyEstimator, LossCounter, RateMeter
from .position import Fix, locate, project_to_plane
from .sample import Sample
from .zones import find_zone

FilterFactory = Callable[[FilterParams], DistanceFilter]


@dataclass(frozen=True)
class Result:
    fix: Fix | None
    zone: str | None
    message: dict  # mensaje `sample` del contrato

    @property
    def x(self) -> float | None:
        return None if self.fix is None else self.fix.x

    @property
    def y(self) -> float | None:
        return None if self.fix is None else self.fix.y


class _AnchorState:
    def __init__(self, anchor: Anchor, flt: DistanceFilter) -> None:
        self.anchor = anchor
        self.filter = flt
        self.last_accepted_ms: int | None = None

    def reset(self) -> None:
        self.filter.reset()
        self.last_accepted_ms = None


class Pipeline:
    """Convierte cada muestra cruda en el mensaje `sample` que se publica."""

    def __init__(self, config: Config, filter_factory: FilterFactory = MedianEmaFilter.from_params) -> None:
        self._config = config
        self._anchors = [_AnchorState(a, filter_factory(config.filter)) for a in config.anchors]
        self._rate = RateMeter()
        self._loss = LossCounter()
        self._latency = LatencyEstimator()
        self._last_tag_ms: int | None = None
        self._previous: tuple[float, float, int] | None = None  # x, y y t_host_ms de la última posición

    def add_sync(self, t0_ms: float, t1_ms: float, t_tag_ms: int) -> None:
        self._track_tag_clock(t_tag_ms)
        self._latency.add_sync(t0_ms, t1_ms, t_tag_ms)

    def process(self, sample: Sample) -> Result:
        self._track_tag_clock(sample.t_ms)
        cfg = self._config
        now = sample.t_host_ms
        max_age_ms = cfg.positioning.max_age_s * 1000

        ranges_msg = {}
        points, radii = [], []
        for state in self._anchors:
            anchor = state.anchor
            if state.last_accepted_ms is not None and now - state.last_accepted_ms >= max_age_ms:
                # Un ancla sin muestras buenas desde hace max_age_s deja de
                # contar. Su ventana es de otra posición: si se conservara, la
                # primera muestra que se pareciera a ella devolvería el ancla
                # al cálculo con una distancia vieja.
                state.reset()
            raw_mm = sample.distances_mm.get(anchor.id, -1)
            quality = sample.qualities.get(anchor.id, 0)
            out = state.filter.update(raw_mm / 1000.0 if raw_mm >= 0 else None, quality)
            if out.accepted:
                state.last_accepted_ms = now

            d = r = None
            # Tras el reinicio de arriba, un filtro con valor tiene siempre una
            # muestra aceptada de hace menos de max_age_s: es el criterio del
            # contrato para entrar en el cálculo y para publicar d y r.
            if out.value_m is not None:
                d = max(out.value_m + anchor.offset_m, 0.0)
                r = project_to_plane(d, anchor.z_m - cfg.tag_height_m)
                points.append((anchor.x_m, anchor.y_m))
                radii.append(r)
            ranges_msg[anchor.id] = {"d": _rounded(d), "r": _rounded(r), "q": quality, "ok": out.accepted}

        previous = None
        if self._previous is not None and now - self._previous[2] < max_age_ms:
            previous = self._previous[:2]
        fix = locate(points, radii, cfg.positioning, cfg.room, previous)
        zone = None
        if fix is not None:
            self._previous = (fix.x, fix.y, now)
            zone = find_zone(cfg.zones, fix.x, fix.y)

        rate = self._rate.update(now)
        message = {
            "type": "sample",
            "seq": sample.seq,
            "x": _rounded(None if fix is None else fix.x),
            "y": _rounded(None if fix is None else fix.y),
            "err_m": _rounded(None if fix is None else fix.err_m),
            "anchors_used": 0 if fix is None else fix.anchors_used,
            "ranges": ranges_msg,
            "rate_hz": None if rate is None else round(rate, 1),
            "latency_ms": self._latency.update(now, sample.t_ms),
            "lost": self._loss.update(sample.seq),
            "zone": zone,
            "valid": fix is not None and fix.valid,
        }
        return Result(fix=fix, zone=zone, message=message)

    def _track_tag_clock(self, t_tag_ms: int) -> None:
        # millis() solo retrocede si el tag ha reiniciado (o al repetir un replay).
        # Su reloj y su seq empiezan de cero, y lo aprendido ya no vale.
        if self._last_tag_ms is not None and t_tag_ms < self._last_tag_ms:
            for state in self._anchors:
                state.reset()
            self._loss.resync()
            self._latency.reset()
            self._previous = None
        self._last_tag_ms = t_tag_ms


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
