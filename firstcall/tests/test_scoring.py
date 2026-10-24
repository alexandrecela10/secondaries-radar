import math
from datetime import timedelta

from fc.config import rulebook
from fc.scoring import SignalInput, decay, score, verdict
from tests.conftest import T0


def sig(i, t, conf, days_ago):
    return SignalInput(f"s{i}", t, conf, T0 - timedelta(days=days_ago))


def test_decay_halves_every_90_days():
    assert decay(0, 90) == 1.0
    assert math.isclose(decay(90, 90), 0.5)
    assert math.isclose(decay(180, 90), 0.25)


def test_score_formula_matches_prd():
    s = score([sig(1, "sale_or_advisor", 0.85, 0), sig(2, "allocation_above_target", 0.8, 90)], T0)
    S = 1.0 * 0.85 * 1.0 + 0.6 * 0.8 * 0.5
    assert math.isclose(s.S, S)
    assert math.isclose(s.score, 1 - math.exp(-S))
    assert s.verdict == "PREPARE"


def test_one_sale_report_alone_stays_on_watch():
    # 1 - exp(-0.85) = 0.573 < 0.6. Flagged to the PM as a rulebook question.
    s = score([sig(1, "sale_or_advisor", 0.85, 0)], T0)
    assert round(s.score, 3) == 0.573 and s.verdict == "WATCH"


def test_point_in_time_ignores_future_signals():
    future = SignalInput("f", "sale_or_advisor", 0.9, T0 + timedelta(days=1))
    assert score([future], T0).S == 0.0


def test_window_drops_old_signals():
    rb = rulebook()
    assert score([sig(1, "sale_or_advisor", 0.9, rb.window_days + 1)], T0).contributions == ()


def test_threshold_boundary():
    assert verdict(0.6) == "PREPARE"
    assert verdict(0.5999) == "WATCH"


def test_contributions_sum_to_S():
    s = score([sig(i, t, 0.7, i * 10) for i, t in enumerate(rulebook().weights)], T0)
    assert math.isclose(sum(c.value for c in s.contributions), s.S)
