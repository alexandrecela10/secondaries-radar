"""Fingerprint matching: labelled lines (target 90%+), the worked example, and a noise stress test."""
import json
import random

import pytest

from quicklook import store
from quicklook.match import match_line
from quicklook.models import ParsedLine, Tier
from quicklook.synth.offers import Market
from quicklook.synth.universe import REF_DATE, generate

from .conftest import EVALS


def _matches(env):
    con = env.con()
    q = """SELECT o.code_name, g.* FROM gold_line_matches g JOIN silver_offers o USING (offer_id)"""
    out = {(r["code_name"], r["line_no"]): r for r in store.rows(con, q)}
    con.close()
    return out


def test_eval_match_accuracy_on_labelled_lines(env):
    got = _matches(env)
    ok, n, misses = 0, 0, []
    for o in env.labels["offers"]:
        for lab in o["lines"]:
            n += 1
            g = got.get((o["code"], lab["line_no"]))
            good = (g is not None and g["tier"] == lab["expected_tier"] and g["resolved"] == lab["expected_resolved"]
                    and (g["tier"] != "same_fund" or g["fund_id"] == lab["true_fund_id"])
                    and (g["tier"] != "same_manager" or g["manager_id"] == lab["expected_manager_id"]))
            ok += good
            if not good:
                misses.append((o["code"], lab["line_no"]))
    EVALS["match_accuracy_labelled"] = {"correct": ok, "total": n, "accuracy": ok / n, "target": 0.9, "misses": misses}
    assert ok / n >= 0.9


def test_no_silent_match(env):
    for g in _matches(env).values():
        assert g["method"], "every match states its method"
        if g["tier"] == "same_fund":
            assert g["confidence"] >= 0.8 and g["compared_series_row"]
        if not g["resolved"]:
            assert 1 <= len(json.loads(g["candidates_json"])) <= 2


def test_worked_example_fund_4_matches_b(env):
    g = _matches(env)[("Cedar", 4)]
    planted_b = next(f["fund_id"] for f in generate(42)["funds"] if f["planted"] == "B")
    assert g["tier"] == "same_fund" and g["fund_id"] == planted_b and g["confidence"] >= 0.95
    cands = json.loads(g["candidates_json"])
    assert cands[0]["fund_id"] == planted_b and cands[1]["score"] < 0.6  # C is clearly not it


def test_fund_9_without_ratios_shows_two_candidates(env):
    g = _matches(env)[("Cedar", 9)]
    assert g["tier"] == "unknown" and not g["resolved"]
    assert len(json.loads(g["candidates_json"])) == 2


def test_one_ratio_is_not_a_fingerprint(env):
    line = ParsedLine(line_no=1, label="Fund 1", strategy="buyout", region="europe", vintage=2016,
                      commitment=20, value=16.1)
    m = match_line(line, "t:L1", env.ref, REF_DATE, __import__("datetime").datetime(2026, 10, 23))
    assert m.tier != Tier.SAME_FUND


def _noisy_line(rng, r, f, noise):
    pc = round(r["pct_called"] * 100) + (rng.choice([-1, 0, 1]) if noise else 0)
    dec = 1 if (noise and rng.random() < 0.3) else 2
    commit = 25.0
    return ParsedLine(line_no=1, label="Fund 1", strategy=f["strategy"], region=f["region"], vintage=f["vintage"],
                      commitment=commit, pct_called=pc / 100, dpi=round(r["dpi"], dec), tvpi=round(r["tvpi"], dec),
                      value=round(commit * r["vpc"], 1))


def test_eval_stress_noise(env):
    """Every fund in the market, presented with broker noise (rounding, +/- 1 point on % called).

    Funds we own must match themselves. Funds we do not own must never match a fund we own.
    """
    import datetime as dt

    market = Market(generate(42))
    rng = random.Random(7)
    known_at = dt.datetime(2026, 10, 23)
    own_ok = own_n = false_match = other_n = 0
    for f in market.funds.values():
        r = market.ratios(f["fund_id"], REF_DATE)
        if not r or r["pct_called"] < 0.05:
            continue
        m = match_line(_noisy_line(rng, r, f, True), "s:L1", env.ref, REF_DATE, known_at)
        if f["status"] == "owned":
            own_n += 1
            own_ok += m.tier == Tier.SAME_FUND and m.fund_id == f["fund_id"]
        else:
            other_n += 1
            false_match += m.tier == Tier.SAME_FUND
    EVALS["match_stress_noise"] = {"owned_funds": own_n, "owned_matched_to_self": own_ok,
                                   "recall": own_ok / own_n, "not_owned_funds": other_n,
                                   "false_same_fund": false_match}
    assert own_ok / own_n >= 0.9
    assert false_match == 0


def test_restatement_after_known_at_is_ignored(env):
    """Point in time: a report restated after T must not change what we knew at T."""
    import datetime as dt

    restated = [(fid, rows) for fid, rows in env.ref.series.items()
                if len({r.as_of_date for r in rows}) < len(rows)]
    assert restated, "the generator restates some reports"
    fid, rows = restated[0]
    by_date = {}
    for r in rows:
        by_date.setdefault(r.as_of_date, []).append(r)
    d, pair = next((d, sorted(p, key=lambda r: r.reported_at)) for d, p in by_date.items() if len(p) > 1)
    first, second = pair
    before = dt.datetime.combine(second.reported_at, dt.time()) - dt.timedelta(days=1)
    after = dt.datetime.combine(second.reported_at, dt.time()) + dt.timedelta(days=1)
    assert env.ref.series_at(fid, d, before).row_id == first.row_id
    assert env.ref.series_at(fid, d, after).row_id == second.row_id
    assert env.ref.series_at(fid, d, dt.datetime.combine(first.reported_at, dt.time()) - dt.timedelta(days=1)) is None
