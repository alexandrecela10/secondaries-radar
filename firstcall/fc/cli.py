"""First Call CLI. Run `python -m fc.cli --help`."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, time, timezone
from pathlib import Path

from .config import DATA_DIR, DEFAULT_DB, REPORTS_DIR, rulebook


def _as_of(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.combine(datetime.fromisoformat(s).date(), time(23, 59), timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="firstcall", description="First Call: public pension news signals")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--classifier", default="auto", choices=["auto", "keyword", "claude"])
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="batch path: fetch every source once and process")
    c.add_argument("--no-gdelt", action="store_true")
    b = sub.add_parser("batch", help="batch path: fetch and process every N minutes")
    b.add_argument("--every", type=float, default=15.0)
    b.add_argument("--no-gdelt", action="store_true")
    p = sub.add_parser("poll", help="stream path: poll sources and publish new items")
    p.add_argument("--every", type=float, default=15.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--no-gdelt", action="store_true")
    p.add_argument("--log", default=None, choices=[None, "redpanda", "file"])
    k = sub.add_parser("consume", help="stream path: classify events into the sink")
    k.add_argument("--idle-exit", type=float, default=None, help="stop after N idle seconds")
    k.add_argument("--log", default=None, choices=[None, "redpanda", "file"])
    k.add_argument("--as-of", default=None)
    bf = sub.add_parser("batch-file", help="batch path over a replay source file (used by measure)")
    bf.add_argument("--source", required=True)
    bf.add_argument("--every-s", type=float, default=5.0)
    bf.add_argument("--stop-file", default=None)
    bf.add_argument("--as-of", required=True)
    r = sub.add_parser("redrive-dlq", help="send dead-lettered events back to the raw topic")
    r.add_argument("--log", default=None, choices=[None, "redpanda", "file"])
    w = sub.add_parser("weekly", help="weekly list (text, and HTML with --html)")
    w.add_argument("--as-of", default=None)
    w.add_argument("--html", action="store_true")
    w.add_argument("--anon", action="store_true", help="hide pension names and links (for public docs)")
    y = sub.add_parser("why", help="evidence behind one pension's score")
    y.add_argument("pension")
    y.add_argument("--as-of", default=None)
    t = sub.add_parser("timeline", help="weekly score against the PREPARE line, with dated signals")
    t.add_argument("pension")
    t.add_argument("--as-of", default=None)
    t.add_argument("--weeks", type=int, default=26)
    ac = sub.add_parser("act", help="record a decision to prepare (assign an analyst)")
    ac.add_argument("pension")
    ac.add_argument("action", choices=["prepare"])
    ac.add_argument("--owner", required=True, help="the analyst who prepares")
    ds = sub.add_parser("dismiss", help="dismiss a flag; the reason feeds the rule review")
    ds.add_argument("pension")
    ds.add_argument("--reason", required=True)
    sub.add_parser("rebuild", help="re-process every landed fetch with the current classifier and rulebook")
    el = sub.add_parser("eval-langfuse", help="labelled headlines as a Langfuse dataset and experiment")
    el.add_argument("--no-upload", action="store_true", help="run locally even when Langfuse keys are set")
    sub.add_parser("rulebook", help="print the rulebook as sentences")
    sub.add_parser("pensions", help="list the pensions we link to")
    e = sub.add_parser("eval", help="classifier precision and recall on labelled headlines")
    e.add_argument("--errors", action="store_true")
    cs = sub.add_parser("cases", help="replay the worked historical cases")
    cs.add_argument("--refresh", action="store_true", help="re-fetch the frozen corpus")
    m = sub.add_parser("measure", help="run the collection architecture experiment")
    m.add_argument("--log", default=None, choices=[None, "redpanda", "file"])
    m.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    from .classify.base import get_classifier

    if a.cmd == "collect":
        from .batch import run_live_once

        print(json.dumps(run_live_once(a.db, get_classifier(a.classifier), use_gdelt=not a.no_gdelt), indent=1, default=str))
    elif a.cmd == "batch":
        import time as _t

        from .batch import run_live_once

        while True:
            print(json.dumps(run_live_once(a.db, get_classifier(a.classifier), use_gdelt=not a.no_gdelt), default=str), flush=True)
            _t.sleep(a.every * 60)
    elif a.cmd == "poll":
        from .stream import poller
        from .stream.log import get_log

        poller.run(get_log(a.log), a.every, once=a.once, use_gdelt=not a.no_gdelt)
    elif a.cmd == "consume":
        from .stream import consumer
        from .stream.log import get_log

        lg = get_log(a.log)
        db = a.db if a.db != str(DEFAULT_DB) else str(DATA_DIR / "stream.duckdb")
        print(f"consuming from {lg.name} into {db}", flush=True)
        print(consumer.run(lg, db, get_classifier(a.classifier), as_of=_as_of(a.as_of) if a.as_of else None,
                           idle_exit=a.idle_exit))
    elif a.cmd == "batch-file":
        from .batch import run_file_loop

        print(run_file_loop(a.db, Path(a.source), get_classifier(a.classifier), _as_of(a.as_of), a.every_s,
                            Path(a.stop_file) if a.stop_file else None), flush=True)
    elif a.cmd == "redrive-dlq":
        from .stream.consumer import redrive_dlq
        from .stream.log import get_log

        print(f"redriven: {redrive_dlq(get_log(a.log))}")
    elif a.cmd in ("timeline", "act", "dismiss"):
        from . import journey, store

        pid = journey.resolve(a.pension)
        if pid is None:
            print(journey.unknown(a.pension))
            return 2
        con = store.connect(a.db)
        if a.cmd == "timeline":
            print(journey.timeline_text(con, pid, _as_of(a.as_of), weeks=a.weeks))
        else:
            d = journey.record_decision(con, pid, "prepare" if a.cmd == "act" else "dismiss",
                                        owner=getattr(a, "owner", None), reason=getattr(a, "reason", None))
            what = f"prepare, owner {d['owner']}" if d["action"] == "prepare" else f"dismiss: {d['reason']}"
            print(f"Recorded {what} for {pid} by {d['decided_by']} at {d['decided_at']:%Y-%m-%d %H:%M} UTC, "
                  f"rulebook v{d['rulebook_version']}, score then {d['score']:.2f} ({d['verdict']}). "
                  + ("Sent to Langfuse as score partner_decision." if d["sent_to_langfuse"]
                     else "Langfuse off or no traced run: stored locally only."))
    elif a.cmd == "rebuild":
        from . import pipeline, store
        from .collect import load_landing

        con = store.connect(a.db)
        print(pipeline.process_many(con, load_landing(), get_classifier(a.classifier), _as_of(None), path="batch"))
    elif a.cmd == "eval-langfuse":
        from .lf_eval import main as lf_main

        lf_main(a.classifier, upload=not a.no_upload)
    elif a.cmd in ("weekly", "why"):
        from . import report, store

        con = store.connect(a.db)
        as_of = _as_of(a.as_of)
        if a.cmd == "why":
            print(report.why_text(con, a.pension, as_of))
        else:
            print(report.weekly_text(con, as_of, anon=a.anon))
            if a.html:
                REPORTS_DIR.mkdir(exist_ok=True)
                out = REPORTS_DIR / f"weekly_{as_of.date()}.html"
                out.write_text(report.weekly_html(con, as_of))
                print(f"\nHTML: {out}")
    elif a.cmd == "rulebook":
        rb = rulebook()
        print(f"Rulebook v{rb.version}. A pension is on PREPARE when its score reaches {rb.prepare_threshold}.")
        if not rb.completed_sales_count:
            print("A sale that already closed is shown as evidence and adds nothing to the score.")
        print(f"Each signal adds weight x confidence, halved every {rb.half_life_days:.0f} days; "
              f"signals older than {rb.window_days} days are dropped.")
        for k, v in sorted(rb.weights.items(), key=lambda kv: -kv[1]):
            print(f"- {rb.descriptions.get(k, k)}: weight {v}.")
    elif a.cmd == "pensions":
        from .pensions import ALL

        for p in ALL.values():
            print(f"{p.id:<14} {p.state}  {p.name}  [{', '.join(p.aliases)}]")
    elif a.cmd == "eval":
        from .classify.keyword import KeywordClassifier
        from .evals import evaluate

        clf = get_classifier(a.classifier)
        for split in ("dev", "holdout", None):
            r = evaluate(clf, split)
            print(f"{(split or 'all'):<8} n={r.n:<4} precision {r.precision:.2f}  recall {r.recall:.2f}  "
                  f"(flagged {r.flagged}, labelled signals {r.labelled_signals})  link accuracy {r.link_accuracy:.2f}")
        print(f"classifier: {clf.name} {clf.version}; labels: agent-labelled, PM to review; target precision 0.80")
        if a.errors:
            for err in evaluate(clf).errors:
                print(f"  [{err['n']}] expected {err['expected']}, got {err['predicted']}: {err['headline']}")
    elif a.cmd == "cases":
        from . import cases

        results = []
        for case in cases.load_cases():
            r = cases.replay(case, get_classifier(a.classifier), refresh=a.refresh)
            results.append(r)
            print(cases.render(r) + "\n")
        from .config import MEASURE_DIR

        MEASURE_DIR.mkdir(exist_ok=True)
        (MEASURE_DIR / "worked_cases.json").write_text(json.dumps(results, indent=1, default=str))
        ok = any(r["weeks_of_warning"] >= 8 for r in results)
        print("Kill criterion (PRD 11): " + ("passed, at least one case shows 8+ weeks." if ok else
              "triggered. Neither case shows 8+ weeks of warning, so First Call is presented as monitoring only."))
    elif a.cmd == "measure":
        from . import measure

        measure.main(log_kind=a.log, quick=a.quick)
    return 0


if __name__ == "__main__":
    sys.exit(main())
