"""Smoke test for the Streamlit demo: the data behind each tab, then the whole app headless.

Run from the repo root: .venv/bin/pytest tests
No key, no network: everything here is offline code over the committed snapshot.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import demo  # noqa: F401
from demo import fcd, hood, ql

ROOT = Path(__file__).resolve().parents[1]


def test_quicklook_cedar_journey():
    o = ql.load_offer("Cedar")
    rb = ql.load_rulebook("v2")
    r = ql.run(o, rb)
    assert r.verdict.headline == "LOOK CLOSER, medium confidence: 1 fund unmatched (9% of value), close to the line."
    assert {x["status"] for x in ql.reasons(r)} == {"met", "not met"}
    assert all(x["source"].startswith("gold_offer_metrics") for x in ql.reasons(r))
    mv = ql.match_view(r, 4)
    assert mv["fund"] == "F003 Halvane Capital Partners III" and round(mv["score"], 2) == 0.98
    assert [g["label"] for g in ql.open_lines(r)] == ["Fund 9"]
    assert {c["fund_id"] for c in ql.candidates_for(r, 9)} == {"F012", "F015"}
    after = ql.run(o, rb, {9: "F012"})
    assert after.verdict.data_matched == 1.0 and after.verdict.band == "medium"
    memo = ql.memo(after, {"kind": "keep"})
    assert "I keep this verdict" in memo and "matched by hand to Ostrova Growth Partners III" in memo


def test_quicklook_rule_edit_flips_verdict():
    base = ql.load_rulebook("v2")
    heavy_price = ql.custom_rulebook(base, base.threshold, {"max_price": {"weight": 5.0}})
    assert ql.run(ql.load_offer("Cedar"), heavy_price).verdict.verdict == "PASS"
    assert ql.custom_rulebook(base, base.threshold, {}).sha == base.sha


def test_quicklook_holdout_shows_overfitting():
    v1, v2 = ql.agreement(ql.load_rulebook("v1")), ql.agreement(ql.load_rulebook("v2"))
    assert (v1["holdout"]["kept"], v1["holdout"]["total"]) == (13, 14)
    assert (v2["holdout"]["kept"], v2["holdout"]["total"]) == (10, 14)
    assert v2["tune"]["kept"] == 14


def test_quicklook_pasted_text():
    o, err = ql.parse_pasted(ql.EXAMPLE_PASTE)
    assert err is None and o.code == "Fern" and len(o.lines) == 3
    assert ql.run(o, ql.load_rulebook("v2")).verdict.verdict in ("PASS", "LOOK CLOSER")
    o, err = ql.parse_pasted("hello, no offer here")
    assert o is None and "offline parser" in err


def test_firstcall_weekly_and_why():
    rb = fcd.custom_rulebook()
    rows = fcd.weekly(rb)
    assert rows and rows[0]["pension"] == "Pension A" and all(r["verdict"] == "WATCH" for r in rows)
    w = fcd.why("Pension A", rb)
    assert w["parts"] and all({"quote", "outlet", "date", "weight", "decay"} <= p.keys() for p in w["parts"])
    assert fcd.timeline("Pension A", rb)["rows"]
    low = fcd.custom_rulebook(threshold=0.3)
    assert fcd.weekly(low)[0]["verdict"] == "PREPARE"


def test_firstcall_snapshot_is_anonymised():
    snap = json.loads(fcd.SNAP.read_text())
    from fc import pensions

    for s in snap["signals"] + snap["closed_unlisted"]:
        for field in ("headline", "quote"):
            assert len(s[field]) <= 300
            assert pensions.mask(s[field]) == s[field], s[field]
        assert "link" not in s
        assert s["pension"] is None or s["pension"].startswith("Pension ")


def test_firstcall_cases_and_classifier():
    rb = fcd.custom_rulebook()
    reps = {c["case"]: fcd.case_replay(c, rb) for c in fcd.cases("keyword")}
    assert reps["wsib_2022"]["weeks_of_warning"] == 0 and reps["nyc_2025"]["weeks_of_warning"] == 0
    c = fcd.classify("CalPERS hires Evercore to run secondary sale of private equity stakes", rb)
    assert c["signal_type"] == "sale_or_advisor" and c["pension"].startswith("California Public")
    assert len(fcd.classifier_evals()) == 2


def test_under_the_hood():
    for _, path in hood.DECISIONS:
        assert hood.measured_table(path).startswith("| # | Criterion")
    assert hood.quicklook_evals() and hood.verdict_evals() and hood.parser_comparison()


def test_app_renders_headless():
    from streamlit.testing.v1 import AppTest

    t = time.time()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.tabs) == 3
    heads = [s.value for s in at.subheader]
    for h in ("09:02. Read the verdict", "08:00. Read the list", "Evals"):
        assert h in heads
    assert time.time() - t < 30
