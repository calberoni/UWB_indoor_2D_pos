import asyncio
import csv
import math
import time

import pytest

import sim
from helpers import make_sample
from uwb_bridge.pipeline import Pipeline
from uwb_bridge.session_log import SessionLog
from uwb_bridge.transports import TransportError
from uwb_bridge.transports.replay import ReplayTransport

WARMUP_SAMPLES = 20  # 2 s: margen para que el filtro se estabilice
FAST = 500.0


def replay(path, config, speed=FAST):
    """Reproduce un log por el transporte de replay y devuelve los resultados del pipeline."""
    pipeline = Pipeline(config)
    results = []
    transport = ReplayTransport(path, speed=speed)
    asyncio.run(transport.run(lambda sample: results.append((sample, pipeline.process(sample)))))
    return results


def write_static_log(path, config_path, x, y, seed, duration_s=30.0):
    options = sim.Options(scenario="static", x_m=x, y_m=y, seed=seed, duration_s=duration_s)
    return sim.simulate(options, path, config_path)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize(
    "x, y",
    [
        (2.0, 2.0),  # centro
        (1.0, 2.0),
        (2.0, 0.3),  # junto a la pared de A y B
        (-0.2, 2.0),  # fuera del triángulo, junto a la pared izquierda
        (4.2, 3.7),  # esquina fuera del triángulo
        (2.0, 3.7),  # junto a C
    ],
)
def test_static_tag_stays_within_10_cm(tmp_path, config, config_path, x, y, seed):
    path = tmp_path / "estatico.csv"
    rows = write_static_log(path, config_path, x, y, seed)
    results = replay(path, config)
    assert len(results) == len(rows) > 280

    settled = [result for _, result in results[WARMUP_SAMPLES:]]
    assert all(r.x is not None for r in settled)

    centre_x = sum(r.x for r in settled) / len(settled)
    centre_y = sum(r.y for r in settled) / len(settled)
    spread = max(math.hypot(r.x - centre_x, r.y - centre_y) for r in settled)
    error = max(math.hypot(r.x - x, r.y - y) for r in settled)
    assert spread < 0.10  # el punto no se mueve
    assert error < 0.10  # y además está donde debe


def test_static_log_contains_the_disturbances_the_filter_must_survive(tmp_path, config_path, geometry):
    # Si el simulador dejara de meter ruido, el test anterior no probaría nada.
    rows = write_static_log(tmp_path / "estatico.csv", config_path, 2.0, 2.0, seed=1, duration_s=120.0)
    true_mm = {i: d * 1000 for i, d in sim.true_distances(geometry, 2.0, 2.0).items()}
    errors = [row[f"d_{i}"] - true_mm[i] if row[f"d_{i}"] != -1 else None for row in rows for i in "abc"]

    failed = sum(e is None for e in errors)
    outliers = sum(e is not None and abs(e) > 500 for e in errors)
    lost = 1200 - len(rows)
    assert 0.003 < failed / len(errors) < 0.03
    assert 0.008 < outliers / len(errors) < 0.04
    assert 0.003 < lost / 1200 < 0.03

    normal = [e for e in errors if e is not None and abs(e) <= 500]
    mean = sum(normal) / len(normal)
    sigma = (sum((e - mean) ** 2 for e in normal) / len(normal)) ** 0.5
    assert mean == pytest.approx(0, abs=5)
    assert sigma == pytest.approx(30, abs=4)


def test_replay_reports_lost_cycles_and_rate(tmp_path, config, config_path):
    path = tmp_path / "estatico.csv"
    rows = write_static_log(path, config_path, 2.0, 2.0, seed=1)
    results = replay(path, config)
    last = results[-1][1].message
    assert last["lost"] == 300 - len(rows) - (299 - rows[-1]["seq"]) - rows[0]["seq"]
    assert last["lost"] > 0
    rates = [r.message["rate_hz"] for _, r in results[WARMUP_SAMPLES:]]
    assert 9.5 < sum(rates) / len(rates) <= 10.0
    assert all(0 <= r.message["latency_ms"] < 100 for _, r in results)


def test_simulator_is_reproducible(tmp_path, config_path):
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    other = tmp_path / "c.csv"
    options = sim.Options(scenario="walk", seed=9, obstructions=(sim.Obstruction("b", 5.0, 3.0),))
    sim.simulate(options, first, config_path)
    sim.simulate(options, second, config_path)
    sim.simulate(sim.Options(scenario="walk", seed=10), other, config_path)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() != other.read_bytes()


def test_simulated_distances_are_3d(config_path, geometry):
    options = sim.Options(scenario="static", x_m=0.0, y_m=0.0, sigma_m=0.0, outlier_rate=0, lost_rate=0, fail_rate=0, duration_s=1)
    rows = sim.generate(options, geometry)
    # Tag justo debajo del ancla A: la distancia es la diferencia de altura, no cero.
    assert {row["d_a"] for row in rows} == {600}
    assert {row["d_b"] for row in rows} == {round(math.sqrt(16 + 0.36) * 1000)}
    assert {row["d_c"] for row in rows} == {round(math.sqrt(4 + 16 + 0.36) * 1000)}


def test_simulator_uses_each_anchor_height(tmp_path, config_path):
    import yaml

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["anchors"][2]["z_m"] = 2.7  # C más alta: 1.5 m por encima del tag
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    options = sim.Options(scenario="static", x_m=2.0, y_m=4.0, sigma_m=0.0, outlier_rate=0, lost_rate=0, fail_rate=0, duration_s=1)
    rows = sim.generate(options, sim.load_geometry(path))
    assert {row["d_c"] for row in rows} == {1500}
    assert {row["d_a"] for row in rows} == {round(math.sqrt(4 + 16 + 0.36) * 1000)}


@pytest.mark.parametrize("anchor", ["a", "b", "c"])
def test_obstruction_biases_one_anchor_with_low_quality(geometry, anchor):
    options = sim.Options(
        scenario="static", x_m=2.0, y_m=2.0, sigma_m=0.0, outlier_rate=0, lost_rate=0, fail_rate=0,
        duration_s=4.0, obstructions=(sim.Obstruction(anchor, 1.0, 2.0),),
    )
    rows = sim.generate(options, geometry)
    true_mm = {i: round(d * 1000) for i, d in sim.true_distances(geometry, 2.0, 2.0).items()}
    covered = [row for row in rows if 1.0 <= (row["t_ms"] - sim.TAG_START_MS) / 1000 < 3.0]
    assert len(covered) == 20
    for row in covered:
        assert 200 <= row[f"d_{anchor}"] - true_mm[anchor] <= 500
        assert row[f"q_{anchor}"] <= 60
        for other in set("abc") - {anchor}:
            assert row[f"d_{other}"] == true_mm[other]


@pytest.mark.parametrize("scenario", ["rect", "walk"])
def test_moving_scenarios_stay_in_the_room_and_cross_zones(config, geometry, scenario):
    rows = sim.generate(sim.Options(scenario=scenario, duration_s=30.0), geometry)
    xs = [float(row["x"]) for row in rows]
    ys = [float(row["y"]) for row in rows]
    assert config.room.x_min_m <= min(xs) and max(xs) <= config.room.x_max_m
    assert config.room.y_min_m < min(ys) and max(ys) <= config.room.y_max_m
    zones = {row["zone"] for row in rows} - {""}
    assert zones == {"mesa", "estantería", "puerta"}


def test_rect_scenario_walks_a_3_by_2_rectangle_at_1_mps(geometry):
    options = sim.Options(scenario="rect", duration_s=10.0, lost_rate=0)
    rows = sim.generate(options, geometry)
    points = [(float(row["x"]), float(row["y"])) for row in rows]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    assert max(xs) - min(xs) == pytest.approx(3.0)
    assert max(ys) - min(ys) == pytest.approx(2.0)
    steps = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    assert max(steps) == pytest.approx(0.1, abs=0.001)


def test_replay_runs_at_real_speed(tmp_path, config_path):
    path = tmp_path / "corto.csv"
    options = sim.Options(scenario="static", duration_s=1.0, lost_rate=0)
    rows = sim.simulate(options, path, config_path)
    span_s = (rows[-1]["t_host_ms"] - rows[0]["t_host_ms"]) / 1000

    arrivals = []
    started = time.monotonic()
    asyncio.run(ReplayTransport(path).run(lambda sample: arrivals.append(time.monotonic() - started)))

    assert len(arrivals) == len(rows) == 10
    assert arrivals[-1] == pytest.approx(span_s, abs=0.15)
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    assert all(0.03 < gap < 0.25 for gap in gaps)


def test_replay_speed_factor(tmp_path, config_path):
    path = tmp_path / "corto.csv"
    sim.simulate(sim.Options(scenario="static", duration_s=2.0, lost_rate=0), path, config_path)
    started = time.monotonic()
    asyncio.run(ReplayTransport(path, speed=4.0).run(lambda sample: None))
    assert time.monotonic() - started == pytest.approx(1.9 / 4, abs=0.2)


def test_replay_follows_gaps_in_the_log(tmp_path):
    path = tmp_path / "hueco.csv"
    path.write_text(
        "t_host_ms,seq,d_a,d_b,d_c,q_a,q_b,q_c,t_ms,x,y,zone\n"
        "1000,0,2900,2900,2900,200,200,200,0,,,\n"
        "1100,1,2900,2900,2900,200,200,200,100,,,\n"
        "1500,5,2900,2900,2900,200,200,200,500,,,\n",
        encoding="utf-8",
    )
    arrivals = []
    started = time.monotonic()
    asyncio.run(ReplayTransport(path).run(lambda sample: arrivals.append(time.monotonic() - started)))
    assert arrivals == pytest.approx([0.0, 0.1, 0.5], abs=0.09)


def test_replay_delivers_log_timestamps_not_current_time(tmp_path, config_path):
    path = tmp_path / "corto.csv"
    rows = sim.simulate(sim.Options(scenario="static", duration_s=1.0), path, config_path)
    samples = []
    asyncio.run(ReplayTransport(path, speed=FAST).run(samples.append))
    assert [s.t_host_ms for s in samples] == [row["t_host_ms"] for row in rows]
    assert [s.seq for s in samples] == [row["seq"] for row in rows]


def test_replay_skips_corrupt_rows(tmp_path):
    path = tmp_path / "sucio.csv"
    path.write_text(
        "t_host_ms,seq,d_a,d_b,d_c,q_a,q_b,q_c,t_ms,x,y,zone\n"
        "1000,0,2900,2900,2900,200,200,200,0,2.000,2.000,\n"
        "1100,1,29x0,2900,2900,200,200,200,100,,,\n"
        "esto no es una fila\n"
        "1200,2,2900,2900,2900,200,200,200\n"
        "1300,3,-1,2900,2900,0,200,200,300,,,mesa\n",
        encoding="utf-8",
    )
    transport = ReplayTransport(path, speed=FAST)
    samples = []
    asyncio.run(transport.run(samples.append))
    assert [s.seq for s in samples] == [0, 3]
    assert transport.discarded == 3


def test_replay_repeats_as_a_tag_restart(tmp_path, config, config_path):
    path = tmp_path / "corto.csv"
    rows = sim.simulate(sim.Options(scenario="static", duration_s=2.0, lost_rate=0), path, config_path)
    pipeline = Pipeline(config)
    messages = []

    async def run():
        transport = ReplayTransport(path, speed=FAST, repeat=True)
        task = asyncio.ensure_future(transport.run(lambda s: messages.append((s, pipeline.process(s).message))))
        while len(messages) < 3 * len(rows):
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    host_times = [s.t_host_ms for s, _ in messages]
    assert host_times == sorted(host_times)  # la hora del Mac nunca retrocede
    assert messages[len(rows)][0].seq == rows[0]["seq"]
    assert all(m["lost"] == 0 for _, m in messages)
    assert all(m["latency_ms"] < 100 for _, m in messages)


@pytest.mark.parametrize(
    "content, fragment",
    [
        ("", "cabecera"),
        ("seq,d_a,d_b\n1,2,3\n", "cabecera"),
        ("t_host_ms,seq,d_a,d_b,q_a,q_b,t_ms,x,y,zone\n1,2,3,4,5,6,7,,,\n", "faltan d_c, q_c"),
        ("t_host_ms,seq,d_a,d_b,d_c,q_a,q_b,q_c,t_ms,x,y,zone\n", "no contiene muestras"),
    ],
)
def test_replay_rejects_files_that_are_not_logs(tmp_path, content, fragment):
    path = tmp_path / "malo.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(TransportError, match=fragment):
        ReplayTransport(path)


def test_replay_of_missing_file(tmp_path):
    with pytest.raises(TransportError, match="no se puede leer"):
        ReplayTransport(tmp_path / "no-existe.csv")


# --- Log de sesión ----------------------------------------------------------


def test_session_log_follows_the_contract_and_can_be_replayed(tmp_path, config, config_path):
    source = tmp_path / "origen.csv"
    sim.simulate(sim.Options(scenario="walk", duration_s=20.0), source, config_path)
    original = replay(source, config)

    log = SessionLog(tmp_path / "logs")
    for sample, result in original:
        log.write(sample, result)
    log.close()

    assert log.path.parent == tmp_path / "logs"
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "t_host_ms,seq,d_a,d_b,d_c,q_a,q_b,q_c,t_ms,x,y,zone"
    assert len(lines) == len(original) + 1

    with log.path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    for row, (sample, result) in zip(rows, original):
        assert int(row["t_host_ms"]) == sample.t_host_ms and int(row["seq"]) == sample.seq
        assert {i: int(row[f"d_{i}"]) for i in "abc"} == sample.distances_mm
        assert {i: int(row[f"q_{i}"]) for i in "abc"} == sample.qualities
        assert int(row["t_ms"]) == sample.t_ms
        if result.x is None:
            assert row["x"] == row["y"] == ""
        else:
            assert row["x"] == f"{result.x:.3f}" and row["y"] == f"{result.y:.3f}"
        assert row["zone"] == (result.zone or "")
    assert {row["zone"] for row in rows} == {"", "mesa", "estantería", "puerta"}

    # Reproducir el log escrito da exactamente la misma sesión.
    again = replay(log.path, config)
    assert [r.message for _, r in again] == [r.message for _, r in original]


def test_session_log_leaves_position_empty_until_there_is_one(tmp_path, config, raw_mm):
    pipeline = Pipeline(config)
    log = SessionLog(tmp_path)
    exact = raw_mm(2.0, 2.0)
    for seq in range(5):
        # Solo A al principio; con B y C, la posición sale tras el calentamiento.
        distances = {**exact, "b": -1, "c": -1} if seq < 2 else exact
        sample = make_sample(distances, seq=seq, t_host_ms=1000 + seq * 100)
        log.write(sample, pipeline.process(sample))
    log.close()
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == f"1000,0,{exact['a']},-1,-1,200,200,200,0,,,"
    assert lines[2] == f"1100,1,{exact['a']},-1,-1,200,200,200,100,,,"
    assert lines[3] == f"1200,2,{exact['a']},{exact['b']},{exact['c']},200,200,200,200,,,"
    assert lines[5] == f"1400,4,{exact['a']},{exact['b']},{exact['c']},200,200,200,400,2.000,2.000,"


def test_session_log_never_overwrites(tmp_path, config):
    sample = make_sample({"a": 2900, "b": 2900, "c": 2900})
    result = Pipeline(config).process(sample)
    paths = []
    for _ in range(3):  # tres sesiones en el mismo segundo
        log = SessionLog(tmp_path)
        log.write(sample, result)
        log.close()
        paths.append(log.path)
    assert len(set(paths)) == 3
    assert all(len(p.read_text(encoding="utf-8").splitlines()) == 2 for p in paths)


def test_session_log_creates_no_file_without_samples(tmp_path):
    log = SessionLog(tmp_path / "logs")
    log.close()
    assert not (tmp_path / "logs").exists()
