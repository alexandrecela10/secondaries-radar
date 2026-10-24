"""Classifier and linker evals on the labelled real headlines (PRD C-3)."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from . import pensions
from .classify.base import Classifier
from .config import FIXTURES_DIR


def load_labels(split: str | None = None) -> list[dict]:
    rows = [json.loads(l) for l in (FIXTURES_DIR / "labelled_headlines.jsonl").read_text().splitlines() if l.strip()]
    return [r for r in rows if split in (None, r["split"])]


@dataclass
class Report:
    n: int
    precision: float          # of items flagged as a signal, share with the right type
    recall: float             # of labelled signals, share caught with the right type
    flagged: int
    labelled_signals: int
    link_accuracy: float
    per_type: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def evaluate(classifier: Classifier, split: str | None = None) -> Report:
    rows = load_labels(split)
    tp = flagged = positives = link_ok = 0
    per = {t: Counter() for t in {r["expected_signal"] for r in rows} | {"sale_or_advisor"}}
    errors = []
    for r in rows:
        got_link = pensions.link(r["headline"]).pension_id
        link_ok += got_link == r["expected_pension"]
        pred = classifier.classify(r["headline"], None).signal_type
        exp = r["expected_signal"]
        per.setdefault(pred, Counter())
        if pred != "none":
            flagged += 1
            per[pred]["flagged"] += 1
        if exp != "none":
            positives += 1
            per[exp]["labelled"] += 1
        if pred == exp and exp != "none":
            tp += 1
            per[exp]["correct"] += 1
        if pred != exp:
            errors.append({"n": r["n"], "headline": r["headline"], "expected": exp, "predicted": pred})
    return Report(
        n=len(rows),
        precision=tp / flagged if flagged else 0.0,
        recall=tp / positives if positives else 0.0,
        flagged=flagged,
        labelled_signals=positives,
        link_accuracy=link_ok / len(rows) if rows else 0.0,
        per_type={k: dict(v) for k, v in per.items() if v},
        errors=errors,
    )
