from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DASHBOARD_DIR = REPO_ROOT / "dashboard"
LOGS_DIR = REPO_ROOT / "logs"


class ConfigError(Exception):
    pass


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
class Display:
    uncertainty_m: float
    wifi_uncertainty_m: float
    trail_s: float


@dataclass(frozen=True)
class Config:
    anchor_distance_m: float
    anchor_height_m: float
    offset_a_m: float
    offset_b_m: float
    tag_height_m: float
    room: Room
    zones: tuple[Zone, ...]
    filter: FilterParams
    display: Display
    ws_port: int
    http_port: int
    serial_baud: int
    ble_name: str
    ble_service_uuid: str
    ble_char_uuid: str

    @property
    def height_diff_m(self) -> float:
        return abs(self.anchor_height_m - self.tag_height_m)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
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
        return _build(data)
    except KeyError as exc:
        raise ConfigError(f"falta la clave {exc.args[0]!r} en {path}") from exc
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"valor no válido en {path}: {exc}") from exc


def _build(data: dict) -> Config:
    anchors = data["anchors"]
    offsets = anchors.get("offset_cm") or {}
    flt = data["filter"]
    room = data["room"]
    display = data["display"]
    server = data["server"]
    transport = data["transport"]

    config = Config(
        anchor_distance_m=float(anchors["distance_m"]),
        anchor_height_m=float(anchors["height_m"]),
        offset_a_m=float(offsets.get("a", 0.0)) / 100.0,
        offset_b_m=float(offsets.get("b", 0.0)) / 100.0,
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

    if config.anchor_distance_m <= 0:
        raise ValueError("anchors.distance_m debe ser mayor que 0")
    if config.filter.median_window < 1:
        raise ValueError("filter.median_window debe ser al menos 1")
    if not 0 < config.filter.ema_alpha <= 1:
        raise ValueError("filter.ema_alpha debe estar en (0, 1]")
    if config.filter.max_jump_rejects < 1:
        raise ValueError("filter.max_jump_rejects debe ser al menos 1")
    return config


def config_message(config: Config, source: str) -> dict:
    """Mensaje `config` del contrato, tal como lo espera el dashboard."""
    return {
        "type": "config",
        "source": source,
        "anchors": {
            "a": {"x": 0.0, "y": 0.0},
            "b": {"x": config.anchor_distance_m, "y": 0.0},
            "height_m": config.anchor_height_m,
        },
        "tag": {"height_m": config.tag_height_m},
        "room": {
            "x_min_m": config.room.x_min_m,
            "x_max_m": config.room.x_max_m,
            "y_min_m": config.room.y_min_m,
            "y_max_m": config.room.y_max_m,
        },
        "zones": [
            {
                "name": z.name,
                "x_m": z.x_m,
                "y_m": z.y_m,
                "width_m": z.width_m,
                "height_m": z.height_m,
            }
            for z in config.zones
        ],
        "display": {
            "uncertainty_m": config.display.uncertainty_m,
            "wifi_uncertainty_m": config.display.wifi_uncertainty_m,
            "trail_s": config.display.trail_s,
        },
    }
