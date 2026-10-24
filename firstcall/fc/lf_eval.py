"""Classifier and linker experiment on the 130 labelled real headlines (PRD C-3), in Langfuse.

Dataset `firstcall/labelled-headlines`: one item per headline.
  input            {"headline", "quote"}            what the pipeline sees (the quote is the headline)
  expected_output  {"signal_type", "pension_id"}    agent-labelled, PM to review
  metadata         {"n", "split", "outlet", "published_at", "link", "label_status"}

Experiment: one run per classifier and split (dev, holdout). Item scores: type_correct, pension_correct.
Run scores: precision, recall, link_accuracy (same definitions as fc/evals.py, the pytest gate).
Without Langfuse keys, or with --no-upload, the same loop runs locally. Either way the numbers are written to
measurements/eval_<classifier>.json.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import pensions, tracing
from .classify.base import Classifier, ClassifierError, get_classifier
from .config import MEASURE_DIR
from .evals import load_labels

DATASET = "firstcall/labelled-headlines"
SPLITS = ("dev", "holdout")


def item_of(row: dict) -> dict:
    return {"id": f"fc-headline-{row['n']:03d}",
            "input": {"headline": row["headline"], "quote": row["headline"][:300]},
            "expected_output": {"signal_type": row["expected_signal"], "pension_id": row["expected_pension"]},
            "metadata": {"n": row["n"], "split": row["split"], "outlet": row.get("outlet"),
                         "published_at": row.get("published_at"), "link": row["link"],
                         "label_status": row["label_status"]}}


def predict(clf_factory, headline: str) -> dict:
    """Link, then classify with the linked pension's name, as the pipeline does."""
    pid = pensions.link(headline).pension_id
    clf: Classifier = clf_factory()
    is_model = clf.name == "claude"
    extra = {"model": clf.model, "model_parameters": clf.model_parameters} if is_model else {}
    with tracing.observe("classify-headline", as_type="generation" if is_model else "span",
                         input={"headline": headline, "pension": pensions.ALL[pid].name if pid else None},
                         metadata={"classifier": f"{clf.name}:{clf.version}",
                                   "prompt_version": getattr(clf, "prompt_version", clf.version)}, **extra) as obs:
        try:
            c = clf.classify(headline, pensions.ALL[pid].name if pid else None)
            out = {"signal_type": c.signal_type, "pension_id": pid, "completed": c.completed,
                   "confidence": c.confidence}
        except ClassifierError as exc:
            obs.update(level="ERROR", status_message=str(exc)[:200])
            out = {"signal_type": "error", "pension_id": pid, "completed": False, "confidence": 0.0,
                   "error": str(exc)[:200]}
        usage = getattr(clf, "last_usage", None)
        obs.update(output=out, **({"usage_details": usage} if is_model and usage else {}))
    if is_model and getattr(clf, "last_usage", None):
        out["usage"] = clf.last_usage
    return out


def metrics(pairs: list[tuple[dict, dict]]) -> dict:
    """pairs: (expected, output). Precision: of items flagged as a signal, share with the right type.
    Recall: of labelled signals, share caught with the right type."""
    tp = flagged = positives = link_ok = 0
    for exp, out in pairs:
        pred, want = out["signal_type"], exp["signal_type"]
        flagged += pred not in ("none", "error")
        positives += want != "none"
        tp += pred == want and want != "none"
        link_ok += out["pension_id"] == exp["pension_id"]
    n = len(pairs)
    return {"n": n, "precision": tp / flagged if flagged else 0.0, "recall": tp / positives if positives else 0.0,
            "link_accuracy": link_ok / n if n else 0.0, "flagged": flagged, "labelled_signals": positives,
            "errors": sum(o["signal_type"] == "error" for _, o in pairs)}


def type_correct(*, output, expected_output, **_):
    from langfuse import Evaluation

    ok = output["signal_type"] == expected_output["signal_type"]
    return Evaluation(name="type_correct", value=1.0 if ok else 0.0,
                      comment=None if ok else f"expected {expected_output['signal_type']}, got {output['signal_type']}")


def pension_correct(*, output, expected_output, **_):
    from langfuse import Evaluation

    ok = output["pension_id"] == expected_output["pension_id"]
    return Evaluation(name="pension_correct", value=1.0 if ok else 0.0,
                      comment=None if ok else f"expected {expected_output['pension_id']}, got {output['pension_id']}")


def run_level(*, item_results, **_):
    from langfuse import Evaluation

    def exp(it):
        e = it.item.expected_output if hasattr(it.item, "expected_output") else it.item["expected_output"]
        return e

    m = metrics([(exp(r), r.output) for r in item_results if isinstance(r.output, dict)])
    return [Evaluation(name="precision", value=m["precision"], comment=f"flagged {m['flagged']}"),
            Evaluation(name="recall", value=m["recall"], comment=f"labelled signals {m['labelled_signals']}"),
            Evaluation(name="link_accuracy", value=m["link_accuracy"])]


def ensure_dataset(lf, rows: list[dict]) -> None:
    lf.create_dataset(name=DATASET, description="130 real public headlines about US public pensions, "
                      "labelled with signal type and pension. Agent-labelled, PM to review.",
                      metadata={"source": "firstcall/fixtures/labelled_headlines.jsonl", "labels": "agent, PM to review"})
    for r in rows:
        it = item_of(r)
        lf.create_dataset_item(dataset_name=DATASET, id=it["id"], input=it["input"],
                               expected_output=it["expected_output"], metadata=it["metadata"])
    lf.flush()


def _factory(kind: str):
    if kind == "claude":
        from .classify.claude import ClaudeClassifier, new_client

        shared = new_client()
        return lambda: ClaudeClassifier(shared)      # one adapter per item: token counts never mix across threads
    clf = get_classifier(kind)
    return lambda: clf


def main(kind: str = "keyword", upload: bool = True) -> dict:
    kind = "keyword" if kind == "auto" and get_classifier("auto").name == "keyword" else (
        "claude" if kind == "auto" else kind)
    rows = load_labels()
    factory = _factory(kind)
    version = factory().version
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    if not upload:
        import os

        os.environ["FC_TRACING"] = "0"
    lf = tracing.client()
    result: dict = {"classifier": f"{kind}:{version}", "run_at": stamp, "langfuse": bool(lf), "splits": {}}
    if lf is not None:
        ensure_dataset(lf, rows)
        dataset = lf.get_dataset(DATASET)
        for split in SPLITS:
            items = [i for i in dataset.items if (i.metadata or {}).get("split") == split]
            res = lf.run_experiment(
                name=f"classifier-{kind}", run_name=f"{kind} {version} {split} {stamp}",
                description=f"First Call headline classifier and linker, {split} split",
                data=items, task=lambda *, item, **_: predict(factory, item.input["headline"]),
                evaluators=[type_correct, pension_correct], run_evaluators=[run_level],
                max_concurrency=4 if kind == "claude" else 10,
                metadata={"classifier": kind, "version": version, "split": split})
            pairs = [(r.item.expected_output, r.output) for r in res.item_results if isinstance(r.output, dict)]
            result["splits"][split] = {**metrics(pairs), "dataset_run_url": res.dataset_run_url,
                                       "items": _items(res.item_results)}
        lf.flush()
    else:
        for split in SPLITS:
            sub = [r for r in rows if r["split"] == split]
            with ThreadPoolExecutor(4 if kind == "claude" else 1) as ex:
                outs = list(ex.map(lambda r: predict(factory, r["headline"]), sub))
            pairs = [(item_of(r)["expected_output"], o) for r, o in zip(sub, outs)]
            result["splits"][split] = {**metrics(pairs), "dataset_run_url": None,
                                       "items": [{"n": r["n"], "headline": r["headline"], "expected": e, "output": o}
                                                 for r, (e, o) in zip(sub, pairs)]}
    all_pairs = [(i["expected"], i["output"]) for s in result["splits"].values() for i in s["items"]]
    result["all"] = metrics(all_pairs)
    tokens = [i["output"].get("usage") for s in result["splits"].values() for i in s["items"] if i["output"].get("usage")]
    if tokens:
        result["tokens"] = {"input": sum(t["input"] for t in tokens), "output": sum(t["output"] for t in tokens)}
    MEASURE_DIR.mkdir(exist_ok=True)
    out = MEASURE_DIR / f"eval_{kind}.json"
    out.write_text(json.dumps(result, indent=1, default=str))
    for split, m in [*result["splits"].items(), ("all", result["all"])]:
        print(f"{kind:<8} {split:<8} n={m['n']:<4} precision {m['precision']:.2f}  recall {m['recall']:.2f}  "
              f"link {m['link_accuracy']:.2f}  errors {m['errors']}"
              + (f"  {m['dataset_run_url']}" if m.get("dataset_run_url") else ""))
    print(f"written: {out}" + ("" if lf else "  (local run: Langfuse off or --no-upload)"))
    return result


def _items(item_results) -> list[dict]:
    out = []
    for r in item_results:
        md = r.item.metadata or {}
        out.append({"n": md.get("n"), "headline": r.item.input["headline"], "expected": r.item.expected_output,
                    "output": r.output, "trace_id": r.trace_id})
    return out
