import itertools
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DASHBOARD_DIR = REPO_ROOT / "dashboard"
LOGS_DIR = REPO_ROOT / "logs"

# Anclas que conoce el protocolo del tag, en el orden de sus campos.
PROTOCOL_ANCHOR_IDS = ("a", "b", "c")

# Separación mínima del ancla más alejada respecto a la recta de las otras dos,
# como fracción del lado más largo. Por debajo el triángulo es casi una recta y
# la posición perpendicular a ella queda indeterminada.
MIN_TRIANGLE_HEIGHT_RATIO = 0.10


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Anchor:
    id: str
    x_m: float
    y_m: float
    z_m: float
    offset_m: float


@dataclass(frozen=True)
class Zone:
    name: str
    x_m: float
    y_m: float
    width_m: float
    height_m: float  # fondo del rectángulo en el eje y, no una altura sobre el suelo


@dataclass(frozen=True)
class Room:
    x_min_m: float
    x_max_m: float
    y_min_m: float
    y_max_m: float


@dataclass(frozen=True)
class FilterParams:
    median_window: int
    ema_alpha: float
    min_quality: int
    max_jump_m: float
    max_jump_rejects: int


@dataclass(frozen=True)
class PositioningParams:
    range_sigma_m: float = 0.05
    max_residual_m: float = 0.30
    max_age_s: float = 0.5
    room_margin_m: float = 0.30


@dataclass(frozen=True)
class Display:
    uncertainty_m: float
    wifi_uncertainty_m: float
    trail_s: float


@dataclass(frozen=True)
class Config:
    anchors: tuple[Anchor, ...]
    tag_height_m: float
    room: Room
    zones: tuple[Zone, ...]
    filter: FilterParams
    positioning: PositioningParams
    display: Display
    ws_port: int
    http_port: int
    serial_baud: int
    ble_name: str
    ble_service_uuid: str
    ble_char_uuid: str


def load_config(path: Path | str = DEFAULT_CONFIG_PATH, protocol_ids: tuple[str, ...] | None = PROTOCOL_ANCHOR_IDS) -> Config:
    """Lee y valida la configuración.

    Con protocol_ids=None no se exige que las anclas sean las del protocolo del
    tag; sirve para usar el cálculo con otro número de anclas.
    """
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"no se puede leer {path}: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} no es YAML válido: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} está vacío o no es un mapa")

    try:
        return build_config(data, protocol_ids)
    except KeyError as exc:
        raise ConfigError(f"falta la clave {exc.args[0]!r} en {path}") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"valor no válido en {path}: {exc}") from exc


def build_config(data: dict, protocol_ids: tuple[str, ...] | None = PROTOCOL_ANCHOR_IDS) -> Config:
    flt = data["filter"]
    room = data["room"]
    display = data["display"]
    server = data["server"]
    transport = data["transport"]
    positioning = data.get("positioning") or {}
    defaults = PositioningParams()

    raw_anchors = data["anchors"]
    if not isinstance(raw_anchors, list):
        raise ValueError("anchors debe ser una lista de anclas con id, x_m, y_m y z_m")

    config = Config(
        anchors=tuple(
            Anchor(
                id=str(a["id"]),
                x_m=float(a["x_m"]),
                y_m=float(a["y_m"]),
                z_m=float(a["z_m"]),
                offset_m=float(a.get("offset_cm") or 0.0) / 100.0,
            )
            for a in raw_anchors
        ),
        tag_height_m=float(data["tag"]["height_m"]),
        room=Room(
            x_min_m=float(room["x_min_m"]),
            x_max_m=float(room["x_max_m"]),
            y_min_m=float(room["y_min_m"]),
            y_max_m=float(room["y_max_m"]),
        ),
        zones=tuple(
            Zone(
                name=str(z["name"]),
                x_m=float(z["x_m"]),
                y_m=float(z["y_m"]),
                width_m=float(z["width_m"]),
                height_m=float(z["height_m"]),
            )
            for z in (data.get("zones") or [])
        ),
        filter=FilterParams(
            median_window=int(flt["median_window"]),
            ema_alpha=float(flt["ema_alpha"]),
            min_quality=int(flt["min_quality"]),
            max_jump_m=float(flt["max_jump_m"]),
            max_jump_rejects=int(flt.get("max_jump_rejects", 5)),
        ),
        positioning=PositioningParams(
            range_sigma_m=float(positioning.get("range_sigma_m", defaults.range_sigma_m)),
            max_residual_m=float(positioning.get("max_residual_m", defaults.max_residual_m)),
            max_age_s=float(positioning.get("max_age_s", defaults.max_age_s)),
            room_margin_m=float(positioning.get("room_margin_m", defaults.room_margin_m)),
        ),
        display=Display(
            uncertainty_m=float(display["uncertainty_m"]),
            wifi_uncertainty_m=float(display["wifi_uncertainty_m"]),
            trail_s=float(display["trail_s"]),
        ),
        ws_port=int(server["ws_port"]),
        http_port=int(server["http_port"]),
        serial_baud=int(transport["serial_baud"]),
        ble_name=str(transport["ble_name"]),
        ble_service_uuid=str(transport["ble_service_uuid"]),
        ble_char_uuid=str(transport["ble_char_uuid"]),
    )

    _check_anchors(config.anchors, protocol_ids)
    if config.filter.median_window < 1:
        raise ValueError("filter.median_window debe ser al menos 1")
    if not 0 < config.filter.ema_alpha <= 1:
        raise ValueError("filter.ema_alpha debe estar en (0, 1]")
    if config.filter.max_jump_rejects < 1:
        raise ValueError("filter.max_jump_rejects debe ser al menos 1")
    p = config.positioning
    if p.range_sigma_m <= 0 or p.max_residual_m <= 0 or p.max_age_s <= 0 or p.room_margin_m < 0:
        raise ValueError("los parámetros de positioning deben ser positivos")
    return config


def _check_anchors(anchors: tuple[Anchor, ...], protocol_ids: tuple[str, ...] | None) -> None:
    ids = [a.id for a in anchors]
    if len(set(ids)) != len(ids):
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"ids de ancla repetidas: {', '.join(repeated)}")
    if len(anchors) < 3:
        raise ValueError(f"hacen falta al menos 3 anclas; hay {len(anchors)}")
    if protocol_ids is not None and set(ids) != set(protocol_ids):
        raise ValueError(
            f"las anclas deben ser {', '.join(protocol_ids)}, las que mide el tag; en la configuración hay {', '.join(ids)}"
        )

    # El triángulo más ancho que se puede formar con las anclas decide si hay
    # geometría para posicionar.
    best_ratio, best_height, longest_side = 0.0, 0.0, 0.0
    for p, q, r in itertools.combinations(anchors, 3):
        sides = [math.dist((p.x_m, p.y_m), (q.x_m, q.y_m)), math.dist((q.x_m, q.y_m), (r.x_m, r.y_m)),
                 math.dist((r.x_m, r.y_m), (p.x_m, p.y_m))]
        longest = max(sides)
        if longest == 0:
            continue
        area = abs((q.x_m - p.x_m) * (r.y_m - p.y_m) - (r.x_m - p.x_m) * (q.y_m - p.y_m)) / 2
        height = 2 * area / longest
        if height / longest > best_ratio:
            best_ratio, best_height, longest_side = height / longest, height, longest
    if best_ratio < MIN_TRIANGLE_HEIGHT_RATIO:
        raise ValueError(
            f"las anclas están casi alineadas: la más separada queda a {best_height:.2f} m de la recta de las otras "
            f"(mínimo {MIN_TRIANGLE_HEIGHT_RATIO * max(longest_side, 1e-9):.2f} m, el "
            f"{MIN_TRIANGLE_HEIGHT_RATIO:.0%} del lado más largo); sin esa separación no se puede posicionar"
        )


def config_message(config: Config, source: str) -> dict:
    """Mensaje `config` del contrato, tal como lo espera el dashboard."""
    return {
        "type": "config",
        "source": source,
        "anchors": [{"id": a.id, "x": a.x_m, "y": a.y_m, "z": a.z_m} for a in config.anchors],
        "tag": {"height_m": config.tag_height_m},
        "room": {
            "x_min_m": config.room.x_min_m,
            "x_max_m": config.room.x_max_m,
            "y_min_m": config.room.y_min_m,
            "y_max_m": config.room.y_max_m,
        },
        "zones": [
            {"name": z.name, "x_m": z.x_m, "y_m": z.y_m, "width_m": z.width_m, "height_m": z.height_m}
            for z in config.zones
        ],
        "display": {
            "uncertainty_m": config.display.uncertainty_m,
            "wifi_uncertainty_m": config.display.wifi_uncertainty_m,
            "trail_s": config.display.trail_s,
        },
    }
