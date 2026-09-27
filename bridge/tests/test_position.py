import math
from dataclasses import replace

import pytest

from uwb_bridge.pipeline import Pipeline
from uwb_bridge.position import intersect, project_to_plane
from uwb_bridge.sample import Sample

D = 4.0


@pytest.mark.parametrize(
    "x, y",
    [
        (2.0, 2.0),  # centrado
        (0.0, 3.0),  # justo delante de A: triángulo 3-4-5 con B
        (4.0, 3.0),  # justo delante de B
        (1.0, 0.5),  # cerca de la pared
        (-0.4, 1.5),  # a la izquierda de A
        (4.4, 2.5),  # a la derecha de B
        (2.0, 0.0),  # sobre la línea de las anclas
    ],
)
def test_intersect_recovers_known_point(x, y):
    r_a = math.hypot(x, y)
    r_b = math.hypot(x - D, y)
    got_x, got_y = intersect(r_a, r_b, D)
    assert got_x == pytest.approx(x, abs=1e-9)
    assert got_y == pytest.approx(y, abs=1e-6)


def test_intersect_3_4_5():
    assert intersect(3.0, 5.0, 4.0) == pytest.approx((0.0, 3.0))


def test_intersect_never_returns_negative_y():
    for r_a, r_b in [(0.5, 3.9), (3.9, 0.5), (2.0, 2.0), (0.0, 4.0)]:
        assert intersect(r_a, r_b, D)[1] >= 0.0


def test_circles_too_small_to_meet():
    # 1 + 1 < 4: no hay corte. El punto queda sobre la pared, entre las anclas.
    assert intersect(1.0, 1.0, D) == pytest.approx((2.0, 0.0))


def test_circle_inside_the_other():
    # |6 − 1| > 4: un círculo contiene al otro.
    x, y = intersect(1.0, 6.0, D)
    assert y == 0.0
    assert x == pytest.approx((1 - 36 + 16) / 8)


def test_zero_radii():
    assert intersect(0.0, 0.0, D) == pytest.approx((2.0, 0.0))


def test_projection_removes_height_difference():
    assert project_to_plane(math.sqrt(8 + 0.6**2), 0.6) == pytest.approx(math.sqrt(8))
    assert project_to_plane(1.0, 0.6) == pytest.approx(0.8)
    assert project_to_plane(2.5, 0.0) == 2.5


def test_projection_uses_magnitude_of_height_difference():
    # Tag por encima de las anclas: Δh negativo, misma proyección.
    assert project_to_plane(1.0, -0.6) == pytest.approx(0.8)


@pytest.mark.parametrize("distance", [0.0, 0.3, 0.59, 0.6])
def test_projection_is_zero_when_distance_below_height_difference(distance):
    assert project_to_plane(distance, 0.6) == 0.0


def _sample(d_a: int, d_b: int, seq: int = 0) -> Sample:
    return Sample(t_host_ms=1_000_000 + seq * 100, seq=seq, d_a=d_a, d_b=d_b, q_a=200, q_b=200, t_ms=seq * 100)


@pytest.mark.parametrize("x, y", [(2.0, 2.0), (0.9, 1.4), (3.5, 0.9), (2.1, 3.6)])
def test_pipeline_recovers_position_from_3d_distances(config, raw_mm, x, y):
    d_a, d_b = raw_mm(x, y)
    result = Pipeline(config).process(_sample(d_a, d_b))
    assert result.x == pytest.approx(x, abs=0.002)
    assert result.y == pytest.approx(y, abs=0.002)


def test_pipeline_reports_3d_and_projected_distances(config, raw_mm):
    d_a, d_b = raw_mm(2.0, 2.0)
    message = Pipeline(config).process(_sample(d_a, d_b)).message
    assert message["d_a"] == pytest.approx(math.sqrt(8 + 0.36), abs=0.001)
    assert message["r_a"] == pytest.approx(math.sqrt(8), abs=0.001)
    assert message["r_a"] < message["d_a"]


def test_pipeline_takes_heights_and_distance_from_config(config):
    # Otra sala: anclas a 2.5 m, tag a 1.0 m, D = 3 m. Tag en (1, 2).
    other = replace(config, anchor_distance_m=3.0, anchor_height_m=2.5, tag_height_m=1.0)
    d_a = math.sqrt(1 + 4 + 1.5**2)
    d_b = math.sqrt(4 + 4 + 1.5**2)
    result = Pipeline(other).process(_sample(round(d_a * 1000), round(d_b * 1000)))
    assert result.x == pytest.approx(1.0, abs=0.002)
    assert result.y == pytest.approx(2.0, abs=0.002)


def test_pipeline_adds_anchor_offsets(config, raw_mm):
    # El ancla A mide 10 cm de menos y la B 5 cm de más; el offset lo corrige.
    calibrated = replace(config, offset_a_m=0.10, offset_b_m=-0.05)
    d_a, d_b = raw_mm(1.5, 2.5)
    result = Pipeline(calibrated).process(_sample(d_a - 100, d_b + 50))
    assert result.x == pytest.approx(1.5, abs=0.002)
    assert result.y == pytest.approx(2.5, abs=0.002)


def test_pipeline_handles_distances_shorter_than_height_difference(config):
    # 0.3 m < Δh = 0.6 m: radios nulos, sin excepción.
    result = Pipeline(config).process(_sample(300, 300))
    assert (result.message["r_a"], result.message["r_b"]) == (0.0, 0.0)
    assert (result.x, result.y) == pytest.approx((2.0, 0.0))


def test_pipeline_has_no_position_until_both_distances_exist(config, raw_mm):
    pipeline = Pipeline(config)
    d_a, d_b = raw_mm(2.0, 2.0)

    first = pipeline.process(_sample(d_a, -1, seq=0))
    assert first.x is None and first.y is None and first.zone is None
    assert first.message["x"] is None and first.message["d_b"] is None and first.message["r_b"] is None
    assert first.message["d_a"] is not None
    assert first.message["valid"] is False

    second = pipeline.process(_sample(d_a, d_b, seq=1))
    assert second.x == pytest.approx(2.0, abs=0.002)
    assert second.message["valid"] is True
