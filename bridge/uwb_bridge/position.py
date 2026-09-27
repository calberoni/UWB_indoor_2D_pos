"""Posición 2D a partir de las distancias a las anclas. Ver docs/posicionamiento.md."""

import math
from dataclasses import dataclass

from .config import PositioningParams, Room

Point = tuple[float, float]

MAX_ITERATIONS = 10
STEP_TOLERANCE_M = 0.001
# Con dos anclas en línea con el tag (o círculos que no se cortan) la geometría
# no dice nada en la dirección perpendicular y HDOP es infinito. Se limita para
# que err_m siga siendo un número que el dashboard pueda dibujar.
MAX_HDOP = 20.0
_SINGULAR = 1e-12


@dataclass(frozen=True)
class Fix:
    x: float
    y: float
    err_m: float
    residual_m: float
    anchors_used: int
    valid: bool


def project_to_plane(distance_m: float, height_diff_m: float) -> float:
    """Proyecta una distancia 3D al plano horizontal del tag."""
    if distance_m <= abs(height_diff_m):
        return 0.0
    return math.sqrt(distance_m**2 - height_diff_m**2)


def linear_solution(anchors: list[Point], ranges: list[float]) -> Point | None:
    """Mínimos cuadrados del sistema lineal que queda al restar el primer círculo a los demás."""
    (x0, y0), r0 = anchors[0], ranges[0]
    # Ecuaciones normales 2×2 de A·p = b, con una fila por ancla a partir de la segunda.
    sxx = sxy = syy = bx = by = 0.0
    for (xi, yi), ri in zip(anchors[1:], ranges[1:]):
        ax, ay = 2 * (xi - x0), 2 * (yi - y0)
        b = r0**2 - ri**2 + xi**2 - x0**2 + yi**2 - y0**2
        sxx += ax * ax
        sxy += ax * ay
        syy += ay * ay
        bx += ax * b
        by += ay * b
    return _solve_2x2(sxx, sxy, syy, bx, by)


def gauss_newton(anchors: list[Point], ranges: list[float], start: Point) -> Point | None:
    """Minimiza Σ(‖p − a_i‖ − r_i)². Devuelve None si la geometría es singular."""
    x, y = start
    for _ in range(MAX_ITERATIONS):
        jxx = jxy = jyy = gx = gy = 0.0
        for (ax, ay), r in zip(anchors, ranges):
            dx, dy = x - ax, y - ay
            dist = math.hypot(dx, dy)
            if dist < 1e-9:
                # Encima del ancla la dirección no está definida; cualquier
                # desplazamiento pequeño la define.
                dx, dy, dist = 1e-6, 0.0, 1e-6
            ux, uy = dx / dist, dy / dist
            f = dist - r
            jxx += ux * ux
            jxy += ux * uy
            jyy += uy * uy
            gx += ux * f
            gy += uy * f
        step = _solve_2x2(jxx, jxy, jyy, -gx, -gy)
        if step is None:
            return None
        x, y = x + step[0], y + step[1]
        if math.hypot(*step) < STEP_TOLERANCE_M:
            break
    return x, y


def residual_rms(anchors: list[Point], ranges: list[float], p: Point) -> float:
    """Residuo por grado de libertad: la posición ya absorbe dos distancias.

    Dividir por N en lugar de N - 2 esconde buena parte del sesgo de un ancla
    con tres anclas, que solo dejan una distancia de sobra.
    """
    total = sum((math.dist(p, a) - r) ** 2 for a, r in zip(anchors, ranges))
    return math.sqrt(total / max(len(anchors) - 2, 1))


def hdop(anchors: list[Point], p: Point) -> float:
    """√traza((HᵀH)⁻¹), con H las direcciones de cada ancla al punto; limitado a MAX_HDOP."""
    sxx = sxy = syy = 0.0
    rows = 0
    for a in anchors:
        dist = math.dist(p, a)
        if dist < 1e-9:
            continue
        ux, uy = (p[0] - a[0]) / dist, (p[1] - a[1]) / dist
        sxx += ux * ux
        sxy += ux * uy
        syy += uy * uy
        rows += 1
    det = sxx * syy - sxy * sxy
    if det <= _SINGULAR:
        return MAX_HDOP
    # Con vectores unitarios la traza de HᵀH es el número de filas.
    return min(math.sqrt(rows / det), MAX_HDOP)


def circle_candidates(a1: Point, r1: float, a2: Point, r2: float) -> tuple[list[Point], bool]:
    """Cortes de dos círculos. Devuelve (puntos, se_cortan).

    Si no se cortan, un único punto en la recta de las anclas, en la
    proporción de los radios.
    """
    base = math.dist(a1, a2)
    ex, ey = (a2[0] - a1[0]) / base, (a2[1] - a1[1]) / base
    along = (r1**2 - r2**2 + base**2) / (2 * base)
    h2 = r1**2 - along**2
    if h2 < 0 or r1 + r2 < base or abs(r1 - r2) > base:
        share = 0.5 if r1 + r2 == 0 else r1 / (r1 + r2)
        return [(a1[0] + ex * base * share, a1[1] + ey * base * share)], False
    h = math.sqrt(h2)
    mx, my = a1[0] + ex * along, a1[1] + ey * along
    if h < STEP_TOLERANCE_M:
        return [(mx, my)], True
    return [(mx - ey * h, my + ex * h), (mx + ey * h, my - ex * h)], True


def locate(
    anchors: list[Point],
    ranges: list[float],
    params: PositioningParams,
    room: Room,
    previous: Point | None,
) -> Fix | None:
    """Posición con las anclas elegibles. previous es la posición anterior, si es reciente."""
    if len(anchors) >= 3:
        return _locate_least_squares(anchors, ranges, params, previous)
    if len(anchors) == 2:
        return _locate_two(anchors, ranges, params, room, previous)
    return None


def _locate_least_squares(anchors, ranges, params, previous) -> Fix | None:
    linear = linear_solution(anchors, ranges)
    starts = [s for s in (previous, linear) if s is not None]
    best = None
    for start in starts:
        p = gauss_newton(anchors, ranges, start)
        if p is None:
            continue
        residual = residual_rms(anchors, ranges, p)
        if best is None or residual < best[1]:
            best = (p, residual)
        # Desde la posición anterior casi siempre basta. Solo si el resultado
        # no encaja se prueba desde la solución lineal, por si la anterior
        # estaba lejos y Gauss-Newton se quedó en un mínimo local.
        if residual <= params.max_residual_m:
            break
    if best is None:
        return None
    p, residual = best
    err = hdop(anchors, p) * max(params.range_sigma_m, residual)
    return Fix(p[0], p[1], err, residual, len(anchors), valid=residual <= params.max_residual_m)


def _locate_two(anchors, ranges, params, room, previous) -> Fix | None:
    if math.dist(anchors[0], anchors[1]) < 1e-9:
        return None
    candidates, _ = circle_candidates(anchors[0], ranges[0], anchors[1], ranges[1])
    if len(candidates) == 1:
        chosen = candidates[0]
    else:
        inside = [c for c in candidates if _in_room(c, room, params.room_margin_m)]
        if len(inside) == 1:
            chosen = inside[0]
        elif len(inside) == 2 and previous is not None:
            chosen = min(inside, key=lambda c: math.dist(c, previous))
        else:
            return None
    residual = residual_rms(anchors, ranges, chosen)
    err = hdop(anchors, chosen) * max(params.range_sigma_m, residual)
    return Fix(chosen[0], chosen[1], err, residual, 2, valid=False)


def _in_room(p: Point, room: Room, margin: float) -> bool:
    return (
        room.x_min_m - margin <= p[0] <= room.x_max_m + margin
        and room.y_min_m - margin <= p[1] <= room.y_max_m + margin
    )


def _solve_2x2(a: float, b: float, d: float, e: float, f: float) -> Point | None:
    """Resuelve [[a, b], [b, d]]·v = (e, f)."""
    det = a * d - b * b
    scale = max(abs(a), abs(d), 1e-300)
    if abs(det) <= _SINGULAR * scale * scale:
        return None
    return (d * e - b * f) / det, (a * f - b * e) / det
