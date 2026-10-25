"""Rebuild the demo snapshot the Streamlit app reads. Run from the repo root:

    .venv/bin/python demo/build_snapshot.py [--firstcall-db firstcall/data/firstcall.duckdb] [--as-of 2026-10-24]

Quick Look: the synthetic universe, the 30 offer emails and their labels (seed 42), copied from a
fresh `synth.run`. Generating takes about 10 seconds, so the app reads this copy instead.

First Call: one real batch run, exported from a local First Call database and anonymised.
Pensions become "Pension A", "Pension B" in score order, and every pension name in a headline
or quote becomes "[Pension]". Only headline, outlet, date and a quote of 300 characters or less
are kept. Links are dropped from this public file: an article URL often names the pension.

A second, git-ignored file, demo/data/firstcall_sources.local.json, keeps the real pension name,
headline, quote and link for each signal id. The app shows it only when FIRSTCALL_SHOW_SOURCES=1
(or the Streamlit secret of that name) is set, so a local run can open every source.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, time, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "quicklook" / "src"), str(ROOT / "firstcall")]
os.environ.setdefault("FC_NO_DOTENV", "1")
OUT = ROOT / "demo" / "data"
QUOTE_MAX = 300


import re

# A person's name or a fund's size can name the pension as surely as its name does.
_PERSON_AFTER = re.compile(r"\b((?i:names?|named|appoints?|appointed|taps?|hires?))\s+((?:[A-Z][\w.'’-]+\s+){1,5})(as)\b")
_PERSON_BEFORE = re.compile(r"^((?:[A-Z][\w.'’-]+\s+){1,2}[A-Z][\w'’-]+)\s+(Named|Appointed|Tapped)\b")
_AMOUNT = re.compile(r"\$\s?\d[\d.,]*\s?(?:bn|billion|b|m|mn|million)?\b", re.IGNORECASE)
_SUFFIX = re.compile(r"\[Pension\]\s?/\s?[A-Z]{2,}\b")


def redact(text: str) -> str:
    text = _PERSON_AFTER.sub(lambda m: f"{m[1]} [person] {m[3]}", text)
    text = _PERSON_BEFORE.sub(lambda m: f"[person] {m[2]}", text)
    text = _SUFFIX.sub("[Pension]", text)
    return _AMOUNT.sub("$[amount]", text)


def build_quicklook() -> None:
    from quicklook import synth

    dest = OUT / "quicklook"
    if dest.exists():
        shutil.rmtree(dest)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        synth.run(tmp, seed=42)
        shutil.copytree(tmp / "reference", dest / "reference")
        (dest / "offers").mkdir(parents=True)
        for f in sorted((tmp / "offers").glob("*.eml")) + sorted((tmp / "offers" / "more").glob("*.eml")):
            shutil.copy(f, dest / "offers" / f.name)
        shutil.copy(tmp / "fixtures" / "labels.json", dest / "labels.json")
    print(f"Quick Look snapshot: {len(list((dest / 'offers').glob('*.eml')))} emails, reference and labels in {dest}")


def build_firstcall(db: Path, as_of_day: str) -> None:
    import duckdb

    from fc import pensions
    from fc.config import rulebook
    from fc.scoring import inputs, score

    as_of = datetime.combine(datetime.fromisoformat(as_of_day).date(), time(23, 59), timezone.utc)
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    cur = con.execute("SELECT * FROM gold_signals WHERE published_at <= ? ORDER BY published_at", [as_of])
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    classifiers = sorted(r[0] for r in con.execute("SELECT DISTINCT classifier FROM classified_items").fetchall())
    con.close()

    by_pension: dict[str, list[dict]] = {}
    for r in rows:
        by_pension.setdefault(r["pension_id"], []).append(r)
    ranked = []
    for pid, sigs in by_pension.items():
        sc = score(inputs(sigs), as_of)
        if sc.contributions:
            ranked.append((sc.score, pid))
    ranked.sort(key=lambda t: (-t[0], t[1]))
    labels = {pid: f"Pension {chr(65 + i)}" if i < 26 else f"Pension {chr(65 + i // 26 - 1)}{chr(65 + i % 26)}"
              for i, (_, pid) in enumerate(ranked)}

    def clean(text: str | None) -> str:
        return redact(pensions.mask(text or ""))[:QUOTE_MAX]

    signals, closed, sources = [], [], {}
    for r in rows:
        in_list = r["pension_id"] in labels
        if not in_list and not r["completed"]:
            continue
        sid = f"s{len(signals):03d}" if in_list else f"c{len(closed):03d}"
        sources[sid] = {"pension": pensions.ALL[r["pension_id"]].name if r["pension_id"] in pensions.ALL else None,
                        "headline": r["headline"], "quote": (r["quote"] or "")[:QUOTE_MAX], "link": r["link"]}
        item = {"id": sid, "pension": labels.get(r["pension_id"]), "signal_type": r["signal_type"],
                "confidence": round(float(r["confidence"]), 3), "completed": bool(r["completed"]),
                "published_at": r["published_at"].astimezone(timezone.utc).isoformat(),
                "outlet": r["outlet"], "headline": clean(r["headline"]), "quote": clean(r["quote"]),
                "n_copies": int(r["n_copies"] or 1)}
        if in_list:
            signals.append(item)
        else:
            closed.append(dict(item, pension=None))
    snap = {"as_of": as_of.isoformat(), "rulebook_version": rulebook().version, "classifiers": classifiers,
            "pensions": [labels[pid] for _, pid in ranked], "signals": signals, "closed_unlisted": closed,
            "note": "Real public headlines, anonymised. Links withheld because they name the pension. Not a statement of any pension's intent."}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "firstcall_snapshot.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n")
    local = {"as_of": as_of.isoformat(), "note": "Local only, git-ignored. Real names and links. Never publish.",
             "pensions": {labels[pid]: pensions.ALL[pid].name for _, pid in ranked}, "sources": sources}
    (OUT / "firstcall_sources.local.json").write_text(json.dumps(local, indent=1, ensure_ascii=False) + "\n")
    print(f"First Call snapshot: {len(labels)} pensions, {len(signals)} signals as of {as_of.date()} "
          f"in {OUT / 'firstcall_snapshot.json'}; real names and links in {OUT / 'firstcall_sources.local.json'} "
          "(git-ignored)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--firstcall-db", type=Path, default=ROOT / "firstcall" / "data" / "firstcall.duckdb")
    ap.add_argument("--as-of", default="2026-10-24")
    ap.add_argument("--skip-quicklook", action="store_true")
    a = ap.parse_args()
    if not a.skip_quicklook:
        build_quicklook()
    if a.firstcall_db.exists():
        build_firstcall(a.firstcall_db, a.as_of)
    else:
        print(f"No First Call database at {a.firstcall_db}; kept the committed First Call snapshot. "
              "Run `cd firstcall && make collect-batch` first to rebuild it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
