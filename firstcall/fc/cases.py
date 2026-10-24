"""Worked cases (PRD F-5): replay a frozen corpus point in time and print the timeline.

The pipeline is the production one. The score on day T uses only items published on or before T.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import yaml

from . import pipeline, store
from .classify.base import Classifier
from .config import CONFIG_DIR, FIXTURES_DIR, rulebook
from .models import RawItem
from .scoring import SignalInput, score
from .sources import google_news

CORPUS = FIXTURES_DIR / "frozen_corpus"


def load_cases() -> list[dict]:
    return yaml.safe_load((CONFIG_DIR / "cases.yaml").read_text())["cases"]


def corpus(case: dict, refresh: bool = False) -> list[RawItem]:
    """Freeze the RSS results once, then always read the frozen file (reproducible replays)."""
    path = CORPUS / f"{case['id']}.jsonl"
    if refresh or not path.exists():
        CORPUS.mkdir(parents=True, exist_ok=True)
        items: dict[str, RawItem] = {}
        for q in case["queries"]:
            _, got = google_news.fetch(q)
            for r in got:
                items.setdefault(r.link, r)
        path.write_text("".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in items.values()))
    rows = [RawItem.model_validate_json(l) for l in path.read_text().splitlines() if l.strip()]
    frozen_at = datetime.now(timezone.utc)
    for m in case.get("manual_items", []):
        rows.append(RawItem(source="frozen", query="manual", title=m["headline"], link=m["link"], outlet=m["outlet"],
                            published=str(m["published"]), fetched_at=frozen_at))
    return rows


def replay(case: dict, classifier: Classifier, refresh: bool = False) -> dict:
    rb = rulebook()
    con = store.connect(":memory:")
    end = datetime.combine(case["end"], time(23, 59), timezone.utc)
    pipeline.process_many(con, corpus(case, refresh), classifier, end, path="replay")
    sigs = store.load_signals(con, case["pension"])
    inputs = [SignalInput(s["signal_id"], s["signal_type"], s["confidence"], s["published_at"]) for s in sigs]
    sale_day: date = case["first_sale_report"]["date"]

    timeline, first_prepare, peak = [], None, (0.0, None)
    d = case["start"]
    while d <= case["end"]:
        sc = score(inputs, datetime.combine(d, time(23, 59), timezone.utc), rb)
        if sc.score > peak[0] and d < sale_day:
            peak = (sc.score, d)
        if sc.verdict == "PREPARE" and first_prepare is None:
            first_prepare = d
        if d.weekday() == 0:
            timeline.append((d, sc.score, sc.verdict))
        d += timedelta(days=1)

    pre_sale = [s for s in sigs if s["published_at"].date() < sale_day]
    first_non_sale = next((s for s in pre_sale if s["signal_type"] != "sale_or_advisor"), None)
    detected_sale = next((s for s in sigs if s["signal_type"] == "sale_or_advisor"), None)
    warning_weeks = (sale_day - first_prepare).days / 7 if first_prepare and first_prepare < sale_day else 0.0
    return {
        "case": case["id"], "pension": case["pension"], "items_in_corpus": con.execute("SELECT count(*) FROM silver_items").fetchone()[0],
        "items_linked": con.execute("SELECT count(*) FROM silver_items WHERE pension_id = ?", [case["pension"]]).fetchone()[0],
        "signals": [{"date": str(s["published_at"].date()), "type": s["signal_type"], "confidence": s["confidence"],
                     "headline": s["headline"], "outlet": s["outlet"], "link": s["link"]} for s in sigs],
        "first_non_sale_signal": str(first_non_sale["published_at"].date()) if first_non_sale else None,
        "first_prepare": str(first_prepare) if first_prepare else None,
        "first_sale_report": str(sale_day),
        "first_sale_signal_detected": str(detected_sale["published_at"].date()) if detected_sale else None,
        "peak_score_before_sale": round(peak[0], 3), "peak_date": str(peak[1]) if peak[1] else None,
        "weeks_of_warning": round(warning_weeks, 1),
        "weekly": [(str(d), round(s, 3), v) for d, s, v in timeline],
    }


def render(r: dict) -> str:
    lines = [f"CASE {r['case']} ({r['pension']})   illustration, n = 2, not a statistic",
             f"corpus: {r['items_in_corpus']} items, {r['items_linked']} linked to {r['pension']}",
             "signals found:"]
    for s in r["signals"]:
        lines.append(f"  {s['date']}  {s['type']:<24} {s['confidence']:.2f}  \"{s['headline'][:80]}\" ({s['outlet']})")
    lines += [
        f"first non-sale signal : {r['first_non_sale_signal'] or 'none before the sale report'}",
        f"first PREPARE         : {r['first_prepare'] or 'never'}",
        f"first sale report     : {r['first_sale_report']} (ground truth, set by hand)",
        f"first sale signal seen: {r['first_sale_signal_detected'] or 'missed'}",
        f"peak score before sale: {r['peak_score_before_sale']} on {r['peak_date']}",
        f"weeks of warning      : {r['weeks_of_warning']}",
    ]
    return "\n".join(lines)
