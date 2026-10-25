"""Export two real Langfuse traces and the latest experiments for the "Under the hood" tab.

    .venv/bin/python demo/export_langfuse.py [--ql-trace ID] [--fc-trace ID]

Reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL (or LANGFUSE_HOST) from the
shell or the repo .env. Key values are never printed or written.

What is kept: the tree of steps (name, kind, parent, start offset, duration), model name, tokens and
cost, plus a short summary per step. Seller Watch headlines name real pensions, so for Seller Watch only
the label the model returned is kept, never the headline, the pension or the link. Offer Check data is
synthetic. Output: demo/data/langfuse_examples.json (committed; safe to publish).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "data" / "langfuse_examples.json"


def _client():
    env = dict(os.environ)
    if not env.get("LANGFUSE_PUBLIC_KEY"):
        from dotenv import dotenv_values

        env.update({k: v for k, v in dotenv_values(ROOT / ".env").items() if v})
    host = (env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or "").rstrip("/")
    if not (host and env.get("LANGFUSE_PUBLIC_KEY") and env.get("LANGFUSE_SECRET_KEY")):
        sys.exit("Langfuse keys not found in the shell or .env.")
    auth = (env["LANGFUSE_PUBLIC_KEY"], env["LANGFUSE_SECRET_KEY"])

    def get(path: str, **params):
        for i in range(6):
            r = httpx.get(f"{host}/api/public/{path}", params=params, auth=auth, timeout=60)
            if r.status_code != 429:
                break
            time.sleep(15 * (i + 1))
        r.raise_for_status()
        return r.json()

    return get


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _summary(o: dict, feature: str) -> str:
    name, out, inp = o["name"], o.get("output") or {}, o.get("input") or {}
    if feature == "firstcall":
        if name == "fetch-source":
            return f"query \"{inp.get('query')}\": {out.get('items')} headlines"
        if name == "dedupe-items":
            return f"{inp.get('items')} headlines, {out.get('unique_links')} unique"
        if name == "classify-headline":
            return f"label: {out.get('signal_type')}"   # never the headline: it names a real pension
        if name == "score-pensions":
            v = Counter(x["verdict"] for x in out.get("verdicts", []))
            return ", ".join(f"{n} {k}" for k, n in v.items())
        if name == "run-batch":
            return f"{out.get('fetched', {}).get('google_news')} fetched, {out.get('classified')} classified"
        return ""
    if name == "parse":
        return f"{len(out.get('lines', []))} fund lines read from the email"
    if name == "match":
        return f"{sum(1 for x in out if x.get('resolved'))} of {len(out)} lines matched to a fund"
    if name == "verdict":
        return out.get("verdict", "")
    if name == "validator":
        return "every number sourced" if out.get("passed") else f"{len(out.get('problems', []))} problems"
    if name == "quick-look-offer":
        return f"Project {(inp.get('subject') or '').split(':')[0].replace('Project ', '')}: {out.get('verdict')}"
    if name == "brief":
        return f"{out.get('bytes')} bytes of HTML"
    return ""


def export_trace(get, trace_id: str, feature: str) -> dict:
    t = get(f"traces/{trace_id}")
    obs = sorted(t["observations"], key=lambda o: o["startTime"])
    t0 = _t(obs[0]["startTime"])
    depth: dict[str, int] = {}
    steps = []
    for o in obs:
        d = 0 if not o.get("parentObservationId") else depth.get(o["parentObservationId"], 0) + 1
        depth[o["id"]] = d
        u = o.get("usageDetails") or {}
        steps.append({"name": o["name"], "type": o["type"], "depth": d,
                      "start_ms": round((_t(o["startTime"]) - t0).total_seconds() * 1000),
                      "duration_ms": round((o.get("latency") or 0) * 1000),
                      "model": o.get("model"), "tokens_in": u.get("input"), "tokens_out": u.get("output"),
                      "cost_usd": o.get("calculatedTotalCost"), "prompt_version": (o.get("metadata") or {}).get("prompt_version"),
                      "summary": _summary(o, feature)})
    return {"feature": feature, "name": t["name"], "id": t["id"], "timestamp": t["timestamp"],
            "environment": t.get("environment"), "latency_s": t.get("latency"), "cost_usd": t.get("totalCost"),
            "steps": steps}


def latest(get, name: str, **params) -> str:
    return get("traces", limit=1, name=name, **params)["data"][0]["id"]


def export_experiments(get) -> list[dict]:
    r = get("experiments", fromStartTime="2026-10-01T00:00:00Z", fields="core,metadata,scores", limit=50)
    out = []
    for x in r.get("data", []):
        out.append({"name": x["name"], "dataset_id": x.get("datasetId"), "started": x.get("startTime"),
                    "metadata": {k: v for k, v in (x.get("metadata") or {}).items() if k != "resourceAttributes"},
                    "scores": {s["name"]: s["value"] for s in x.get("scores") or [] if s.get("dataType") == "NUMERIC"}})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ql-trace")
    ap.add_argument("--fc-trace")
    a = ap.parse_args()
    get = _client()
    ql_id = a.ql_trace or latest(get, "quick-look-offer", environment="demo")
    fc_id = a.fc_trace or latest(get, "run-batch", environment="development")
    data = {"exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "Langfuse public API",
            "traces": [export_trace(get, ql_id, "quicklook"), export_trace(get, fc_id, "firstcall")],
            "experiments": export_experiments(get)}
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print(f"Wrote 2 traces and {len(data['experiments'])} experiments to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
