from collections.abc import Callable
from dataclasses import dataclass

from .config import Config, FilterParams
from .filters import DistanceFilter, MedianEmaFilter
from .metrics import LatencyEstimator, LossCounter, RateMeter
from .position import intersect, project_to_plane
from .sample import Sample
from .zones import find_zone

FilterFactory = Callable[[FilterParams], DistanceFilter]


@dataclass(frozen=True)
class Result:
    x: float | None
    y: float | None
    zone: str | None
    message: dict  # mensaje `sample` del contrato


class Pipeline:
    """Convierte cada muestra cruda en el mensaje `sample` que se publica."""

    def __init__(self, config: Config, filter_factory: FilterFactory = MedianEmaFilter.from_params) -> None:
        self._config = config
        self._filter_a = filter_factory(config.filter)
        self._filter_b = filter_factory(config.filter)
        self._rate = RateMeter()
        self._loss = LossCounter()
        self._latency = LatencyEstimator()
        self._last_tag_ms: int | None = None

    def add_sync(self, t0_ms: float, t1_ms: float, t_tag_ms: int) -> None:
        self._track_tag_clock(t_tag_ms)
        self._latency.add_sync(t0_ms, t1_ms, t_tag_ms)

    def process(self, sample: Sample) -> Result:
        self._track_tag_clock(sample.t_ms)
        cfg = self._config

        out_a = self._filter_a.update(_to_metres(sample.d_a), sample.q_a)
        out_b = self._filter_b.update(_to_metres(sample.d_b), sample.q_b)

        d_a = r_a = d_b = r_b = None
        if out_a.value_m is not None:
            d_a = max(out_a.value_m + cfg.offset_a_m, 0.0)
            r_a = project_to_plane(d_a, cfg.height_diff_m)
        if out_b.value_m is not None:
            d_b = max(out_b.value_m + cfg.offset_b_m, 0.0)
            r_b = project_to_plane(d_b, cfg.height_diff_m)

        x = y = zone = None
        if r_a is not None and r_b is not None:
            x, y = intersect(r_a, r_b, cfg.anchor_distance_m)
            zone = find_zone(cfg.zones, x, y)

        message = {
            "type": "sample",
            "seq": sample.seq,
            "x": _rounded(x),
            "y": _rounded(y),
            "d_a": _rounded(d_a),
            "d_b": _rounded(d_b),
            "r_a": _rounded(r_a),
            "r_b": _rounded(r_b),
            "q_a": sample.q_a,
            "q_b": sample.q_b,
            "rate_hz": round(self._rate.update(sample.t_host_ms), 1),
            "latency_ms": self._latency.update(sample.t_host_ms, sample.t_ms),
            "lost": self._loss.update(sample.seq),
            "zone": zone,
            "valid": out_a.accepted and out_b.accepted,
        }
        return Result(x=x, y=y, zone=zone, message=message)

    def _track_tag_clock(self, t_tag_ms: int) -> None:
        # millis() solo retrocede si el tag ha reiniciado (o al repetir un replay).
        # Su reloj y su seq empiezan de cero, y lo aprendido ya no vale.
        if self._last_tag_ms is not None and t_tag_ms < self._last_tag_ms:
            self._filter_a.reset()
            self._filter_b.reset()
            self._loss.resync()
            self._latency.reset()
        self._last_tag_ms = t_tag_ms


def _to_metres(raw_mm: int) -> float | None:
    return raw_mm / 1000.0 if raw_mm >= 0 else None


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
