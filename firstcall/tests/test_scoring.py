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


def test_completed_sale_is_evidence_not_score():
    rb = rulebook()
    assert rb.completed_sales_count is False and rb.version == 2
    done = SignalInput("done", "sale_or_advisor", 0.85, T0 - timedelta(days=3), completed=True)
    open_ = SignalInput("open", "sale_or_advisor", 0.85, T0 - timedelta(days=3))
    assert score([done], T0).S == 0.0 and score([done], T0).verdict == "WATCH"
    assert math.isclose(score([done, open_], T0).S, score([open_], T0).S)


def test_keyword_flags_completed_sales_only():
    from fc.classify.keyword import KeywordClassifier
    k = KeywordClassifier()
    closed = ["NYC Comptroller Lander and Pension Trustees Close Record Private Equity Secondary Sale",
              "NYC Pensions Sell $5B in Private Equity Secondaries to Blackstone",
              "Blackstone acquires $5bn PE portfolio from NYC pension",
              "Blackstone was sole buyer of PSERS' secondaries sale"]
    still_open = ["As philosophies change, Washington State gears up for first secondaries sale",
                  "Strategic Partners emerges as sole buyer in NYC's $5bn-plus secondaries sale",
                  "CalPERS plans to sell $2bn of private equity stakes",
                  "Washington pension system shops large PE portfolio as secondaries surge"]
    for h in closed:
        c = k.classify(h)
        assert c.signal_type == "sale_or_advisor" and c.completed, h
    for h in still_open:
        c = k.classify(h)
        assert c.signal_type == "sale_or_advisor" and not c.completed, h
