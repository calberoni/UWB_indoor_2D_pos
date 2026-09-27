#!/usr/bin/env python3
"""Genera logs sintéticos con el formato de log del contrato de datos.

Sirve para desarrollar y probar el bridge y el dashboard sin hardware. La
geometría (anclas con su altura, altura del tag, sala y zonas) sale de
config.yaml. Con los mismos
argumentos y la misma semilla el fichero generado es idéntico.

Los dos logs de ejemplo del repo se generaron así:

    python tools/sim.py rect --out logs/ejemplo-rectangulo.csv
    python tools/sim.py walk --out logs/ejemplo-paseo.csv

Otros usos: un punto quieto, y un paseo con el ancla C tapada entre los
segundos 22 y 24.5:

    python tools/sim.py static --x 2.0 --y 2.0 --out logs/estatico.csv
    python tools/sim.py walk --obstruction c:22:2.5 --out logs/paseo-tapado.csv
"""

import argparse
import bisect
import csv
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

# El protocolo del tag mide siempre estas tres anclas, en este orden.
ANCHOR_IDS = ("a", "b", "c")
LOG_COLUMNS = (
    "t_host_ms",
    "seq",
    *(f"d_{i}" for i in ANCHOR_IDS),
    *(f"q_{i}" for i in ANCHOR_IDS),
    "t_ms",
    "x",
    "y",
    "zone",
)
MAX_DISTANCE_MM = 65534
SCENARIOS = ("static", "rect", "walk")

PERIOD_MS = 100
HOST_START_MS = 1_790_000_000_000  # fijo, para que el log no dependa de la hora
TAG_START_MS = 60_000
SEQ_MODULO = 0x10000

RECT_WIDTH_M = 3.0
RECT_DEPTH_M = 2.0
RECT_SPEED_MPS = 1.0
WALK_SPEED_MPS = 0.8
WALK_DWELL_S = 1.5
WALL_MARGIN_M = 0.2


@dataclass(frozen=True)
class Geometry:
    anchors: dict  # id → (x, y, z)
    tag_height_m: float
    room: dict
    zones: list


@dataclass(frozen=True)
class Obstruction:
    anchor: str  # id del ancla
    start_s: float
    duration_s: float

    def covers(self, t_s: float) -> bool:
        return self.start_s <= t_s < self.start_s + self.duration_s


@dataclass(frozen=True)
class Options:
    scenario: str = "static"
    duration_s: float = 30.0
    seed: int = 1
    x_m: float | None = None  # solo para static; por defecto, el centro de la sala
    y_m: float | None = None
    sigma_m: float = 0.03
    outlier_rate: float = 0.02
    lost_rate: float = 0.01
    fail_rate: float = 0.01
    obstructions: tuple[Obstruction, ...] = field(default_factory=tuple)
    seq_start: int = 0


def load_geometry(path: Path | str = DEFAULT_CONFIG_PATH) -> Geometry:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    anchors = {str(a["id"]): (float(a["x_m"]), float(a["y_m"]), float(a["z_m"])) for a in data["anchors"]}
    if set(anchors) != set(ANCHOR_IDS):
        raise ValueError(f"el simulador necesita las anclas {', '.join(ANCHOR_IDS)}; la configuración tiene {', '.join(anchors)}")
    return Geometry(
        anchors=anchors,
        tag_height_m=float(data["tag"]["height_m"]),
        room={k: float(v) for k, v in data["room"].items()},
        zones=list(data.get("zones") or []),
    )


def true_distances(geometry: Geometry, x: float, y: float) -> dict[str, float]:
    """Distancias 3D del tag a cada ancla, como las mide la radio."""
    h = geometry.tag_height_m
    return {i: math.sqrt((x - ax) ** 2 + (y - ay) ** 2 + (az - h) ** 2) for i, (ax, ay, az) in geometry.anchors.items()}


def zone_at(geometry: Geometry, x: float, y: float) -> str:
    for zone in geometry.zones:
        if zone["x_m"] <= x <= zone["x_m"] + zone["width_m"] and zone["y_m"] <= y <= zone["y_m"] + zone["height_m"]:
            return str(zone["name"])
    return ""


# --- Trayectorias -----------------------------------------------------------


def _room_centre(geometry: Geometry) -> tuple[float, float]:
    room = geometry.room
    return (room["x_min_m"] + room["x_max_m"]) / 2, (room["y_min_m"] + room["y_max_m"]) / 2


def static_path(geometry: Geometry, options: Options):
    cx, cy = _room_centre(geometry)
    x = cx if options.x_m is None else options.x_m
    y = cy if options.y_m is None else options.y_m
    return lambda t_s: (x, y)


def rect_path(geometry: Geometry):
    cx, cy = _room_centre(geometry)
    x0, y0 = cx - RECT_WIDTH_M / 2, cy - RECT_DEPTH_M / 2
    corners = [
        (x0, y0),
        (x0 + RECT_WIDTH_M, y0),
        (x0 + RECT_WIDTH_M, y0 + RECT_DEPTH_M),
        (x0, y0 + RECT_DEPTH_M),
    ]
    perimeter = 2 * (RECT_WIDTH_M + RECT_DEPTH_M)

    def position(t_s: float) -> tuple[float, float]:
        s = (t_s * RECT_SPEED_MPS) % perimeter
        for i, (ax, ay) in enumerate(corners):
            bx, by = corners[(i + 1) % 4]
            side = math.hypot(bx - ax, by - ay)
            if s <= side:
                return ax + (bx - ax) * s / side, ay + (by - ay) * s / side
            s -= side
        return corners[0]

    return position


def _catmull_rom(p0, p1, p2, p3, u: float) -> tuple[float, float]:
    return tuple(
        0.5 * (2 * b + (c - a) * u + (2 * a - 5 * b + 4 * c - d) * u**2 + (3 * b - a - 3 * c + d) * u**3)
        for a, b, c, d in zip(p0, p1, p2, p3)
    )


def _walk_waypoints(geometry: Geometry) -> list[tuple[float, float]]:
    points = [(z["x_m"] + z["width_m"] / 2, z["y_m"] + z["height_m"] / 2) for z in geometry.zones]
    if len(points) >= 3:
        return points
    # Con menos de tres zonas no sale un circuito: se completa con una elipse.
    cx, cy = _room_centre(geometry)
    rx = (geometry.room["x_max_m"] - geometry.room["x_min_m"]) * 0.3
    ry = (geometry.room["y_max_m"] - geometry.room["y_min_m"]) * 0.3
    return points + [(cx + rx * math.cos(a), cy + ry * math.sin(a)) for a in (0.5, 2.6, 4.7)]


def walk_path(geometry: Geometry):
    """Circuito cerrado y suave que pasa por el centro de cada zona y se detiene en él."""
    waypoints = _walk_waypoints(geometry)
    count = len(waypoints)
    room = geometry.room

    legs = []  # por tramo: (longitudes acumuladas, puntos)
    for i in range(count):
        p0, p1, p2, p3 = (waypoints[(i + k - 1) % count] for k in range(4))
        points = [_catmull_rom(p0, p1, p2, p3, step / 200) for step in range(201)]
        points = [
            (
                min(max(x, room["x_min_m"] + WALL_MARGIN_M), room["x_max_m"] - WALL_MARGIN_M),
                min(max(y, room["y_min_m"] + WALL_MARGIN_M), room["y_max_m"] - WALL_MARGIN_M),
            )
            for x, y in points
        ]
        lengths = [0.0]
        for a, b in zip(points, points[1:]):
            lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        legs.append((lengths, points))

    durations = [WALK_DWELL_S + lengths[-1] / WALK_SPEED_MPS for lengths, _ in legs]
    lap_s = sum(durations)

    def position(t_s: float) -> tuple[float, float]:
        t = t_s % lap_s
        for (lengths, points), duration in zip(legs, durations):
            if t < duration:
                break
            t -= duration
        moving = min(max(t - WALK_DWELL_S, 0.0) / (duration - WALK_DWELL_S), 1.0)
        # Arranque y frenada progresivos: una persona no pasa de 0 a 0.8 m/s de golpe.
        eased = moving * moving * (3 - 2 * moving)
        target = eased * lengths[-1]
        i = min(bisect.bisect_right(lengths, target), len(lengths) - 1)
        span = lengths[i] - lengths[i - 1]
        f = (target - lengths[i - 1]) / span if span > 0 else 0.0
        (ax, ay), (bx, by) = points[i - 1], points[i]
        return ax + (bx - ax) * f, ay + (by - ay) * f

    return position


def build_path(geometry: Geometry, options: Options):
    if options.scenario == "static":
        return static_path(geometry, options)
    if options.scenario == "rect":
        return rect_path(geometry)
    if options.scenario == "walk":
        return walk_path(geometry)
    raise ValueError(f"escenario desconocido: {options.scenario}")


# --- Medidas ----------------------------------------------------------------


def _measure(rng: random.Random, true_m: float, options: Options, bias_m: float | None) -> tuple[int, int]:
    """Devuelve (distancia en mm, calidad) de un ranging."""
    if rng.random() < options.fail_rate:
        return -1, 0

    distance = true_m + rng.gauss(0.0, options.sigma_m)
    if bias_m is None:
        quality = round(rng.gauss(200, 12))
    else:
        # Con el cuerpo en medio el primer camino llega atenuado y tarde. La
        # calidad cae a ambos lados del umbral del filtro, como pasará en la
        # sala: unas muestras se descartan y otras entran con su sesgo.
        distance += bias_m
        quality = rng.randint(15, 60)

    if rng.random() < options.outlier_rate:
        jump = rng.uniform(0.8, 4.0)
        # Casi siempre de más (rebotes); de menos solo si queda una distancia posible.
        if rng.random() < 0.25 and distance - jump > 0.1:
            jump = -jump
        distance += jump

    return min(max(round(distance * 1000), 0), MAX_DISTANCE_MM), min(max(quality, 0), 255)


def generate(options: Options, geometry: Geometry) -> list[dict]:
    # El escenario entra en la semilla para que dos logs con la misma semilla no
    # repitan el mismo patrón de ruido, fallos y pérdidas.
    rng = random.Random(f"{options.scenario}:{options.seed}")
    position = build_path(geometry, options)
    biases = {o: rng.uniform(0.20, 0.50) for o in options.obstructions}

    rows = []
    for cycle in range(round(options.duration_s * 1000 / PERIOD_MS)):
        t_s = cycle * PERIOD_MS / 1000
        x, y = position(t_s)
        true = true_distances(geometry, x, y)

        bias = dict.fromkeys(ANCHOR_IDS)
        for obstruction, bias_m in biases.items():
            if obstruction.covers(t_s):
                bias[obstruction.anchor] = bias_m

        measured = {i: _measure(rng, true[i], options, bias[i]) for i in ANCHOR_IDS}
        # Retraso entre el envío del tag y la llegada al Mac: una base fija más
        # una cola, siempre menor que un ciclo para que el orden no cambie.
        delay_ms = 12 + min(rng.expovariate(1 / 6.0), 70.0)
        lost = rng.random() < options.lost_rate
        if lost:
            continue

        rows.append(
            {
                "t_host_ms": HOST_START_MS + cycle * PERIOD_MS + round(delay_ms),
                "seq": (options.seq_start + cycle) % SEQ_MODULO,
                **{f"d_{i}": measured[i][0] for i in ANCHOR_IDS},
                **{f"q_{i}": measured[i][1] for i in ANCHOR_IDS},
                "t_ms": TAG_START_MS + cycle * PERIOD_MS,
                "x": f"{x:.3f}",
                "y": f"{y:.3f}",
                "zone": zone_at(geometry, x, y),
            }
        )
    return rows


def write_log(rows: list[dict], file) -> None:
    writer = csv.DictWriter(file, fieldnames=LOG_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)


def simulate(options: Options, out_path: Path | str, config_path: Path | str = DEFAULT_CONFIG_PATH) -> list[dict]:
    rows = generate(options, load_geometry(config_path))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as file:
        write_log(rows, file)
    return rows


# --- Línea de comandos ------------------------------------------------------


def _obstruction(text: str) -> Obstruction:
    try:
        anchor, start, duration = text.split(":")
        anchor = anchor.lower()
        if anchor not in ANCHOR_IDS:
            raise ValueError
        return Obstruction(anchor, float(start), float(duration))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} no tiene la forma ANCLA:INICIO:DURACIÓN, p. ej. b:12:3") from None


def _rate(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("debe estar entre 0 y 1")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera un log sintético de una sesión UWB.")
    parser.add_argument("scenario", choices=SCENARIOS, help="static: punto quieto; rect: rectángulo de 3 × 2 m; walk: paseo por las zonas")
    parser.add_argument("--out", metavar="FICHERO", help="por defecto, la salida estándar")
    parser.add_argument("--config", metavar="FICHERO", default=DEFAULT_CONFIG_PATH, help="por defecto, config.yaml del repo")
    parser.add_argument("--duration", type=float, default=30.0, metavar="S", help="duración en segundos (por defecto 30)")
    parser.add_argument("--seed", type=int, default=1, help="semilla del generador (por defecto 1)")
    parser.add_argument("--x", type=float, metavar="M", help="posición x para static (por defecto, el centro de la sala)")
    parser.add_argument("--y", type=float, metavar="M", help="posición y para static (por defecto, el centro de la sala)")
    parser.add_argument("--sigma-cm", type=float, default=3.0, metavar="CM", help="desviación típica del ruido (por defecto 3)")
    parser.add_argument("--outliers", type=_rate, default=0.02, metavar="P", help="fracción de outliers (por defecto 0.02)")
    parser.add_argument("--lost", type=_rate, default=0.01, metavar="P", help="fracción de ciclos perdidos (por defecto 0.01)")
    parser.add_argument("--failed", type=_rate, default=0.01, metavar="P", help="fracción de rangings fallidos (por defecto 0.01)")
    parser.add_argument(
        "--obstruction",
        type=_obstruction,
        action="append",
        default=[],
        metavar="ANCLA:INICIO:DURACIÓN",
        help="tramo con un ancla tapada, en segundos; se puede repetir",
    )
    args = parser.parse_args(argv)

    options = Options(
        scenario=args.scenario,
        duration_s=args.duration,
        seed=args.seed,
        x_m=args.x,
        y_m=args.y,
        sigma_m=args.sigma_cm / 100,
        outlier_rate=args.outliers,
        lost_rate=args.lost,
        fail_rate=args.failed,
        obstructions=tuple(args.obstruction),
    )

    if args.out is None:
        write_log(generate(options, load_geometry(args.config)), sys.stdout)
        return 0
    rows = simulate(options, args.out, args.config)
    print(f"{args.out}: {len(rows)} muestras, {options.duration_s:g} s, escenario {options.scenario}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
