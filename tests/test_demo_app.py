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
    rb = ql.load_rulebook("v3")
    r = ql.run(o, rb)
    assert r.verdict.headline == ("LOOK CLOSER: fits our rules, and the price looks right by our own data. "
                                  "High confidence. Still open: 1 fund (9% of value) not identified.")
    assert all(x["source"].startswith("gold_offer_metrics") for x in ql.reasons(r))
    mv = ql.match_view(r, 4)
    assert mv["fund"] == "F003 Halvane Capital Partners III" and round(mv["score"], 2) == 0.98
    assert [g["label"] for g in ql.open_lines(r)] == ["Fund 9"]
    assert {c["fund_id"] for c in ql.candidates_for(r, 9)} == {"F012", "F015"}
    pc = ql.price_check(r)
    assert pc["view"] == "cheap" and [a["line"] for a in pc["adjustments"]] == ["Fund 1", "Fund 4"]
    assert 0.05 < pc["change"] < 0.06 and pc["high"] > pc["ask_mid"]
    after = ql.run(o, rb, {9: "F012"})
    assert after.verdict.data_matched == 1.0
    memo = ql.memo(after, {"kind": "keep"})
    assert memo.startswith("Project Cedar: worth a closer look. I suggest we sign the NDA and ask for the data room.")
    assert "Fund 9 was identified by hand as Ostrova Growth Partners III (layla)" in memo
    assert "score" not in memo.lower() and "rulebook" not in memo.lower()


def test_quicklook_hornbeam_is_too_expensive_by_our_data():
    r = ql.run(ql.load_offer("Hornbeam"), ql.load_rulebook("v3"))
    assert r.verdict.verdict == "PASS" and r.verdict.veto_rule == ql.rulebook.PRICE_VETO
    assert ql.price_check(r)["change"] < -0.1
    assert ql.run(ql.load_offer("Hornbeam"), ql.load_rulebook("v2")).verdict.verdict == "LOOK CLOSER"
    assert ql.memo(r, None).startswith("Project Hornbeam: too expensive by our own data.")


def test_quicklook_rule_edit_flips_verdict():
    base = ql.load_rulebook("v3")
    loose = ql.custom_rulebook(base, base.threshold, {}, veto_gap=0.20)
    assert ql.run(ql.load_offer("Hornbeam"), loose).verdict.verdict == "LOOK CLOSER"
    strict = ql.custom_rulebook(base, base.threshold, {"known_managers": {"value": 0.9}})
    assert ql.run(ql.load_offer("Cedar"), strict).verdict.verdict == "PASS"
    assert ql.custom_rulebook(base, base.threshold, {}).sha == base.sha


def test_quicklook_holdout_by_rulebook():
    v1, v2, v3 = (ql.agreement(ql.load_rulebook(v)) for v in ("v1", "v2", "v3"))
    assert (v1["holdout"]["kept"], v1["holdout"]["total"]) == (13, 14)
    assert (v2["holdout"]["kept"], v2["holdout"]["total"]) == (10, 14)
    assert (v3["holdout"]["kept"], v3["holdout"]["good_passed"]) == (14, 0)
    assert v3["tune"]["good_passed"] == 2  # Myrtle and Poplar: open questions for the team, shown in the app


def test_quicklook_pasted_text():
    o, err = ql.parse_pasted(ql.EXAMPLE_PASTE)
    assert err is None and o.code == "Fern" and len(o.lines) == 3
    assert ql.run(o, ql.load_rulebook("v3")).verdict.verdict in ("PASS", "LOOK CLOSER")
    o, err = ql.parse_pasted("hello, no offer here")
    assert o is None and "offline parser" in err


def test_firstcall_weekly_and_why():
    rb = fcd.custom_rulebook()
    rows = fcd.weekly(rb)
    assert rows and rows[0]["pension"] == "New York City Retirement Systems" and all(r["verdict"] == "WATCH" for r in rows)
    w = fcd.why("New York City Retirement Systems", rb)
    assert w["parts"] and all({"quote", "outlet", "date", "weight", "decay"} <= p.keys() for p in w["parts"])
    assert fcd.timeline("New York City Retirement Systems", rb)["rows"]
    low = fcd.custom_rulebook(threshold=0.3)
    assert fcd.weekly(low)[0]["verdict"] == "PREPARE"


def test_firstcall_snapshot_names_and_links_every_source():
    snap = json.loads(fcd.SNAP.read_text())
    from fc import pensions

    names = {p.name for p in pensions.ALL.values()}
    for s in snap["signals"] + snap["closed_unlisted"]:
        assert len(s["headline"]) <= 300 and len(s["quote"]) <= 300   # a short quote, never the article
        assert s["link"].startswith("https://"), s["id"]
        assert s["pension"] is None or s["pension"] in names
    assert set(snap["pensions"]) <= names


def test_firstcall_sentence_links_its_source():
    rb = fcd.custom_rulebook()
    w = fcd.why("New York City Retirement Systems", rb)
    src = fcd.source(w["parts"][0])
    assert src["link"].startswith("https://") and fcd.source_md(src).startswith("[")
    s = fcd.verdict_sentence(w)
    assert s.startswith("New York City Retirement Systems: keep an eye on it.") and "](https://" in s


def test_firstcall_wording_is_plain():
    rb = fcd.custom_rulebook()
    text = fcd.verdict_sentence(fcd.why("New York City Retirement Systems", rb))
    for word in ("score", "decay", "threshold", "confidence", "rulebook", "tier", "\u2014"):
        assert word not in text.lower(), word
    assert fcd.ago(3) == "3 days ago" and fcd.ago(58) == "8 weeks ago"


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
    for feature in ("quicklook", "firstcall"):
        t = hood.trace(feature)
        assert t and hood.trace_steps(t) and hood.trace_summary(t)["model_calls"] >= 1
    ex = hood.experiment_rows()
    assert ex["firstcall"] and ex["quicklook"]
    assert hood.classifier_in_words()[0].startswith("Claude flagged 23 headlines as signs, and 21 were right")
    raw = hood.LANGFUSE.read_text()
    from fc import pensions

    assert "sk-lf" not in raw and "pk-lf" not in raw and "@" not in raw
    for p in pensions.ALL.values():   # trace exports keep no headline and no pension
        assert p.name not in raw and p.id not in raw.replace("_", " ").split(), p.name


def _render(monkeypatch):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def test_app_renders_headless(monkeypatch):
    t = time.time()
    at = _render(monkeypatch)
    assert len(at.tabs) == 3
    heads = [s.value for s in at.subheader]
    for h in ("09:02. Read the verdict", "08:00. Read the list", "Tracing: a record of every run",
              "Evals: is the model right?"):
        assert h in heads
    assert time.time() - t < 60


def _fc_text(at) -> str:
    tab = at.tabs[1]
    return "\n".join(e.value for part in (tab.markdown, tab.caption, tab.info) for e in part)


def test_first_call_tab_links_every_news_item(monkeypatch):
    text = _fc_text(_render(monkeypatch))
    assert "](https://news.google.com/" in text and "New York City Retirement Systems" in text
    assert "Link hidden" not in text
    assert "Not a statement of any pension's intent." in text
    assert "privateequityinternational.com" in text   # historical cases link too
    assert "\u2014" not in text
