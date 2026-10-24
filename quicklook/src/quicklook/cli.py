"""radar: the Quick Look command line.

  radar generate                    synthetic universe, offers and labels
  radar ingest FILE...              run the pipeline on .eml files
  radar demo                        generate, ingest every offer, print Cedar
  radar status                      offers and verdicts
  radar why cedar                   headline and reasons with sources
  radar override cedar pass --reason "..."
  radar rules print                 the rulebook as sentences
  radar rules test [CANDIDATE]      diff a candidate rulebook on the labelled offers
  radar replay                      rebuild silver, gold and briefs from bronze
  radar serve | batch | send        intake paths (see quicklook.intake)
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import pipeline, rulebook, store
from .config import DATA_DIR, RULEBOOK_PATH, Workspace, default_workspace, fixtures_path, offers_dir
from .parse import get_parser


def _ws(args) -> Workspace:
    return default_workspace(args.workspace)


def cmd_generate(args) -> int:
    from .synth import run

    out = run(DATA_DIR, seed=args.seed)
    print(f"Generated {out['funds']} funds, {out['series_rows']} quarterly rows, {out['holdings']} holdings, "
          f"{out['offers']} offers plus 1 non-offer email (seed {args.seed}) in {DATA_DIR}.")
    return 0


def cmd_ingest(args) -> int:
    ws = _ws(args)
    parser = get_parser(force_offline=args.offline)
    if parser.offline:
        print("Offline parser in use (no ANTHROPIC_API_KEY).")
    for f in args.files:
        r = pipeline.process_email(Path(f).read_bytes(), ws, parser, channel="cli")
        print(f"{Path(f).name}: {r.status}. {r.headline or ''}" + (f" Brief: {r.brief_path}" if r.brief_path else ""))
    return 0


def cmd_demo(args) -> int:
    if not fixtures_path().exists():
        cmd_generate(argparse.Namespace(seed=42))
    ws = _ws(args)
    parser = get_parser(force_offline=args.offline)
    files = sorted(offers_dir().glob("*.eml"))
    results = [pipeline.process_email(f.read_bytes(), ws, parser, channel="demo") for f in files]
    for f, r in zip(files, results):
        print(f"{f.stem:8s} {r.status:13s} {r.headline or ''}")
    cedar = next((r for r in results if (r.code_name or "").lower() == "cedar"), None)
    if cedar and cedar.offer_id:
        print()
        _why(ws, "cedar")
        con = store.connect(ws, read_only=True)
        b = store.one(con, "SELECT path FROM gold_briefs WHERE offer_id = ?", [cedar.offer_id])
        con.close()
        if b:
            print(f"\nBrief: {ws.briefs_dir / b['path']}")
    return 0


def cmd_status(args) -> int:
    con = store.connect(_ws(args), read_only=True)
    q = """SELECT o.code_name, v.verdict, v.band, v.score, b.valid,
                  (SELECT new_verdict FROM overrides x WHERE x.offer_id = o.offer_id ORDER BY created_at DESC LIMIT 1) AS ov
           FROM silver_offers o JOIN gold_verdicts v USING (offer_id) LEFT JOIN gold_briefs b USING (offer_id)
           ORDER BY o.email_date"""
    for r in store.rows(con, q):
        ov = f"  overridden to {r['ov']}" if r["ov"] else ""
        score = "n/a" if r["score"] is None else f"{r['score']:.2f}"
        print(f"{r['code_name']:8s} {r['verdict']:12s} {r['band']:6s} score {score}  brief {'ok' if r['valid'] else 'INVALID'}{ov}")
    for r in store.rows(con, "SELECT * FROM review_queue ORDER BY logged_at"):
        print(f"review queue: {r['message_id']}: {r['reason']}")
    con.close()
    return 0


def _why(ws: Workspace, offer: str) -> int:
    con = store.connect(ws, read_only=True)
    o = store.resolve_offer(con, offer)
    if not o:
        print(f"No offer called {offer}.")
        return 1
    oid = o["offer_id"]
    v = store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [oid])
    print(f"Project {o['code_name']} (values at {o['reference_date']}, received {o['email_date']:%Y-%m-%d %H:%M})")
    print(v["headline"])
    ov = store.one(con, "SELECT * FROM overrides WHERE offer_id = ? ORDER BY created_at DESC LIMIT 1", [oid])
    if ov:
        print(f"Team override: {ov['new_verdict']} by {ov['user_name']} ({ov['reason']})")
    score = "n/a" if v["score"] is None else f"{v['score']:.2f}"
    print(f"Score {score} vs threshold {v['threshold']:g}; data matched {v['data_matched']:.0%}; rulebook v{v['rulebook_version']}.")
    print("Why:")
    for r in store.rows(con, "SELECT * FROM gold_rule_results WHERE offer_id = ? ORDER BY rank", [oid]):
        val = "no value" if r["value"] is None else f"{r['value']:.3f}"
        veto = " (veto)" if r["veto"] else ""
        print(f"  {r['status']:11s} {r['description']}{veto}: {r['metric']} = {val} {r['op']} {r['threshold']:g}"
              f"  [gold_offer_metrics {oid}:{r['metric']}]")
    for l in store.rows(con, "SELECT * FROM gold_line_matches WHERE offer_id = ? AND NOT resolved ORDER BY line_no", [oid]):
        names = ", ".join(c["fund_name"] for c in json.loads(l["candidates_json"]))
        print(f"  open: {l['label']} ({l['method']}); candidates: {names or 'none'}")
    con.close()
    return 0


def cmd_why(args) -> int:
    return _why(_ws(args), args.offer)


def cmd_override(args) -> int:
    try:
        r = pipeline.record_override(_ws(args), args.offer, args.verdict, args.reason, args.user or getpass.getuser())
    except (ValueError, KeyError) as e:
        print(str(e).strip("'\""))
        return 2
    print(f"Recorded: {r['code_name']} {r['original_verdict']} -> {r['new_verdict']} by {r['user_name']}, "
          f"rulebook v{r['rulebook_version']}. Recorded, not learned from. Brief updated: {r['brief_path']}")
    return 0


def cmd_rules_print(args) -> int:
    rb = rulebook.load(Path(args.rulebook))
    print("\n".join(rulebook.as_sentences(rb)))
    return 0


def labelled_metrics(ws: Workspace) -> tuple[dict[str, dict], dict[str, dict]]:
    """Metrics per labelled offer, ingesting the offer first if this workspace lacks it."""
    labels = {o["code"]: o for o in json.loads(fixtures_path().read_text())["offers"]}
    con = store.connect(ws)
    have = {r["code_name"] for r in store.rows(con, "SELECT code_name FROM silver_offers")}
    con.close()
    parser = None
    for code, lab in labels.items():
        if code not in have:
            parser = parser or get_parser()
            pipeline.process_email((offers_dir() / lab["file"]).read_bytes(), ws, parser, channel="rules-test")
    con = store.connect(ws, read_only=True)
    out: dict[str, dict] = {}
    for code in labels:
        o = store.resolve_offer(con, code)
        out[code] = {m["metric"]: m["value"] for m in
                     store.rows(con, "SELECT metric, value FROM gold_offer_metrics WHERE offer_id = ?", [o["offer_id"]])}
    con.close()
    return out, labels


def agreement(verdicts: dict, labels: dict) -> tuple[int, int, int]:
    tests = [c for c in verdicts if labels[c]["is_test_offer"]]
    kept = sum(1 for c in tests if (verdicts[c].verdict == "LOOK CLOSER") == (labels[c]["pm_label"] == "LOOK"))
    good_passed = sum(1 for c in tests if labels[c]["pm_label"] == "LOOK" and verdicts[c].verdict == "PASS")
    return kept, len(tests), good_passed


def cmd_rules_test(args) -> int:
    ws = _ws(args)
    current = rulebook.load(Path(args.current))
    try:
        cand = rulebook.load(Path(args.candidate)) if args.candidate else current
    except rulebook.RulebookError as e:
        print(f"Candidate rulebook rejected: {e}")
        return 2
    mets, labels = labelled_metrics(ws)
    old = {c: rulebook.evaluate(current, m) for c, m in mets.items()}
    new = {c: rulebook.evaluate(cand, m) for c, m in mets.items()}
    print(f"Rulebook v{current.version} -> v{cand.version}, on {len(labels)} labelled offers:")
    for s in rulebook.diff_sentences(old, new):
        print("  " + s)
    for name, vs, rb in (("current", old, current), ("candidate", new, cand)):
        kept, n, gp = agreement(vs, labels)
        print(f"{name} v{rb.version}: verdicts agree with the PM labels on {kept} of {n} test offers "
              f"({kept / n:.0%}); good offers marked PASS: {gp}.")
    return 0


def cmd_replay(args) -> int:
    import time

    ws = _ws(args)
    t0 = time.perf_counter()
    res = pipeline.replay(ws, get_parser(force_offline=args.offline))
    print(f"Replayed {len(res)} emails from bronze in {time.perf_counter() - t0:.2f}s.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="Quick Look: screen secondaries offers from email.")
    p.add_argument("--workspace", default="main", help="data/<name> holds this pipeline's layers")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, default=42)
    g.set_defaults(fn=cmd_generate)
    i = sub.add_parser("ingest")
    i.add_argument("files", nargs="+")
    i.add_argument("--offline", action="store_true")
    i.set_defaults(fn=cmd_ingest)
    d = sub.add_parser("demo")
    d.add_argument("--offline", action="store_true")
    d.set_defaults(fn=cmd_demo)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    w = sub.add_parser("why")
    w.add_argument("offer")
    w.set_defaults(fn=cmd_why)
    o = sub.add_parser("override")
    o.add_argument("offer")
    o.add_argument("verdict", choices=["pass", "look"])
    o.add_argument("--reason", required=True)
    o.add_argument("--user")
    o.set_defaults(fn=cmd_override)
    r = sub.add_parser("rules")
    rs = r.add_subparsers(dest="rules_cmd", required=True)
    rp = rs.add_parser("print")
    rp.add_argument("--rulebook", default=str(RULEBOOK_PATH))
    rp.set_defaults(fn=cmd_rules_print)
    rt = rs.add_parser("test")
    rt.add_argument("candidate", nargs="?")
    rt.add_argument("--current", default=str(RULEBOOK_PATH))
    rt.set_defaults(fn=cmd_rules_test)
    rep = sub.add_parser("replay")
    rep.add_argument("--offline", action="store_true")
    rep.set_defaults(fn=cmd_replay)

    from .intake import cli as intake_cli

    intake_cli.register(sub)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except rulebook.RulebookError as e:
        print(f"Rulebook error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
