"""First Call data functions for the demo. No Streamlit here, so tests can call every one.

Scores come from `fc.scoring`, the production code, over a committed snapshot of one real
batch run (anonymised) and over the two worked historical cases (real names: both sales were
publicly reported). Your weights and threshold re-score everything instantly.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

from fc import pensions
from fc.classify.keyword import KeywordClassifier
from fc.config import Rulebook, load_rulebook
from fc.scoring import SignalInput, decay, score

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "demo" / "data" / "firstcall_snapshot.json"
FC = ROOT / "firstcall"
SIGNAL_WORDS = {
    "sale_or_advisor": "Sale or sale adviser",
    "allocation_above_target": "Private equity above target",
    "liquidity_need": "Needs cash",
    "policy_review": "Reviewing or cutting private equity",
    "leadership_change": "New or departing CIO",
    "none": "No signal",
}
CASE_LINKS = {
    "wsib_2022": "https://www.privateequityinternational.com/as-philosophies-change-washington-state-gears-up-for-first-secondaries-sale/",
    "nyc_2025": "https://comptroller.nyc.gov/newsroom/nyc-comptroller-lander-and-pension-trustees-close-record-private-equity-secondary-sale",
}


@lru_cache(maxsize=1)
def base_rulebook() -> Rulebook:
    return load_rulebook(FC / "config" / "rulebook.yaml")


def custom_rulebook(weights: dict[str, float] | None = None, threshold: float | None = None) -> Rulebook:
    rb = base_rulebook()
    return dataclasses.replace(rb, weights={**rb.weights, **(weights or {})},
                               prepare_threshold=rb.prepare_threshold if threshold is None else threshold)


def is_default(rb: Rulebook) -> bool:
    b = base_rulebook()
    return rb.weights == b.weights and rb.prepare_threshold == b.prepare_threshold


# ------------------------------------------------------------------ weekly list (snapshot)

@lru_cache(maxsize=1)
def snapshot() -> dict:
    snap = json.loads(SNAP.read_text())
    for i, s in enumerate(snap["signals"]):
        s["signal_id"] = f"s{i:03d}"
        s["published"] = datetime.fromisoformat(s["published_at"])
    for s in snap["closed_unlisted"]:
        s["published"] = datetime.fromisoformat(s["published_at"])
    return snap


def as_of() -> datetime:
    return datetime.fromisoformat(snapshot()["as_of"])


def signals_of(pension: str) -> list[dict]:
    return [s for s in snapshot()["signals"] if s["pension"] == pension]


def _inputs(sigs: list[dict]) -> list[SignalInput]:
    return [SignalInput(s["signal_id"], s["signal_type"], s["confidence"], s["published"], s["completed"]) for s in sigs]


def weekly(rb: Rulebook, when: datetime | None = None) -> list[dict]:
    """The Monday list: PREPARE first, then WATCH, by score."""
    when = when or as_of()
    out = []
    for p in snapshot()["pensions"]:
        sigs = signals_of(p)
        sc = score(_inputs(sigs), when, rb)
        if not sc.contributions:
            continue
        top = next(s for s in sigs if s["signal_id"] == sc.contributions[0].signal_id)
        out.append({"pension": p, "verdict": sc.verdict, "score": round(sc.score, 3), "signals": len(sc.contributions),
                    "top_quote": top["quote"], "top_type": SIGNAL_WORDS[top["signal_type"]], "outlet": top["outlet"],
                    "date": top["published"].date().isoformat()})
    out.sort(key=lambda r: (r["verdict"] != "PREPARE", -r["score"], r["pension"]))
    return out


def closed_sales(days: int = 90) -> list[dict]:
    """Closed-sale stories in the last `days`: evidence only, never scored (rulebook v2)."""
    t = as_of()
    allc = [s for s in snapshot()["signals"] + snapshot()["closed_unlisted"] if s["completed"]]
    return sorted([s for s in allc if t - timedelta(days=days) < s["published"] <= t],
                  key=lambda s: s["published"], reverse=True)


def why(pension: str, rb: Rulebook, when: datetime | None = None) -> dict:
    """Each counted signal: weight x confidence x decay, with its quote, outlet and date."""
    when = when or as_of()
    sigs = signals_of(pension)
    sc = score(_inputs(sigs), when, rb)
    by_id = {s["signal_id"]: s for s in sigs}
    parts = []
    for c in sc.contributions:
        s = by_id[c.signal_id]
        parts.append({"type": SIGNAL_WORDS[c.signal_type], "signal_type": c.signal_type, "weight": c.weight,
                      "confidence": c.confidence, "age_days": round(c.age_days), "decay": round(c.decay, 2),
                      "value": round(c.value, 3), "quote": s["quote"], "headline": s["headline"],
                      "outlet": s["outlet"], "date": s["published"].date().isoformat(), "copies": s["n_copies"]})
    counted = {c.signal_id for c in sc.contributions}
    closed = [s for s in sigs if s["completed"] and s["published"] <= when
              and (when - s["published"]).days <= rb.window_days]
    outside = [s for s in sigs if s["signal_id"] not in counted and s not in closed]
    return {"pension": pension, "verdict": sc.verdict, "score": sc.score, "S": sc.S, "parts": parts,
            "closed": closed, "outside": len(outside), "threshold": rb.prepare_threshold,
            "half_life": rb.half_life_days, "window": rb.window_days}


def _eod(d: date) -> datetime:
    return datetime.combine(d, time(23, 59), timezone.utc)


def timeline_rows(sig_inputs: list[SignalInput], end: date, weeks: int, rb: Rulebook) -> list[dict]:
    starts = [end - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]
    return [{"week": d.isoformat(), "score": round(score(sig_inputs, _eod(d), rb).score, 3)} for d in starts]


def timeline(pension: str, rb: Rulebook, weeks: int = 26) -> dict:
    sigs = signals_of(pension)
    end = as_of().date()
    rows = timeline_rows(_inputs(sigs), end, weeks, rb)
    start = date.fromisoformat(rows[0]["week"]) - timedelta(days=7)
    marks = [{"date": s["published"].date().isoformat(), "type": SIGNAL_WORDS[s["signal_type"]],
              "closed": s["completed"], "quote": s["quote"], "outlet": s["outlet"]}
             for s in sigs if start < s["published"].date() <= end]
    return {"rows": rows, "signals": marks, "threshold": rb.prepare_threshold}


# ------------------------------------------------------------------ worked cases (real names)

@lru_cache(maxsize=2)
def cases(classifier: str = "keyword") -> list[dict]:
    f = "worked_cases.json" if classifier == "keyword" else "worked_cases_claude.json"
    out = json.loads((FC / "measurements" / f).read_text())
    meta = {c["id"]: c for c in yaml.safe_load((FC / "config" / "cases.yaml").read_text())["cases"]}
    for c in out:
        m = meta[c["case"]]
        c["name"] = pensions.ALL[c["pension"]].name
        c["start"], c["end"] = str(m["start"]), str(m["end"])
        c["ground_truth"] = m["first_sale_report"]
    return out


def case_replay(case: dict, rb: Rulebook) -> dict:
    """Point-in-time replay of a worked case under your rulebook: weekly score, first PREPARE, weeks of warning."""
    sig_inputs = [SignalInput(f"c{i}", s["type"], s["confidence"],
                              datetime.combine(date.fromisoformat(s["date"]), time(12), timezone.utc), s["completed"])
                  for i, s in enumerate(case["signals"])]
    sale_day = date.fromisoformat(case["first_sale_report"])
    d, end = date.fromisoformat(case["start"]), date.fromisoformat(case["end"])
    first_prepare, weekly_rows = None, []
    while d <= end:
        sc = score(sig_inputs, _eod(d), rb)
        if sc.verdict == "PREPARE" and first_prepare is None:
            first_prepare = d
        if d.weekday() == 0:
            weekly_rows.append({"week": d.isoformat(), "score": round(sc.score, 3)})
        d += timedelta(days=1)
    warn = (sale_day - first_prepare).days / 7 if first_prepare and first_prepare < sale_day else 0.0
    return {"rows": weekly_rows, "first_prepare": first_prepare.isoformat() if first_prepare else None,
            "weeks_of_warning": round(warn, 1), "sale_day": sale_day.isoformat()}


# ------------------------------------------------------------------ classify a headline

def classify(headline: str, rb: Rulebook) -> dict:
    """The offline keyword classifier and the linker, on your headline. Code only; no model."""
    headline = (headline or "").strip()[:400]
    c = KeywordClassifier().classify(headline)
    ln = pensions.link(headline)
    w = 0.0 if (c.completed and not rb.completed_sales_count) else rb.weights.get(c.signal_type, 0.0)
    value = w * c.confidence * decay(0, rb.half_life_days)
    alone = 1 - __import__("math").exp(-value)
    return {"signal_type": c.signal_type, "type": SIGNAL_WORDS[c.signal_type], "confidence": c.confidence,
            "completed": c.completed, "rationale": c.rationale, "quote": c.quote,
            "pension": pensions.ALL[ln.pension_id].name if ln.pension_id else None, "link_reason": ln.reason,
            "weight": w, "value": round(value, 3), "score_alone": round(alone, 3),
            "verdict_alone": "PREPARE" if alone >= rb.prepare_threshold - 1e-12 else "WATCH",
            "classifier": f"keyword {KeywordClassifier.version}"}


# ------------------------------------------------------------------ evals

def classifier_evals() -> list[dict]:
    rows = []
    for f, name in (("eval_keyword.json", "Keyword rules kw-3 (offline)"), ("eval_claude.json", "Claude fc-classify-v3")):
        d = json.loads((FC / "measurements" / f).read_text())
        s = d["splits"]
        rows.append({"classifier": name, "dev precision": round(s["dev"]["precision"], 2),
                     "dev recall": round(s["dev"]["recall"], 2), "holdout precision": round(s["holdout"]["precision"], 2),
                     "holdout recall": round(s["holdout"]["recall"], 2),
                     "link accuracy": round(s["holdout"]["link_accuracy"], 2), "run": d["run_at"]})
    return rows
