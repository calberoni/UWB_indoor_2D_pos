from collections.abc import Iterable

from .config import Zone


def find_zone(zones: Iterable[Zone], x: float, y: float) -> str | None:
    """Nombre de la primera zona que contiene el punto, en el orden de la configuración."""
    for zone in zones:
        if zone.x_m <= x <= zone.x_m + zone.width_m and zone.y_m <= y <= zone.y_m + zone.height_m:
            return zone.name
    return None
