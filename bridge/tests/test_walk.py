"""Escenarios simulados completos: anclas que se caen, se tapan o miden con sesgo."""

import math

import pytest

import sim
from uwb_bridge.parsers import parse_log_row
from uwb_bridge.pipeline import Pipeline

WARMUP_S = 2.0
DROPOUT_S = 3.0


def elapsed_s(row) -> float:
    return (row["t_ms"] - sim.TAG_START_MS) / 1000


def run(config, rows):
    pipeline = Pipeline(config)
    return [(row, pipeline.process(parse_log_row({k: str(v) for k, v in row.items()}))) for row in rows]


def without_anchor(rows, anchor, start_s, duration_s=DROPOUT_S):
    """El ranging con esa ancla falla durante el tramo, como si se hubiera apagado."""
    return [
        {**row, f"d_{anchor}": -1, f"q_{anchor}": 0} if start_s <= elapsed_s(row) < start_s + duration_s else row
        for row in rows
    ]


def error(row, result) -> float:
    return math.hypot(result.x - float(row["x"]), result.y - float(row["y"]))


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("start_s", [12.0, 20.0])
def test_anchor_down_for_3_s_does_not_make_the_position_jump(config, geometry, seed, start_s):
    rows = without_anchor(sim.generate(sim.Options(scenario="walk", seed=seed), geometry), "c", start_s)
    results = [(row, r) for row, r in run(config, rows) if elapsed_s(row) >= WARMUP_S]

    assert all(r.x is not None for _, r in results)  # nunca se queda sin posición
    points = [(r.x, r.y) for _, r in results]
    steps = [math.dist(p, q) for p, q in zip(points, points[1:])]
    assert max(steps) < 0.30

    during = [(row, r) for row, r in results if start_s + 0.5 <= elapsed_s(row) < start_s + DROPOUT_S]
    assert all(r.message["anchors_used"] == 2 for _, r in during)
    assert all(r.message["valid"] is False for _, r in during)
    assert all(r.message["ranges"]["c"]["d"] is None for _, r in during)
    assert max(error(row, r) for row, r in during) < 0.8


def test_third_anchor_returns_after_the_dropout(config, geometry):
    start_s = 12.0
    rows = without_anchor(sim.generate(sim.Options(scenario="walk", seed=1, fail_rate=0, outlier_rate=0), geometry), "c", start_s)
    after = [r for row, r in run(config, rows) if start_s + DROPOUT_S + 0.5 <= elapsed_s(row) < start_s + DROPOUT_S + 2]
    assert all(r.message["anchors_used"] == 3 for r in after)
    assert all(r.message["valid"] for r in after)


@pytest.mark.parametrize("anchor", ["a", "b", "c"])
def test_obstructed_anchor_keeps_a_position(config, geometry, anchor):
    # Un cuerpo delante de un ancla: sesgo de 20–50 cm y calidad baja durante 10 s.
    options = sim.Options(scenario="static", x_m=2.0, y_m=2.0, seed=3, obstructions=(sim.Obstruction(anchor, 10.0, 10.0),))
    results = [(row, r) for row, r in run(config, sim.generate(options, geometry)) if elapsed_s(row) >= WARMUP_S]
    assert all(r.x is not None for _, r in results)
    covered = [error(row, r) for row, r in results if 10.0 <= elapsed_s(row) < 20.0]
    clear = [error(row, r) for row, r in results if elapsed_s(row) >= 22.0]
    assert max(clear) < 0.10
    assert sorted(covered)[len(covered) // 2] < 0.40


@pytest.mark.parametrize("p, anchor", [((2.0, 3.5), "c"), ((0.5, 0.5), "a")])
def test_anchor_with_a_bias_marks_the_cycles_invalid(config, geometry, p, anchor):
    # Un rebote sin línea de visión: el ancla mide 0.8 m de más todo el rato.
    options = sim.Options(scenario="static", x_m=p[0], y_m=p[1], seed=2, outlier_rate=0)
    rows = [
        {**row, f"d_{anchor}": row[f"d_{anchor}"] + 800} if row[f"d_{anchor}"] != -1 else row
        for row in sim.generate(options, geometry)
    ]
    settled = [r.message for row, r in run(config, rows) if elapsed_s(row) >= WARMUP_S]
    invalid = sum(not m["valid"] for m in settled)
    assert invalid > 0.9 * len(settled)
    assert all(m["x"] is not None and m["anchors_used"] == 3 for m in settled)
    # El error estimado refleja el residuo: el círculo de incertidumbre crece.
    assert min(m["err_m"] for m in settled) > 0.3


def test_err_m_is_small_in_the_middle_of_the_room(config, geometry):
    options = sim.Options(scenario="static", x_m=2.0, y_m=2.0, seed=1)
    settled = [r.message for row, r in run(config, sim.generate(options, geometry)) if elapsed_s(row) >= WARMUP_S]
    assert all(0.04 < m["err_m"] < 0.10 for m in settled)
