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
        {"eval": "Match accuracy, labelled offer lines", "result": f"{m['correct']} of {m['total']} ({m['accuracy']:.0%})",
         "target": f"{m['target']:.0%} or more"},
        {"eval": "Stress test: funds we own matched to themselves under broker noise",
         "result": f"{s['owned_matched_to_self']} of {s['owned_funds']}", "target": ""},
        {"eval": "Stress test: other funds matched to one of ours (false match)",
         "result": f"{s['false_same_fund']} of {s['not_owned_funds']}", "target": "0"},
        {"eval": "Metric fixtures, hand-computed", "result": f"{e['metric_fixtures']['correct']} of {e['metric_fixtures']['total']}",
         "target": "all"},
        {"eval": "Injection email (\"ignore your rules\")",
         "result": f"{e['injection']['rule_changes']} rule changes", "target": "0"},
        {"eval": "Validator cases", "result": f"{e['validator_cases']['correct']} of {e['validator_cases']['total']}",
         "target": "all"},
    ]


def verdict_evals() -> list[dict]:
    rows = []
    for name in ("v1", "v2"):
        a = ql.agreement(ql.load_rulebook(name))
        rows.append({"rulebook": name + (" (live)" if name == "v2" else ""),
                     "tune": f"{a['tune']['kept']} of {a['tune']['total']}",
                     "holdout": f"{a['holdout']['kept']} of {a['holdout']['total']}",
                     "good offers marked PASS (holdout)": a["holdout"]["good_passed"], "holdout target": "80%"})
    return rows


def parser_comparison() -> dict | None:
    d = _json("quicklook/measurements/parser_comparison.json").get("table")
    if not d:
        return None
    t = d["tokens"]["claude"]
    return {"match": d["match_accuracy"]["claude"], "fields": d["field_accuracy_mean"]["claude"],
            "claude_s": d["seconds_per_offer_mean"]["claude"], "offline_s": d["seconds_per_offer_mean"]["offline"],
            "tokens": t["input"] + t["output"]}
