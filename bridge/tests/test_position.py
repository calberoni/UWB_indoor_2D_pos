import math
from dataclasses import replace

import pytest

from helpers import make_sample, process_steady
from uwb_bridge.config import Anchor, PositioningParams, Room
from uwb_bridge.pipeline import Pipeline
from uwb_bridge.position import (
    MAX_HDOP,
    circle_candidates,
    gauss_newton,
    hdop,
    linear_solution,
    locate,
    project_to_plane,
    residual_rms,
)

ANCHORS = [(0.0, 0.0), (4.0, 0.0), (2.0, 4.0)]
PARAMS = PositioningParams()
ROOM = Room(x_min_m=-0.5, x_max_m=4.5, y_min_m=0.0, y_max_m=4.0)


def ranges_to(p, anchors=ANCHORS):
    return [math.dist(p, a) for a in anchors]


# --- Proyección -------------------------------------------------------------


def test_projection_removes_height_difference():
    assert project_to_plane(math.sqrt(8 + 0.6**2), 0.6) == pytest.approx(math.sqrt(8))
    assert project_to_plane(1.0, 0.6) == pytest.approx(0.8)
    assert project_to_plane(2.5, 0.0) == 2.5


def test_projection_uses_magnitude_of_height_difference():
    # Tag por encima del ancla: Δh negativo, misma proyección.
    assert project_to_plane(1.0, -0.6) == pytest.approx(0.8)


@pytest.mark.parametrize("distance", [0.0, 0.3, 0.59, 0.6])
def test_projection_is_zero_when_distance_below_height_difference(distance):
    assert project_to_plane(distance, 0.6) == 0.0


# --- Tres anclas --------------------------------------------------------------


@pytest.mark.parametrize(
    "p",
    [
        (2.0, 2.0),  # dentro del triángulo
        (0.5, 0.3),  # junto a la pared de A y B
        (2.0, 0.0),  # sobre la recta de A y B
        (4.3, 3.8),  # esquina fuera del triángulo
        (-0.4, 3.9),  # la otra esquina
        (2.0, 3.9),  # junto a C
    ],
)
def test_known_points_are_recovered(p):
    fix = locate(ANCHORS, ranges_to(p), PARAMS, ROOM, previous=None)
    assert (fix.x, fix.y) == pytest.approx(p, abs=1e-4)
    assert fix.residual_m == pytest.approx(0.0, abs=1e-6)
    assert fix.anchors_used == 3
    assert fix.valid is True


def test_linear_solution_is_exact_with_exact_ranges():
    for p in [(2.0, 2.0), (-0.3, 3.1), (4.4, 0.2)]:
        assert linear_solution(ANCHORS, ranges_to(p)) == pytest.approx(p)


def test_linear_solution_of_aligned_anchors_is_none():
    line = [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)]
    assert linear_solution(line, [1.0, 1.5, 3.0]) is None


@pytest.mark.parametrize("start", [(20.0, -15.0), (2.0, -2.0), (-10.0, 30.0), (0.0, 0.0), (1e-7, 0.0), (2.0, 4.0)])
def test_gauss_newton_converges_from_a_bad_start(start):
    truth = (1.3, 2.2)
    p = gauss_newton(ANCHORS, ranges_to(truth), start)
    assert p == pytest.approx(truth, abs=1e-3)


def test_locate_ignores_a_wrong_previous_position():
    truth = (3.1, 1.2)
    fix = locate(ANCHORS, ranges_to(truth), PARAMS, ROOM, previous=(-50.0, 80.0))
    assert (fix.x, fix.y) == pytest.approx(truth, abs=1e-3)


def test_more_than_three_anchors():
    anchors = ANCHORS + [(4.0, 4.0), (-0.5, 2.0)]
    truth = (1.7, 2.6)
    fix = locate(anchors, ranges_to(truth, anchors), PARAMS, ROOM, previous=None)
    assert (fix.x, fix.y) == pytest.approx(truth, abs=1e-4)
    assert fix.anchors_used == 5


def test_least_squares_averages_noise():
    truth = (2.0, 2.0)
    noisy = [r + e for r, e in zip(ranges_to(truth), (0.03, -0.02, 0.01))]
    fix = locate(ANCHORS, noisy, PARAMS, ROOM, previous=None)
    assert math.dist((fix.x, fix.y), truth) < 0.05
    assert 0 < fix.residual_m < 0.03
    assert fix.valid is True


# --- Residuo ----------------------------------------------------------------


def test_residual_is_normalised_by_degrees_of_freedom():
    p = (2.0, 2.0)
    ranges = [r + e for r, e in zip(ranges_to(p), (0.3, 0.0, -0.3))]
    assert residual_rms(ANCHORS, ranges, p) == pytest.approx(math.sqrt(0.09 + 0 + 0.09))
    four = ANCHORS + [(4.0, 4.0)]
    ranges4 = [r + e for r, e in zip(ranges_to(p, four), (0.3, 0.0, -0.3, 0.0))]
    assert residual_rms(four, ranges4, p) == pytest.approx(math.sqrt(0.18 / 2))


@pytest.mark.parametrize("p, biased", [((2.0, 3.5), 2), ((0.5, 0.5), 0), ((3.5, 0.5), 1)])
def test_biased_anchor_marks_the_cycle_invalid(p, biased):
    ranges = ranges_to(p)
    ranges[biased] += 0.8
    fix = locate(ANCHORS, ranges, PARAMS, ROOM, previous=p)
    assert fix.residual_m > PARAMS.max_residual_m
    assert fix.valid is False
    # La posición se publica igualmente.
    assert fix.x is not None and fix.err_m >= fix.residual_m


def test_biased_anchor_is_not_always_detected():
    # Con tres anclas solo sobra una distancia: parte del sesgo se absorbe en
    # la posición y el residuo que queda depende de dónde esté el tag. Aun
    # normalizado por N - 2 (se detecta en un 80 % de la sala), en el centro el
    # residuo queda en 0.25 m: el punto se va 83 cm y el ciclo sigue siendo válido.
    p = (2.0, 2.0)
    ranges = ranges_to(p)
    ranges[0] += 0.8
    fix = locate(ANCHORS, ranges, PARAMS, ROOM, previous=p)
    assert fix.residual_m < PARAMS.max_residual_m
    assert fix.valid is True
    assert math.dist((fix.x, fix.y), p) > 0.5


# --- HDOP y error estimado ----------------------------------------------------


def test_hdop_inside_a_good_triangle():
    assert 1.0 < hdop(ANCHORS, (2.0, 1.6)) < 1.4


def test_hdop_grows_approaching_the_line_of_two_anchors():
    two = ANCHORS[:2]
    # En (2, 2) las dos direcciones son perpendiculares: el mejor caso.
    values = [hdop(two, (2.0, y)) for y in (2.0, 1.5, 0.8, 0.4, 0.2, 0.1)]
    assert values == sorted(values)
    assert values[0] == pytest.approx(math.sqrt(2)) and values[-1] > 10


def test_hdop_is_capped_on_the_line_of_two_anchors():
    assert hdop(ANCHORS[:2], (2.0, 0.0)) == MAX_HDOP


def test_hdop_grows_away_from_the_triangle():
    values = [hdop(ANCHORS, (2.0, -y)) for y in (0.5, 2.0, 5.0, 15.0)]
    assert values == sorted(values)
    assert values[-1] > 3


def test_err_m_is_hdop_times_sigma_or_residual():
    p = (2.0, 2.0)
    exact = locate(ANCHORS, ranges_to(p), PARAMS, ROOM, previous=None)
    assert exact.err_m == pytest.approx(hdop(ANCHORS, p) * PARAMS.range_sigma_m, rel=1e-3)
    ranges = ranges_to(p)
    ranges[2] += 0.8
    biased = locate(ANCHORS, ranges, PARAMS, ROOM, previous=p)
    assert biased.err_m == pytest.approx(hdop(ANCHORS, (biased.x, biased.y)) * biased.residual_m, rel=1e-6)


# --- Dos anclas ---------------------------------------------------------------


def test_two_circles_meet_in_two_symmetric_points():
    points, meet = circle_candidates(ANCHORS[0], math.hypot(2, 2), ANCHORS[1], math.hypot(2, 2))
    assert meet is True
    first, second = sorted(points)
    assert first == pytest.approx((2.0, -2.0))
    assert second == pytest.approx((2.0, 2.0))


def test_two_anchors_only_one_candidate_in_the_room():
    # El simétrico de (1.5, 2) respecto a la recta de A y B queda en y = −2, fuera.
    p = (1.5, 2.0)
    fix = locate(ANCHORS[:2], ranges_to(p, ANCHORS[:2]), PARAMS, ROOM, previous=None)
    assert (fix.x, fix.y) == pytest.approx(p)
    assert fix.anchors_used == 2
    assert fix.valid is False


def test_room_margin_counts_as_inside():
    # Con A y C: el simétrico de p queda 6 cm fuera de la sala, dentro del margen.
    anchors = [ANCHORS[0], ANCHORS[2]]
    p = (1.2, 0.2)
    points, _ = circle_candidates(anchors[0], math.dist(p, anchors[0]), anchors[1], math.dist(p, anchors[1]))
    mirror = next(c for c in points if math.dist(c, p) > 0.01)
    assert ROOM.x_min_m - PARAMS.room_margin_m <= mirror[0] < ROOM.x_min_m
    assert locate(anchors, ranges_to(p, anchors), PARAMS, ROOM, previous=None) is None
    tight = replace(PARAMS, room_margin_m=0.0)
    fix = locate(anchors, ranges_to(p, anchors), tight, ROOM, previous=None)
    assert (fix.x, fix.y) == pytest.approx(p)


def test_two_anchors_both_inside_takes_the_one_nearest_the_previous_position():
    anchors = [ANCHORS[0], ANCHORS[2]]  # recta y = 2x, cruza la sala en diagonal
    p = (2.0, 1.0)
    ranges = ranges_to(p, anchors)
    points, _ = circle_candidates(anchors[0], ranges[0], anchors[1], ranges[1])
    mirror = next(c for c in points if math.dist(c, p) > 0.01)
    assert mirror == pytest.approx((-0.4, 2.2))  # también dentro de la sala

    near_p = locate(anchors, ranges, PARAMS, ROOM, previous=(2.1, 1.1))
    assert (near_p.x, near_p.y) == pytest.approx(p)
    near_mirror = locate(anchors, ranges, PARAMS, ROOM, previous=(-0.3, 2.0))
    assert (near_mirror.x, near_mirror.y) == pytest.approx(mirror)


def test_two_anchors_undecidable_gives_no_position():
    anchors = [ANCHORS[0], ANCHORS[2]]
    assert locate(anchors, ranges_to((2.0, 1.0), anchors), PARAMS, ROOM, previous=None) is None
    # Tampoco si las dos soluciones caen fuera de la sala.
    far = (9.0, 1.0)
    assert locate(anchors, ranges_to(far, anchors), PARAMS, ROOM, previous=far) is None


@pytest.mark.parametrize("r_a, r_b, expected", [(1.0, 1.0, (2.0, 0.0)), (1.0, 3.0 - 2.5, (8 / 3 * 1.0, 0.0))])
def test_two_circles_that_do_not_meet(r_a, r_b, expected):
    # Demasiado pequeños para tocarse: punto de la recta en la proporción de los radios.
    fix = locate(ANCHORS[:2], [r_a, r_b], PARAMS, ROOM, previous=None)
    assert (fix.x, fix.y) == pytest.approx(expected)
    assert fix.valid is False
    assert fix.err_m == pytest.approx(MAX_HDOP * fix.residual_m)


def test_one_circle_inside_the_other():
    fix = locate(ANCHORS[:2], [1.0, 6.0], PARAMS, ROOM, previous=None)
    assert (fix.x, fix.y) == pytest.approx((4.0 * 1 / 7, 0.0))
    assert fix.valid is False


def test_fewer_than_two_anchors_gives_no_position():
    assert locate(ANCHORS[:1], [2.0], PARAMS, ROOM, previous=(1.0, 1.0)) is None
    assert locate([], [], PARAMS, ROOM, previous=None) is None


# --- Pipeline -------------------------------------------------------------------


@pytest.mark.parametrize("x, y", [(2.0, 2.0), (0.9, 1.4), (3.5, 0.9), (3.7, 3.4), (-0.3, 0.4)])
def test_pipeline_recovers_position_from_3d_distances(config, raw_mm, x, y):
    result = process_steady(Pipeline(config), raw_mm(x, y))
    assert result.x == pytest.approx(x, abs=0.002)
    assert result.y == pytest.approx(y, abs=0.002)
    assert result.message["anchors_used"] == 3
    assert result.message["valid"] is True


def test_pipeline_message_ranges(config, raw_mm):
    distances = raw_mm(2.0, 2.0)
    message = process_steady(Pipeline(config), distances).message
    assert list(message["ranges"]) == ["a", "b", "c"]
    for anchor_id, entry in message["ranges"].items():
        assert set(entry) == {"d", "r", "q", "ok"}
        assert entry["d"] == pytest.approx(distances[anchor_id] / 1000, abs=0.001)
        assert entry["r"] == pytest.approx(math.sqrt(entry["d"] ** 2 - 0.36), abs=0.001)
        assert entry["q"] == 200 and entry["ok"] is True
    assert message["err_m"] == pytest.approx(hdop(ANCHORS, (2.0, 2.0)) * 0.05, abs=0.002)


def test_pipeline_uses_each_anchor_height(config):
    # Anclas a alturas distintas: la proyección debe usar la de cada una.
    anchors = (
        Anchor("a", 0.0, 0.0, 2.5, 0.0),
        Anchor("b", 4.0, 0.0, 1.2, 0.0),  # a la altura del tag
        Anchor("c", 2.0, 4.0, 0.3, 0.0),  # por debajo del tag
    )
    other = replace(config, anchors=anchors, tag_height_m=1.2)
    truth = (1.4, 2.3)
    distances = {
        a.id: round(math.sqrt((truth[0] - a.x_m) ** 2 + (truth[1] - a.y_m) ** 2 + (a.z_m - 1.2) ** 2) * 1000)
        for a in anchors
    }
    result = process_steady(Pipeline(other), distances)
    assert (result.x, result.y) == pytest.approx(truth, abs=0.002)
    assert result.message["ranges"]["b"]["r"] == pytest.approx(result.message["ranges"]["b"]["d"])


def test_pipeline_adds_anchor_offsets(config, raw_mm):
    # A mide 10 cm de menos, B 5 cm de más y C 20 cm de menos; los offsets lo corrigen.
    offsets = {"a": 0.10, "b": -0.05, "c": 0.20}
    calibrated = replace(config, anchors=tuple(replace(a, offset_m=offsets[a.id]) for a in config.anchors))
    distances = raw_mm(1.5, 2.5)
    measured = {i: d - round(offsets[i] * 1000) for i, d in distances.items()}
    result = process_steady(Pipeline(calibrated), measured)
    assert (result.x, result.y) == pytest.approx((1.5, 2.5), abs=0.002)
    assert result.message["ranges"]["a"]["d"] == pytest.approx(distances["a"] / 1000, abs=0.001)


def test_pipeline_handles_distances_shorter_than_height_difference(config):
    result = process_steady(Pipeline(config), {"a": 300, "b": 300, "c": 300})
    assert all(entry["r"] == 0.0 for entry in result.message["ranges"].values())
    assert result.x is not None  # sin excepción; con residuo enorme
    assert result.message["valid"] is False


def test_pipeline_generic_in_the_number_of_anchors(config, raw_mm):
    # El cálculo no depende de que haya exactamente tres anclas.
    extra = Anchor("d", 4.0, 4.0, 2.2, 0.0)
    four = replace(config, anchors=config.anchors + (extra,))
    truth = (2.5, 1.5)
    distances = raw_mm(*truth)
    distances["d"] = round(math.sqrt((truth[0] - 4) ** 2 + (truth[1] - 4) ** 2 + 1.0**2) * 1000)
    message = process_steady(Pipeline(four), distances).message
    assert list(message["ranges"]) == ["a", "b", "c", "d"]
    assert message["anchors_used"] == 4
    assert (message["x"], message["y"]) == pytest.approx(truth, abs=0.002)


def test_pipeline_has_no_position_with_a_single_anchor(config, raw_mm):
    distances = raw_mm(2.0, 2.0)
    message = process_steady(Pipeline(config), {"a": distances["a"], "b": -1, "c": -1}).message
    assert message["x"] is None and message["y"] is None and message["err_m"] is None
    assert message["anchors_used"] == 0 and message["zone"] is None and message["valid"] is False
    assert message["ranges"]["a"]["d"] is not None
    assert message["ranges"]["b"] == {"d": None, "r": None, "q": 200, "ok": False}


def test_stale_anchor_leaves_the_calculation(config, raw_mm):
    pipeline = Pipeline(config)
    p = (1.5, 2.2)
    distances = raw_mm(*p)
    for seq in range(5):
        pipeline.process(make_sample(distances, seq=seq))
    used, published = [], []
    for seq in range(5, 15):  # C falla: su último valor vale durante max_age_s
        message = pipeline.process(make_sample({**distances, "c": -1}, seq=seq)).message
        used.append(message["anchors_used"])
        assert message["ranges"]["c"]["ok"] is False
        published.append(message["ranges"]["c"]["d"] is not None)
        assert (message["ranges"]["c"]["d"] is None) == (message["ranges"]["c"]["r"] is None)
    # Muestra a los 0.5 s justos: ya no cuenta («menos de max_age_s»).
    assert used == [3] * 4 + [2] * 6
    # d y r siguen el mismo criterio que el cálculo: el último valor mientras
    # sea reciente, null después.
    assert published == [True] * 4 + [False] * 6
    # Con A y B la posición sigue saliendo: el simétrico queda fuera de la sala.
    assert (message["x"], message["y"]) == pytest.approx(p, abs=0.002)
    assert message["valid"] is False


def test_rejected_samples_keep_the_last_value_while_fresh(config, raw_mm):
    pipeline = Pipeline(config)
    distances = raw_mm(2.0, 2.0)
    for seq in range(5):
        pipeline.process(make_sample(distances, seq=seq))
    last_d = pipeline.process(make_sample(distances, seq=5)).message["ranges"]["b"]["d"]
    # Muestras de B con mala calidad: se descartan, pero su valor sigue menos de 0.5 s.
    low_b = {"a": 200, "b": 10, "c": 200}
    entries = [
        pipeline.process(make_sample(distances, seq=seq, qualities=low_b)).message["ranges"]["b"] for seq in range(6, 12)
    ]
    assert [e["ok"] for e in entries] == [False] * 6
    assert [e["q"] for e in entries] == [10] * 6
    assert [e["d"] for e in entries] == [last_d] * 4 + [None] * 2
    assert entries[-1]["r"] is None
    # Al volver las muestras buenas, el filtro de B arranca de cero: la
    # distancia vuelve tras las muestras de calentamiento.
    back = [pipeline.process(make_sample(distances, seq=seq)).message["ranges"]["b"] for seq in range(12, 15)]
    assert [e["ok"] for e in back] == [True] * 3
    assert [e["d"] is None for e in back] == [True, True, False]
    assert back[-1]["d"] == pytest.approx(last_d, abs=0.002)


def test_stale_previous_position_is_not_used_to_disambiguate(config, raw_mm):
    pipeline = Pipeline(config)
    p = (2.0, 1.0)  # con A y C, las dos soluciones caen en la sala
    distances = raw_mm(*p)
    only_a = {**distances, "b": -1, "c": -1}
    only_a_c = {**distances, "b": -1}
    seq = 0
    for batch, count in ((distances, 5), (only_a, 6), (only_a_c, 3)):
        for _ in range(count):
            message = pipeline.process(make_sample(batch, seq=seq)).message
            seq += 1
    # A y C vuelven a estar disponibles, pero la última posición es de hace
    # más de max_age_s: no hay con qué elegir entre las dos soluciones.
    assert message["ranges"]["c"]["d"] is not None and message["ranges"]["b"]["d"] is None
    assert message["x"] is None and message["anchors_used"] == 0


def test_previous_position_disambiguates_while_fresh(config, raw_mm):
    pipeline = Pipeline(config)
    p = (2.0, 1.0)
    distances = raw_mm(*p)
    for seq in range(20):
        message = pipeline.process(make_sample({**distances, "b": -1} if seq >= 5 else distances, seq=seq)).message
    assert message["anchors_used"] == 2
    assert (message["x"], message["y"]) == pytest.approx(p, abs=0.002)
