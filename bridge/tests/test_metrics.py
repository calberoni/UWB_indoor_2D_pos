import pytest

from uwb_bridge.metrics import LatencyEstimator, LossCounter, RateMeter
from uwb_bridge.pipeline import Pipeline
from uwb_bridge.sample import Sample

# --- Ciclos perdidos --------------------------------------------------------


def count(seqs):
    counter = LossCounter()
    for seq in seqs:
        lost = counter.update(seq)
    return lost


def test_no_loss_in_consecutive_sequence():
    assert count(range(100, 200)) == 0


def test_first_sample_sets_the_baseline():
    assert count([40000]) == 0


def test_gap_counts_missing_cycles():
    assert count([10, 11, 15, 16]) == 3


def test_gaps_accumulate():
    assert count([1, 3, 4, 7, 8, 9, 20]) == 1 + 2 + 10


def test_wraparound_without_loss():
    assert count([65533, 65534, 65535, 0, 1, 2]) == 0


def test_wraparound_with_loss_across_the_boundary():
    assert count([65534, 1]) == 2  # faltan 65535 y 0


def test_wraparound_with_loss_before_and_after():
    assert count([65530, 65533, 2, 3]) == 2 + 4


def test_several_full_turns():
    seqs = [i % 65536 for i in range(65000, 65000 + 3 * 65536, 1)]
    assert count(seqs) == 0


def test_repeated_seq_is_not_a_loss():
    assert count([5, 5, 6]) == 0


def test_late_seq_is_not_a_loss():
    # Un seq atrasado no son 65535 ciclos perdidos.
    assert count([100, 101, 99, 102]) == 0


def test_resync_keeps_the_count():
    counter = LossCounter()
    for seq in (500, 503):
        counter.update(seq)
    counter.resync()
    assert counter.update(0) == 2
    assert counter.update(2) == 3


# --- Tasa -------------------------------------------------------------------


def test_rate_at_steady_10_hz():
    meter = RateMeter()
    rates = [meter.update(1_000_000 + i * 100) for i in range(30)]
    assert rates[0] == 0.0  # con una sola muestra no hay tasa
    assert all(r == pytest.approx(10.0) for r in rates[1:])


def test_rate_with_jitter_stays_near_10_hz():
    meter = RateMeter()
    jitter = [0, 7, -5, 12, 3, -8, 9, 1, -3, 6]
    rates = [meter.update(1_000_000 + i * 100 + jitter[i % 10]) for i in range(100)]
    assert all(9.5 <= r <= 10.5 for r in rates[10:])


def test_rate_drops_when_samples_are_missing():
    meter = RateMeter()
    for i in range(40):
        if i % 2 == 0:  # llega una de cada dos
            rate = meter.update(1_000_000 + i * 100)
    assert rate == pytest.approx(5.0)


def test_rate_only_looks_at_the_last_second():
    meter = RateMeter()
    for i in range(20):
        meter.update(1_000_000 + i * 100)
    # Cinco segundos de silencio y vuelve a 20 Hz.
    rates = [meter.update(1_007_000 + i * 50) for i in range(30)]
    assert rates[0] == 0.0
    assert rates[-1] == pytest.approx(20.0)


# --- Latencia ---------------------------------------------------------------


def test_latency_is_delay_above_best_case():
    estimator = LatencyEstimator()
    offset = 1_790_000_000_000  # el reloj del Mac va muy por delante del tag
    delays = [20, 15, 40, 15, 90]
    got = [estimator.update(offset + i * 100 + d, i * 100) for i, d in enumerate(delays)]
    # La referencia es el menor retraso visto hasta el momento.
    assert got == [0, 0, 25, 0, 75]


def test_latency_window_forgets_after_30_s():
    estimator = LatencyEstimator()
    estimator.update(1_000_000 + 5, 0)  # mejor caso: 5 ms
    assert estimator.update(1_000_100 + 25, 100) == 20
    # 31 s después el mejor caso antiguo ya no cuenta.
    assert estimator.update(1_031_200 + 25, 31_200) == 0


def test_latency_window_keeps_samples_within_30_s():
    estimator = LatencyEstimator()
    estimator.update(1_000_000 + 5, 0)
    assert estimator.update(1_029_000 + 25, 29_000) == 20


def test_sync_offset_formula():
    estimator = LatencyEstimator()
    # SYNC enviado a t0 = 1000, respuesta a t1 = 1010, el tag dijo 400:
    # desfase = (1000 + 1010) / 2 − 400 = 605.
    estimator.add_sync(1000, 1010, 400)
    assert estimator.sync_round_trip_ms == 10
    # Muestra con t_ms = 500 recibida a t_host = 1130: 1130 − (500 + 605) = 25.
    assert estimator.update(1130, 500) == 25


def test_sync_keeps_measurement_with_shortest_round_trip():
    estimator = LatencyEstimator()
    estimator.add_sync(1000, 1040, 400)  # ida y vuelta 40 ms → desfase 620
    estimator.add_sync(11_000, 11_008, 10_400)  # 8 ms → desfase 604
    estimator.add_sync(21_000, 21_030, 20_400)  # 30 ms: peor, se ignora
    assert estimator.sync_round_trip_ms == 8
    assert estimator.update(21_130, 20_500) == 21_130 - (20_500 + 604)


def test_sync_takes_precedence_over_window_minimum():
    estimator = LatencyEstimator()
    assert estimator.update(1130, 500) == 0  # sin SYNC: es el mejor caso visto
    estimator.add_sync(1000, 1010, 400)
    assert estimator.update(1230, 600) == 25


def test_latency_is_never_negative():
    estimator = LatencyEstimator()
    estimator.add_sync(1000, 1040, 400)  # desfase 620, con ±20 ms de error
    assert estimator.update(1510, 900) == 0  # 1510 − 1520 = −10


def test_sync_with_negative_round_trip_is_ignored():
    estimator = LatencyEstimator()
    estimator.add_sync(1010, 1000, 400)
    assert estimator.sync_round_trip_ms is None


# --- Reinicio del tag -------------------------------------------------------


def _sample(seq, t_ms, t_host_ms, d=3000):
    return Sample(t_host_ms=t_host_ms, seq=seq, d_a=d, d_b=d, q_a=200, q_b=200, t_ms=t_ms)


def test_tag_restart_does_not_inflate_lost_or_latency(config):
    pipeline = Pipeline(config)
    host = 1_790_000_000_000
    for i in range(50):
        message = pipeline.process(_sample(40_000 + i, 500_000 + i * 100, host + i * 100 + 15)).message
    assert message["lost"] == 0

    # El tag reinicia: seq y millis() vuelven a empezar, y está en otro sitio.
    host += 8_000
    for i in range(20):
        message = pipeline.process(_sample(i, 1_000 + i * 100, host + i * 100 + 15, d=5500)).message
        assert message["lost"] == 0
        assert message["latency_ms"] == 0
        assert message["valid"] is True
        assert message["d_a"] == pytest.approx(5.5)


def test_tag_restart_discards_sync_offset(config):
    pipeline = Pipeline(config)
    pipeline.add_sync(1_000_000, 1_000_010, 500_000)
    assert pipeline.process(_sample(1, 500_100, 1_000_125)).message["latency_ms"] == 20

    # Tras el reinicio el desfase viejo daría una latencia de minutos.
    assert pipeline.process(_sample(0, 1_000, 1_010_000)).message["latency_ms"] == 0
    pipeline.add_sync(1_010_100, 1_010_104, 1_100)
    assert pipeline.process(_sample(1, 1_200, 1_010_230)).message["latency_ms"] == 28


def test_pipeline_counts_lost_cycles_across_wraparound(config):
    pipeline = Pipeline(config)
    for i, seq in enumerate([65533, 65534, 1, 2]):
        message = pipeline.process(_sample(seq, i * 100, 1_000_000 + i * 100)).message
    assert message["lost"] == 2
