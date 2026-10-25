"""Under-the-hood data for the demo: the pattern diagram, measured tables and eval results.

Every number is read from files committed in the repo (outputs/decisions/*.md and */measurements/*.json),
or recomputed from the Quick Look snapshot, so the page cannot drift from the evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import ql

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = [
    ("Quick Look intake: batch, every minute", ROOT / "outputs" / "decisions" / "quick-look-collection-architecture.md"),
    ("First Call news: batch, every 15 minutes", ROOT / "outputs" / "decisions" / "first-call-collection-architecture.md"),
]

DIAGRAM = """digraph {
  rankdir=LR; node [shape=box, style="rounded", fontname="Helvetica", fontsize=11];
  src [label="Source\\nemail or headline"];
  bronze [label="Bronze\\nstored as received"];
  read [label="Read\\nmodel or offline rules\\nfill a fixed schema", style="rounded,dashed"];
  silver [label="Silver\\nPydantic-checked rows"];
  gold [label="Gold\\nmatch, metrics, scores\\n(code, point in time)"];
  rules [label="Rulebook\\nplain English YAML"];
  out [label="Brief or weekly list\\nevery number sourced"];
  src -> bronze -> read -> silver -> gold -> rules -> out;
  {rank=same; read}
}"""


def measured_table(path: Path) -> str:
    """The first markdown table under the 'Measured' heading of a decision doc."""
    lines = path.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("## Measured"))
    rows, seen = [], False
    for l in lines[start + 1:]:
        if l.startswith("|"):
            rows.append(l)
            seen = True
        elif seen:
            break
    return "\n".join(rows)


def _json(rel: str) -> dict:
    p = ROOT / rel
    return json.loads(p.read_text()) if p.exists() else {}


def quicklook_evals() -> list[dict]:
    e = _json("quicklook/measurements/evals.json")
    m, s = e["match_accuracy_labelled"], e["match_stress_noise"]
    return [
        {"test": "Offer lines matched to the right fund", "result": f"{m['correct']} of {m['total']} ({m['accuracy']:.0%})",
         "target": f"{m['target']:.0%} or more"},
        {"test": "Messy broker wording: funds we own still found",
         "result": f"{s['owned_matched_to_self']} of {s['owned_funds']}", "target": ""},
        {"test": "Messy broker wording: other funds wrongly taken for ours",
         "result": f"{s['false_same_fund']} of {s['not_owned_funds']}", "target": "0"},
        {"test": "Numbers checked against hand calculations", "result": f"{e['metric_fixtures']['correct']} of {e['metric_fixtures']['total']}",
         "target": "all"},
        {"test": "Email that says \"ignore your rules\"",
         "result": f"{e['injection']['rule_changes']} rules changed", "target": "0"},
        {"test": "Source checker: test briefs judged right", "result": f"{e['validator_cases']['correct']} of {e['validator_cases']['total']}",
         "target": "all"},
    ]


def verdict_evals() -> list[dict]:
    rows = []
    for name in ("v1", "v2"):
        a = ql.agreement(ql.load_rulebook(name))
        rows.append({"rules version": name + (" (in use)" if name == "v2" else ""),
                     "offers used to set the rules": f"{a['tune']['kept']} of {a['tune']['total']} agree",
                     "new offers (the fair test)": f"{a['holdout']['kept']} of {a['holdout']['total']} agree",
                     "good new offers wrongly passed": a["holdout"]["good_passed"], "target on new offers": "80%"})
    return rows


def parser_comparison() -> dict | None:
    d = _json("quicklook/measurements/parser_comparison.json").get("table")
    if not d:
        return None
    t = d["tokens"]["claude"]
    return {"match": d["match_accuracy"]["claude"], "fields": d["field_accuracy_mean"]["claude"],
            "claude_s": d["seconds_per_offer_mean"]["claude"], "offline_s": d["seconds_per_offer_mean"]["offline"],
            "tokens": t["input"] + t["output"]}


# ------------------------------------------------------------------ Langfuse: traces and experiments

LANGFUSE = ROOT / "demo" / "data" / "langfuse_examples.json"
STEP_WORDS = {
    "quick-look-offer": "one offer, end to end", "intake": "receive the email", "parse": "model reads the email",
    "match": "match each line to a fund", "metrics": "compute the numbers", "verdict": "apply the team's rules",
    "brief": "write the brief", "validator": "check every number has a source",
    "run-batch": "one overnight run, end to end", "fetch-source": "fetch one news search",
    "dedupe-items": "drop repeated headlines", "classify-headline": "model sorts one headline",
    "score-pensions": "update every pension's total",
}


def langfuse_examples() -> dict:
    """Two real traces and the latest experiments, exported by demo/export_langfuse.py (no headlines kept)."""
    return _json("demo/data/langfuse_examples.json")


def trace(feature: str) -> dict | None:
    return next((t for t in langfuse_examples().get("traces", []) if t["feature"] == feature), None)


def trace_steps(t: dict) -> list[dict]:
    """One row per step, as in the Langfuse tree view: indented name, kind, start, duration, tokens, cost."""
    rows = []
    for i, s in enumerate(t["steps"]):
        tokens = f"{s['tokens_in']:,} in, {s['tokens_out']:,} out" if s.get("tokens_in") else ""
        rows.append({"order": i, "step": ("    " * s["depth"]) + s["name"], "name": s["name"],
                     "what it does": STEP_WORDS.get(s["name"], ""), "kind": s["type"].lower(),
                     "start_s": s["start_ms"] / 1000, "end_s": (s["start_ms"] + max(s["duration_ms"], 30)) / 1000,
                     "took": f"{s['duration_ms'] / 1000:.2f} s", "model": s.get("model") or "", "tokens": tokens,
                     "cost (USD)": f"{s['cost_usd']:.4f}" if s.get("cost_usd") else "", "result": s["summary"]})
    return rows


def trace_summary(t: dict) -> dict:
    gens = [s for s in t["steps"] if s["type"] == "GENERATION"]
    return {"steps": len(t["steps"]), "model_calls": len(gens), "seconds": t["latency_s"],
            "model_seconds": sum(s["duration_ms"] for s in gens) / 1000,
            "tokens": sum((s.get("tokens_in") or 0) + (s.get("tokens_out") or 0) for s in gens),
            "cost": t["cost_usd"] or 0.0, "fetches": sum(s["name"] == "fetch-source" for s in t["steps"]), "model": next((s["model"] for s in gens if s.get("model")), None),
            "when": t["timestamp"][:16].replace("T", " ") + " UTC"}


def experiment_rows() -> dict[str, list[dict]]:
    """The latest Langfuse experiments, side by side, per dataset."""
    ex = langfuse_examples().get("experiments", [])
    fc = []
    for e in ex:
        m = e["metadata"]
        if "classifier" in m:
            fc.append({"classifier": f"{'Keyword rules' if m['classifier'] == 'keyword' else 'Claude'} {m['version'].split('/')[-1]}",
                       "split": m["split"], "precision": round(e["scores"].get("precision", 0), 2),
                       "recall": round(e["scores"].get("recall", 0), 2),
                       "right pension": round(e["scores"].get("link_accuracy", 0), 2), "run": e["name"].split(" ")[-2]})
    ql_rows: dict[str, dict] = {}
    for e in ex:
        m = e["metadata"]
        if "parser" in m:
            r = ql_rows.setdefault(m["parser"], {"parser": "Claude" if m["parser"].startswith("claude") else "Offline rules",
                                                 "prompt": m.get("prompt_version", "")})
            for k, v in e["scores"].items():
                r[k] = round(v, 2)
    ql = [{"parser": r["parser"], "prompt": r["prompt"], "lines matched right": r.get("match_accuracy"),
           "fields read right": r.get("field_accuracy_mean"),
           "verdict agrees, new offers": r.get("verdict_agreement_holdout"),
           "good offers kept": r.get("good_offers_passed")} for r in ql_rows.values()]
    fc.sort(key=lambda r: (r["split"], r["classifier"]))
    return {"firstcall": fc, "quicklook": ql}


def classifier_in_words() -> list[str]:
    """The First Call holdout result as counts, from the committed eval files."""
    out = []
    for f, name in (("eval_claude.json", "Claude"), ("eval_keyword.json", "The offline keyword rules")):
        h = _json(f"firstcall/measurements/{f}").get("splits", {}).get("holdout")
        if not h:
            continue
        right = round(h["precision"] * h["flagged"])
        caught = round(h["recall"] * h["labelled_signals"])
        out.append(f"{name} flagged {h['flagged']} headlines as signs, and {right} were right. Of the "
                   f"{h['labelled_signals']} real signs, {caught} were caught.")
    return out


def holdout_size() -> tuple[int, int]:
    h = _json("firstcall/measurements/eval_claude.json")["splits"]["holdout"]
    return h["n"], h["labelled_signals"]
