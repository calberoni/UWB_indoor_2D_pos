"""Utilidades compartidas por los tests."""

from uwb_bridge.sample import Sample

GOOD_QUALITY = 200


def make_sample(distances_mm: dict, seq: int = 0, t_ms: int | None = None, t_host_ms: int | None = None, qualities: dict | None = None) -> Sample:
    """Muestra con las distancias dadas; por defecto a 10 Hz y con buena calidad."""
    return Sample(
        t_host_ms=1_790_000_000_000 + seq * 100 if t_host_ms is None else t_host_ms,
        seq=seq,
        distances_mm=dict(distances_mm),
        qualities=qualities or dict.fromkeys(distances_mm, GOOD_QUALITY),
        t_ms=seq * 100 if t_ms is None else t_ms,
    )


WARMUP = 3  # muestras que necesita el filtro tras arrancar o reiniciarse


def process_steady(pipeline, distances_mm: dict, count: int = WARMUP, first_seq: int = 0, **kwargs):
    """Procesa la misma medida `count` veces seguidas y devuelve el último resultado."""
    for seq in range(first_seq, first_seq + count):
        result = pipeline.process(make_sample(distances_mm, seq=seq, **kwargs))
    return result
