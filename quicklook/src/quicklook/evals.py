"""Experiment runner over the labelled data, for one parser (offline or claude).

Two datasets, built from data/fixtures/labels.json:
  quicklook-offer-lines  input: offer, line number, line text  expected: true fund, tier, manager, fields as written
  quicklook-offers       input: offer                          expected: verdict (the label)
Item-level scores: match_correct, field_accuracy (lines); verdict_correct (offers).
Run-level scores: match_accuracy, field_accuracy_mean, verdict_agreement_tune, verdict_agreement_holdout,
good_offers_passed.

With LANGFUSE_* keys the datasets are synced and the run is a Langfuse experiment (one trace per item).
Without keys, or with --local, the same task and evaluators run in a plain loop. Either way the numbers
go to measurements/eval_<parser>.json. pytest stays the gate; this is the shared record.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import time
from pathlib import Path

from langfuse import Evaluation

from . import pipeline, store
from .config import DATA_DIR, ROOT, Workspace, fixtures_path, offers_dir
from .emailio import read_email
from .tracing import get_tracer, keys_present

DATASET_LINES = "quicklook-offer-lines"
DATASET_OFFERS = "quicklook-offers"
FIELDS = ("vintage", "currency", "commitment", "pct_called", "dpi", "tvpi", "value")
OUT = ROOT / "measurements"


# ---------------------------------------------------------------- items

def line_text(content, line_no: int) -> str:
    """The line as the broker wrote it: a body line, or the table row with its header."""
    for raw in content.body.splitlines():
        if raw.strip().startswith(f"Fund {line_no}:") or raw.strip().startswith(f"Fund {line_no} ("):
            return raw.strip()
    for t in content.tables:
        for r in t.rows[1:]:
            if r and r[0].strip() == f"Fund {line_no}":
                return "; ".join(f"{h}: {v}" for h, v in zip(t.rows[0], r) if str(v).strip())
    return ""


def build_items(data_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    labels = json.loads((data_dir / "fixtures" / "labels.json" if data_dir else fixtures_path()).read_text())
    odir = data_dir / "offers" if data_dir else offers_dir()
    lines, offers = [], []
    for o in labels["offers"]:
        content = read_email((odir / o["file"]).read_bytes())
        offers.append(dict(
            id=f"offer-{o['code'].lower()}",
            input={"offer": o["code"], "file": o["file"], "subject": content.subject},
            expected_output={"verdict": "LOOK CLOSER" if o["pm_label"] == "LOOK" else "PASS"},
            metadata={"split": o["split"], "archetype": o["archetype"], "rationale": o["rationale"],
                      "is_test_offer": o["is_test_offer"], "fmt": o["fmt"],
                      "label_source": "synthetic: analyst intent written before the rulebook ran"}))
        for l in o["lines"]:
            lines.append(dict(
                id=f"line-{o['code'].lower()}-{l['line_no']:02d}",
                input={"offer": o["code"], "file": o["file"], "line_no": l["line_no"],
                       "line_text": line_text(content, l["line_no"])},
                expected_output={"true_fund_id": l["true_fund_id"], "tier": l["expected_tier"],
                                 "resolved": l["expected_resolved"], "manager_id": l["expected_manager_id"],
                                 "fields": l["shown"]},
                metadata={"offer": o["code"], "split": o["split"], "selector": l["selector"], "fmt": o["fmt"],
                          "label_source": "synthetic generator truth"}))
    return lines, offers


def sync_datasets(lf, line_items: list[dict], offer_items: list[dict]) -> None:
    """Create the datasets if needed and upsert every item by its stable id."""
    for name, items, desc in ((DATASET_LINES, line_items, "Offer lines: which fund, which tier, fields as written"),
                              (DATASET_OFFERS, offer_items, "Offers: LOOK CLOSER or PASS, with tune/holdout split")):
        try:
            lf.create_dataset(name=name, description=desc, metadata={"source": "quicklook synth seed 42"})
        except Exception:
            pass  # already there
        for it in items:
            lf.create_dataset_item(dataset_name=name, id=it["id"], input=it["input"],
                                   expected_output=it["expected_output"], metadata=it["metadata"])


# ---------------------------------------------------------------- task

def _inp(item) -> dict:
    return item.input if hasattr(item, "input") else item["input"]


class Task:
    """Runs the pipeline once per offer into its own workspace; later lines of the offer read gold."""

    def __init__(self, parser, ws: Workspace, data_dir: Path | None = None):
        self.parser, self.ws = parser, ws
        self.odir = data_dir / "offers" if data_dir else offers_dir()
        self.calls: list[dict] = []  # per parsed offer: seconds, tokens

    def _ensure(self, inp: dict) -> dict | None:
        raw = (self.odir / inp["file"]).read_bytes()
        r = pipeline.process_email(raw, self.ws, self.parser, channel="eval")
        if r.status != "duplicate":
            call = dict(getattr(self.parser, "last_call", None) or {})
            self.calls.append(dict(offer=inp["offer"], status=r.status, seconds=round(r.seconds, 3),
                                   usage=call.get("usage"), fell_back=call.get("fell_back_offline", False)))
        con = store.connect(self.ws, read_only=True)
        o = store.resolve_offer(con, inp["offer"])
        con.close()
        return o

    def __call__(self, *, item, **_):
        inp = _inp(item)
        o = self._ensure(inp)
        if o is None:
            return {"missing": True, "reason": "offer not parsed"}
        con = store.connect(self.ws, read_only=True)
        try:
            if "line_no" not in inp:
                v = store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [o["offer_id"]])
                return {"verdict": v["verdict"], "band": v["band"], "score": v["score"], "headline": v["headline"]}
            s = store.one(con, "SELECT * FROM silver_offer_lines WHERE offer_id = ? AND line_no = ?",
                          [o["offer_id"], inp["line_no"]])
            g = store.one(con, "SELECT * FROM gold_line_matches WHERE offer_id = ? AND line_no = ?",
                          [o["offer_id"], inp["line_no"]])
            if not s or not g:
                return {"missing": True, "reason": "line not in silver (blocked or not read)"}
            return {"tier": g["tier"], "fund_id": g["fund_id"], "manager_id": g["manager_id"],
                    "resolved": bool(g["resolved"]), "confidence": g["confidence"], "method": g["method"],
                    "fields": {k: s[k] for k in FIELDS}}
        finally:
            con.close()


# ---------------------------------------------------------------- evaluators

def match_correct(*, output, expected_output, **_):
    if output.get("missing"):
        return Evaluation(name="match_correct", value=0, data_type="BOOLEAN", comment=output["reason"])
    e = expected_output
    ok = (output["tier"] == e["tier"] and output["resolved"] == e["resolved"]
          and (e["tier"] != "same_fund" or output["fund_id"] == e["true_fund_id"])
          and (e["tier"] != "same_manager" or output["manager_id"] == e["manager_id"]))
    return Evaluation(name="match_correct", value=1 if ok else 0, data_type="BOOLEAN",
                      comment=None if ok else f"got {output['tier']} {output.get('fund_id') or output.get('manager_id')}, "
                                              f"expected {e['tier']} {e['true_fund_id']}")


def _same(a, b, key: str) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(b, str) or isinstance(a, str):
        return str(a) == str(b)
    return abs(float(a) - float(b)) <= (0.0005 if key == "pct_called" else 1e-6)


def field_accuracy(*, output, expected_output, **_):
    if output.get("missing"):
        return Evaluation(name="field_accuracy", value=0.0, comment=output["reason"])
    want = expected_output["fields"]
    wrong = [k for k in FIELDS if not _same(output["fields"].get(k), want.get(k), k)]
    return Evaluation(name="field_accuracy", value=1 - len(wrong) / len(FIELDS),
                      comment=("wrong: " + ", ".join(f"{k} got {output['fields'].get(k)} want {want.get(k)}"
                                                     for k in wrong)) if wrong else None)


def verdict_correct(*, output, expected_output, **_):
    if output.get("missing"):
        return Evaluation(name="verdict_correct", value=0, data_type="BOOLEAN", comment=output["reason"])
    ok = output["verdict"] == expected_output["verdict"]
    return Evaluation(name="verdict_correct", value=1 if ok else 0, data_type="BOOLEAN",
                      comment=None if ok else f"got {output['verdict']} (score {output['score']}), "
                                              f"label {expected_output['verdict']}")


def _records(item_results) -> list[dict]:
    out = []
    for r in item_results:
        item = r.item
        md = item.metadata if hasattr(item, "metadata") else item.get("metadata", {})
        exp = item.expected_output if hasattr(item, "expected_output") else item.get("expected_output")
        out.append(dict(input=_inp(item), metadata=md or {}, expected=exp, output=r.output,
                        scores={e.name: e.value for e in r.evaluations}))
    return out


def aggregates(*, item_results, **_):
    recs = _records(item_results)
    evs = []

    def mean(name, pred=lambda r: True):
        vals = [float(r["scores"][name]) for r in recs if name in r["scores"] and pred(r)]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    for name, agg in (("match_correct", "match_accuracy"), ("field_accuracy", "field_accuracy_mean")):
        v, n = mean(name)
        if v is not None:
            evs.append(Evaluation(name=agg, value=v, comment=f"{n} lines"))
    for split in ("tune", "holdout"):
        v, n = mean("verdict_correct", lambda r: r["metadata"].get("split") == split and r["metadata"].get("is_test_offer"))
        if v is not None:
            evs.append(Evaluation(name=f"verdict_agreement_{split}", value=v, comment=f"{n} offers"))
    gp = [r for r in recs if "verdict_correct" in r["scores"] and r["metadata"].get("is_test_offer")
          and r["expected"]["verdict"] == "LOOK CLOSER" and r["output"].get("verdict") == "PASS"]
    if any("verdict_correct" in r["scores"] for r in recs):
        evs.append(Evaluation(name="good_offers_passed", value=len(gp),
                              comment=", ".join(r["input"]["offer"] for r in gp) or None))
    return evs


# ---------------------------------------------------------------- runners

class _LocalResult:
    def __init__(self, item, output, evaluations):
        self.item, self.output, self.evaluations = item, output, evaluations


def run_local(items: list[dict], task, evaluators) -> tuple[list, list]:
    results = []
    for it in items:
        out = task(item=it)
        evs = [e(input=it["input"], output=out, expected_output=it["expected_output"], metadata=it["metadata"])
               for e in evaluators]
        results.append(_LocalResult(it, out, evs))
    return results, aggregates(item_results=results)


def make_parser(name: str):
    if name == "claude":
        from .parse.claude import ClaudeParser

        return ClaudeParser()
    from .parse import OfflineParser

    return OfflineParser()


def run(parser_name: str = "offline", local: bool = False, limit: int | None = None,
        data_dir: Path | None = None, out_dir: Path = OUT) -> dict:
    line_items, offer_items = build_items(data_dir)
    if limit:
        keep = {it["input"]["offer"] for it in offer_items[:limit]}
        offer_items = [it for it in offer_items if it["input"]["offer"] in keep]
        line_items = [it for it in line_items if it["input"]["offer"] in keep]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = (data_dir or DATA_DIR) / f"eval-{parser_name}"
    shutil.rmtree(root, ignore_errors=True)
    ws = Workspace(root=root, reference_dir=(data_dir or DATA_DIR) / "reference")
    parser = make_parser(parser_name)
    task = Task(parser, ws, data_dir)
    if local:  # local means nothing leaves the machine, pipeline traces included
        from .tracing import Tracer, set_tracer

        set_tracer(Tracer(None))
    tr = get_tracer()
    use_lf = tr.on and not local
    t0 = time.perf_counter()
    report: dict = {"parser": parser.name, "parser_version": parser.version, "run_name": f"{parser_name}-{stamp}",
                    "mode": "langfuse" if use_lf else "local", "n_offers": len(offer_items), "n_lines": len(line_items)}
    for key, items, name, evaluators in (("lines", line_items, DATASET_LINES, [match_correct, field_accuracy]),
                                         ("offers", offer_items, DATASET_OFFERS, [verdict_correct])):
        if use_lf:
            lf = tr.client
            if key == "lines":
                sync_datasets(lf, line_items, offer_items)
            ds = lf.get_dataset(name)
            data = [i for i in ds.items if not limit or _inp(i)["offer"] in {x["input"]["offer"] for x in items}]
            res = lf.run_experiment(name=f"quicklook {key}", run_name=report["run_name"],
                                    description=f"Quick Look pipeline with the {parser_name} parser", data=data,
                                    task=task, evaluators=evaluators, run_evaluators=[aggregates], max_concurrency=1,
                                    metadata={"parser": parser.name, "prompt_version": parser.version})
            item_results, run_evs = res.item_results, res.run_evaluations
            report[f"{key}_experiment_url"] = getattr(res, "experiment_url", None)
        else:
            item_results, run_evs = run_local(items, task, evaluators)
        report[key] = {e.name: e.value for e in run_evs}
        recs = _records(item_results)
        report[f"{key}_failures"] = [dict(id=f"{r['input']['offer']}" + (f":{r['input']['line_no']}" if "line_no" in r["input"] else ""),
                                          scores=r["scores"]) for r in recs
                                     if any(v not in (1, 1.0) for v in r["scores"].values())]
        if key == "lines":
            per_field = {k: 0 for k in FIELDS}
            for r in recs:
                o = r["output"]
                for k in FIELDS:
                    per_field[k] += 0 if o.get("missing") else int(_same(o["fields"].get(k), r["expected"]["fields"].get(k), k))
            report["field_accuracy_by_field"] = {k: round(v / len(recs), 4) for k, v in per_field.items()}
    tr.flush()
    toks = [c["usage"] for c in task.calls if c.get("usage")]
    report["parse_calls"] = len(task.calls)
    report["seconds_total"] = round(time.perf_counter() - t0, 2)
    report["seconds_per_offer_mean"] = round(sum(c["seconds"] for c in task.calls) / max(1, len(task.calls)), 3)
    if toks:
        report["tokens"] = {k: sum(t.get(k, 0) for t in toks) for k in ("input", "output")}
    report["fell_back_offline"] = sum(1 for c in task.calls if c.get("fell_back"))
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"eval_{parser_name}.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def main(parser: str = "offline", local: bool = False, limit: int | None = None) -> int:
    if parser == "claude":
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY is not set (shell, quicklook/.env or ../.env). The Claude eval did not run.")
            return 2
    if not keys_present() and not local:
        print("No LANGFUSE keys: running locally, results in measurements/ only.")
    r = run(parser, local, limit)
    print(f"{r['parser']} ({r['mode']}): {r['n_offers']} offers, {r['n_lines']} lines, {r['seconds_total']}s")
    for k in ("lines", "offers"):
        for name, v in r[k].items():
            print(f"  {name}: {v:.3f}" if isinstance(v, float) else f"  {name}: {v}")
    if r.get("tokens"):
        print(f"  tokens: {r['tokens']['input']} in, {r['tokens']['output']} out")
    for k in ("lines_experiment_url", "offers_experiment_url"):
        if r.get(k):
            print(f"  {k.split('_')[0]} experiment: {r[k]}")
    print(f"Saved measurements/eval_{parser}.json")
    return 0


def compare(out_dir: Path = OUT) -> int:
    runs = {}
    for p in ("offline", "claude"):
        f = out_dir / f"eval_{p}.json"
        if f.exists():
            runs[p] = json.loads(f.read_text())
    if len(runs) < 2:
        print(f"Need both eval_offline.json and eval_claude.json in measurements/; have {sorted(runs)}.")
        return 2
    rows = ["match_accuracy", "field_accuracy_mean", "verdict_agreement_tune", "verdict_agreement_holdout",
            "good_offers_passed"]
    table = {}
    for name in rows:
        table[name] = {p: (r["lines"] | r["offers"]).get(name) for p, r in runs.items()}
    for k in FIELDS:
        table[f"field:{k}"] = {p: r["field_accuracy_by_field"].get(k) for p, r in runs.items()}
    table["seconds_per_offer_mean"] = {p: r["seconds_per_offer_mean"] for p, r in runs.items()}
    table["tokens"] = {p: r.get("tokens") for p, r in runs.items()}
    out = {"compared_at": dt.datetime.now().isoformat(timespec="seconds"),
           "runs": {p: r["run_name"] for p, r in runs.items()}, "table": table,
           "only_offline_failed": sorted({f["id"] for f in runs["offline"]["lines_failures"]}
                                         - {f["id"] for f in runs["claude"]["lines_failures"]}),
           "only_claude_failed": sorted({f["id"] for f in runs["claude"]["lines_failures"]}
                                        - {f["id"] for f in runs["offline"]["lines_failures"]})}
    (out_dir / "parser_comparison.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"{'metric':<28}{'offline':>12}{'claude':>12}")
    for name, v in table.items():
        f = lambda x: "n/a" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))  # noqa: E731
        print(f"{name:<28}{f(v.get('offline')):>12}{f(v.get('claude')):>12}")
    print(f"Lines only offline got wrong: {', '.join(out['only_offline_failed']) or 'none'}")
    print(f"Lines only Claude got wrong: {', '.join(out['only_claude_failed']) or 'none'}")
    print("Saved measurements/parser_comparison.json")
    return 0
