"""Metric fixtures with hand-computed answers. Target: 100% correct (C-3)."""
import datetime as dt

import pytest

from quicklook import metrics as M
from quicklook.match import fund_ratios, ratio_score
from quicklook.models import Tier

from .conftest import EVALS

PLAN = {"weights": {"strategy": 0.5, "region": 0.3, "vintage_band": 0.2},
        "targets": {"strategy": {"buyout": 0.5, "venture": 0.5}, "region": {"europe": 1.0},
                    "vintage_band": {"2013-2017": 1.0}}}


def L(i, usd, strategy="buyout", region="europe", vintage=2016, tier=Tier.SAME_FUND, resolved=True, lo=None, hi=None):
    return M.LineFact(f"x:L{i}", usd, strategy, region, vintage, tier, resolved, None, lo, hi)


LINES = [L(1, 50, lo=0.80, hi=0.90), L(2, 30, "venture", tier=Tier.SAME_MANAGER),
         L(3, 10, "venture", "north_america", 2019, Tier.UNKNOWN, resolved=False),
         L(4, 5, tier=Tier.UNKNOWN), L(5, 3, lo=0.70, hi=0.80), L(6, 2, "credit", tier=Tier.UNKNOWN)]
# total = 100

CASES = [
    ("share same fund", lambda: M.share(LINES, lambda l: l.tier == Tier.SAME_FUND), 0.53),
    ("share known manager", lambda: M.share(LINES, lambda l: l.tier != Tier.UNKNOWN), 0.83),
    ("share venture", lambda: M.share(LINES, lambda l: l.strategy == "venture"), 0.40),
    ("share unresolved", lambda: M.share(LINES, lambda l: not l.resolved), 0.10),
    ("top-5 concentration", lambda: M.top5_concentration(LINES), 0.98),
    ("top-5 of five lines is 1", lambda: M.top5_concentration(LINES[:5]), 1.0),
    ("empty offer has no share", lambda: M.share([], lambda l: True), None),
    # strategy mix: buyout .58, venture .40, credit .02 vs .5/.5: TVD = (.08+.10+.02)/2 = .10 -> .90
    # region: europe .90, na .10 vs europe 1: TVD .10 -> .90; vintage: 2013-17 .90 vs 1 -> .90
    ("fit score", lambda: M.fit_score(LINES, PLAN), 0.90),
    ("one minus TVD identical", lambda: M.one_minus_tvd({"a": 0.3, "b": 0.7}, {"a": 0.3, "b": 0.7}), 1.0),
    ("one minus TVD disjoint", lambda: M.one_minus_tvd({"a": 1.0}, {"b": 1.0}), 0.0),
    # priced lines: (0.80*50 + 0.70*3)/53 and (0.90*50 + 0.80*3)/53
    ("price range low", lambda: M.priced_range(LINES)[0], (0.80 * 50 + 0.70 * 3) / 53),
    ("price range high", lambda: M.priced_range(LINES)[1], (0.90 * 50 + 0.80 * 3) / 53),
    ("no owned funds, no price", lambda: M.priced_range(LINES[1:4])[0], None),
    ("fund age", lambda: M.fund_age(2016, dt.date(2026, 6, 30)), 3833 / 365.25),
    ("vintage band 2012", lambda: M.vintage_band(2012), "2008-2012"),
    ("vintage band 2018", lambda: M.vintage_band(2018), "2018-2024"),
]

ASKING = [("high 80s", (0.87, 0.89)), ("mid 80s", (0.84, 0.86)), ("low 90s", (0.91, 0.93)), ("par", (1.0, 1.0)),
          ("92%", (0.92, 0.92)), ("85-88%", (0.85, 0.88)), ("80s", (0.80, 0.89)), ("best offer", None), (None, None)]


@pytest.mark.parametrize("name,fn,expected", CASES, ids=[c[0] for c in CASES])
def test_metric_fixture(name, fn, expected):
    got = fn()
    if expected is None or isinstance(expected, str):
        assert got == expected
    else:
        assert got == pytest.approx(expected, abs=1e-4)


@pytest.mark.parametrize("text,expected", ASKING)
def test_asking_price(text, expected):
    got = M.asking_price_range(text)
    assert got == (pytest.approx(expected) if expected else None)


def test_line_price_range_buyout_age_10_5():
    # buyout, 10.5 years old -> band 72% to 84%; our report says the line is worth 2% more -> x1.02
    lo, hi, method = M.line_price_range("buyout", 2016, dt.date(2026, 6, 30), 100.0, 102.0)
    assert (lo, hi) == (pytest.approx(0.72 * 1.02), pytest.approx(0.84 * 1.02))
    assert "band 72% to 84%" in method


def test_guidance_minus_range_top_offer_level():
    lines = [L(1, 60, lo=0.8, hi=0.85), L(2, 40, "credit", lo=0.9, hi=0.95)]
    m = M.offer_metrics(lines, "high 80s", PLAN)
    # range top = .85*.6 + .95*.4 = .89 ; asking mid .88 -> -0.01
    assert m["price_range_high"].value == pytest.approx(0.89)
    assert m["guidance_minus_range_top"].value == pytest.approx(-0.01)
    assert all(v.version == "v1" for v in m.values())


def test_offer_metrics_without_guidance_is_not_checked():
    m = M.offer_metrics([L(1, 10, lo=0.8, hi=0.9)], None, PLAN)
    assert m["guidance_minus_range_top"].value is None
    assert m["guidance_minus_range_top"].note


def test_ratio_score_worked_example():
    """README example: line 92% called, DPI 0.84x, TVPI 1.71x vs fund B at 92.4%, 0.842x, 1.712x."""
    fund_b = {"pct_called": 0.924, "dpi": 0.842, "tvpi": 1.712}
    s, d = ratio_score({"pct_called": 0.92, "dpi": 0.84, "tvpi": 1.71}, fund_b)
    # distances: .004/.10 = .04 ; .002/.25 = .008 ; .002/.25 = .008 -> mean .0187 -> .9813
    assert s == pytest.approx(1 - (0.04 + 0.008 + 0.008) / 3)
    fund_c = {"pct_called": 0.881, "dpi": 0.552, "tvpi": 1.448}
    s_c, _ = ratio_score({"pct_called": 0.92, "dpi": 0.84, "tvpi": 1.71}, fund_c)
    assert s_c < 0.6


def test_fund_ratios():
    r = fund_ratios(called=92.4, distributed=77.8, nav=80.4, size=100)
    assert r["pct_called"] == pytest.approx(0.924)
    assert r["dpi"] == pytest.approx(77.8 / 92.4)
    assert r["tvpi"] == pytest.approx(158.2 / 92.4)
    assert r["value_per_commitment"] == pytest.approx(0.804)


def test_eval_metric_fixtures_all_correct():
    ok = 0
    for _, fn, exp in CASES:
        got = fn()
        ok += (got == exp) if (exp is None or isinstance(exp, str)) else (got is not None and abs(got - exp) < 1e-4)
    EVALS["metric_fixtures"] = {"correct": ok, "total": len(CASES), "accuracy": ok / len(CASES), "target": 1.0}
    assert ok == len(CASES)


def test_metrics_do_not_depend_on_hash_seed():
    """Found by the parity check: set order changed float sums across processes."""
    import os
    import subprocess
    import sys

    code = ("from quicklook import metrics as M; print(repr(M.one_minus_tvd("
            "{'buyout':.3891,'growth':.2977,'venture':.15,'credit':.11,'infra':.0532},"
            "{'buyout':.45,'growth':.2,'venture':.05,'credit':.15,'infra':.15})))")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONHASHSEED=str(s))).stdout for s in range(6)}
    assert len(outs) == 1
