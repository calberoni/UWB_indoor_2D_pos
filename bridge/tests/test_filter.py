import random

import pytest

from uwb_bridge.filters import FilterOutput, MedianEmaFilter
from uwb_bridge.pipeline import Pipeline
from helpers import make_sample

GOOD = 200


def feed(flt, values, quality=GOOD):
    return [flt.update(v, quality) for v in values]


def test_no_value_before_first_valid_sample():
    flt = MedianEmaFilter()
    assert flt.update(None, GOOD) == FilterOutput(None, accepted=False)
    assert flt.update(2.0, 10) == FilterOutput(None, accepted=False)


def test_value_appears_after_the_warmup_samples():
    flt = MedianEmaFilter(warmup_samples=3)
    outputs = feed(flt, [2.5, 2.6, 2.4, 2.5])
    assert [o.accepted for o in outputs] == [True] * 4
    assert [o.value_m for o in outputs[:2]] == [None, None]
    # La primera salida es la mediana de las tres, sin media exponencial detrás.
    assert outputs[2].value_m == pytest.approx(2.5)


def test_outlier_among_the_first_samples_does_not_leak():
    flt = MedianEmaFilter()
    outputs = feed(flt, [6.0, 2.5, 2.5, 2.5])
    assert outputs[2].value_m == pytest.approx(2.5)
    assert outputs[3].value_m == pytest.approx(2.5)


def test_warmup_with_a_single_sample_window():
    assert MedianEmaFilter(median_window=1).update(2.5, GOOD) == FilterOutput(2.5, accepted=True)


def test_single_spike_is_rejected():
    flt = MedianEmaFilter()
    feed(flt, [2.0] * 10)
    spike = flt.update(5.0, GOOD)
    assert spike.accepted is False
    assert spike.value_m == pytest.approx(2.0)
    after = flt.update(2.0, GOOD)
    assert after.accepted is True
    assert after.value_m == pytest.approx(2.0)


def test_failed_ranging_holds_last_value():
    flt = MedianEmaFilter()
    feed(flt, [2.0] * 5)
    out = flt.update(None, 0)
    assert out == FilterOutput(pytest.approx(2.0), accepted=False)


def test_negative_distance_counts_as_failure():
    flt = MedianEmaFilter()
    feed(flt, [2.0] * 5)
    assert flt.update(-0.001, GOOD).accepted is False


def test_quality_threshold():
    flt = MedianEmaFilter(min_quality=40)
    feed(flt, [2.0] * 5)
    assert flt.update(2.4, 39).accepted is False
    assert flt.update(2.0, 40).accepted is True


def test_outlier_below_jump_limit_is_absorbed_by_median():
    flt = MedianEmaFilter()
    feed(flt, [2.0] * 5)
    out = flt.update(3.2, GOOD)  # salto de 1.2 m: pasa el corte, pero es uno entre cinco
    assert out.accepted is True
    assert out.value_m == pytest.approx(2.0)


def test_ema_weights_new_sample_by_alpha():
    flt = MedianEmaFilter(median_window=1, ema_alpha=0.4)
    outputs = [o.value_m for o in feed(flt, [0.0, 1.0, 1.0, 1.0])]
    assert outputs == pytest.approx([0.0, 0.4, 0.64, 0.784])


def test_median_uses_last_five_valid_samples():
    flt = MedianEmaFilter(median_window=5, ema_alpha=1.0)  # α = 1: la salida es la mediana
    outputs = [o.value_m for o in feed(flt, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6])]
    assert outputs[4] == pytest.approx(1.2)  # mediana de 1.0 … 1.4
    assert outputs[6] == pytest.approx(1.4)  # mediana de 1.2 … 1.6
    # Un fallo no ocupa sitio en la ventana.
    assert flt.update(None, 0).value_m == pytest.approx(1.4)
    assert flt.update(1.7, GOOD).value_m == pytest.approx(1.5)


def test_real_movement_is_accepted_after_five_jump_rejections():
    flt = MedianEmaFilter(max_jump_m=1.5, max_jump_rejects=5)
    feed(flt, [2.0] * 10)
    outputs = feed(flt, [5.0] * 8)

    assert [o.accepted for o in outputs] == [False] * 5 + [True] * 3
    assert all(o.value_m == pytest.approx(2.0) for o in outputs[:5])
    # Al aceptar se reinicia: la salida salta al valor nuevo sin arrastrar el viejo.
    assert all(o.value_m == pytest.approx(5.0) for o in outputs[5:])


def test_restart_does_not_depend_on_a_single_sample():
    flt = MedianEmaFilter()
    feed(flt, [2.0] * 10)
    assert all(not o.accepted for o in feed(flt, [5.0] * 5))
    # La muestra que dispara el reinicio es un outlier: mandan las cinco anteriores.
    out = flt.update(9.0, GOOD)
    assert out.accepted is True
    assert out.value_m == pytest.approx(5.0)
    assert flt.update(5.0, GOOD) == FilterOutput(pytest.approx(5.0), accepted=True)


def test_accepted_outlier_does_not_become_the_reference():
    flt = MedianEmaFilter(max_jump_m=1.5)
    feed(flt, [2.0] * 5)
    assert flt.update(3.45, GOOD).accepted is True  # salto de 1.45 m: entra
    # 1.94 está a 1.51 m del outlier, pero a 6 cm de donde está el tag.
    out = flt.update(1.94, GOOD)
    assert out.accepted is True
    assert out.value_m == pytest.approx(2.0, abs=0.01)


def test_staircase_of_outliers_is_not_followed():
    flt = MedianEmaFilter(max_jump_m=1.5)
    feed(flt, [1.19] * 5)
    # Cada outlier está a menos de 1.5 m del anterior, pero no del tag.
    accepted = [o.accepted for o in feed(flt, [2.36, 3.33, 1.19, 4.23, 1.19, 1.19])]
    assert accepted == [True, False, True, False, True, True]
    assert flt.update(1.19, GOOD).value_m == pytest.approx(1.19)


def test_filter_does_not_stay_locked_with_noisy_data_after_a_move():
    rng = random.Random(7)
    flt = MedianEmaFilter()
    feed(flt, [2.0 + rng.gauss(0, 0.03) for _ in range(30)])
    outputs = feed(flt, [6.0 + rng.gauss(0, 0.03) for _ in range(30)])
    assert outputs[-1].accepted
    assert outputs[-1].value_m == pytest.approx(6.0, abs=0.05)
    assert sum(not o.accepted for o in outputs) == 5


def test_isolated_outliers_never_reach_the_reset():
    flt = MedianEmaFilter()
    feed(flt, [2.0] * 5)
    for _ in range(20):
        # Cuatro saltos seguidos y una muestra buena: la cuenta vuelve a cero.
        assert all(not o.accepted for o in feed(flt, [6.0] * 4))
        good = flt.update(2.0, GOOD)
        assert good.accepted
        assert good.value_m == pytest.approx(2.0)


def test_failures_between_jumps_do_not_restart_the_count():
    flt = MedianEmaFilter()
    feed(flt, [2.0] * 5)
    sequence = [5.0, None, 5.0, 5.0, None, 5.0, 5.0, 5.0]
    outputs = feed(flt, sequence)
    assert [o.accepted for o in outputs] == [False] * 7 + [True]
    assert outputs[-1].value_m == pytest.approx(5.0)


def test_noise_is_reduced():
    rng = random.Random(3)
    flt = MedianEmaFilter()
    outputs = [o.value_m for o in feed(flt, [3.0 + rng.gauss(0, 0.03) for _ in range(500)])]
    settled = outputs[20:]
    mean = sum(settled) / len(settled)
    sigma = (sum((v - mean) ** 2 for v in settled) / len(settled)) ** 0.5
    assert mean == pytest.approx(3.0, abs=0.005)
    assert sigma < 0.02


def test_reset_forgets_everything():
    flt = MedianEmaFilter()
    feed(flt, [2.0] * 5)
    flt.reset()
    assert flt.update(None, 0).value_m is None
    assert [o.value_m for o in feed(flt, [7.0] * 3)] == [None, None, 7.0]


def test_pipeline_accepts_another_filter_class(config):
    class PassThrough:
        def __init__(self, params):
            self.value = None

        def update(self, distance_m, quality):
            self.value = distance_m
            return FilterOutput(distance_m, accepted=distance_m is not None)

        def reset(self):
            self.value = None

    pipeline = Pipeline(config, filter_factory=PassThrough)
    sample = make_sample({"a": 2500, "b": 3500, "c": 3000}, qualities={"a": 1, "b": 1, "c": 1})
    message = pipeline.process(sample).message
    assert [message["ranges"][i]["d"] for i in "abc"] == [2.5, 3.5, 3.0]
    assert all(message["ranges"][i]["ok"] for i in "abc")
