"""Shared processing code. The batch job and the stream consumer both call these functions,
so any difference between the two paths comes from collection, not from processing.

Steps per item: bronze -> silver (contract) -> link -> classify (once per item) -> gold for the pension.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from . import pensions, store, tracing
from .classify.base import Classifier, ClassifierError
from .models import RawItem
from .normalize import to_silver

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    item_id: str | None
    pension_id: str | None
    status: str          # rejected | unlinked | classified | cached
    signal_type: str | None = None


def ingest(con, raw: RawItem, classifier: Classifier) -> Outcome:
    """Bronze to classified. Raises ClassifierError (the caller decides: DLQ or rejected)."""
    store.insert_bronze(con, raw)
    try:
        item = to_silver(raw)
    except ValidationError as exc:
        store.reject(con, store.bronze_id(raw), "silver", str(exc), raw.model_dump(mode="json"))
        return Outcome(None, None, "rejected")
    linked = pensions.link(item.headline)
    store.upsert_silver(con, item, linked.pension_id, linked.reason)
    if linked.pension_id is None:
        return Outcome(item.item_id, None, "unlinked")
    tag = f"{classifier.name}:{classifier.version}"
    cached = store.get_classification(con, item.item_id, tag)
    if cached is not None:      # redelivery: no second model call, no second row
        return Outcome(item.item_id, linked.pension_id, "cached", cached.signal_type)
    result = _classify_traced(classifier, item, linked.pension_id, tag)
    store.upsert_classification(con, item.item_id, result, tag)
    return Outcome(item.item_id, linked.pension_id, "classified", result.signal_type)


def _classify_traced(classifier: Classifier, item, pension_id: str, tag: str):
    """One classified item = one observation: a generation for the model, a span for keyword rules."""
    is_model = classifier.name == "claude"
    extra = {"model": classifier.model, "model_parameters": classifier.model_parameters} if is_model else {}
    meta = {"classifier": tag, "prompt_version": getattr(classifier, "prompt_version", classifier.version),
            "item_id": item.item_id, "pension_id": pension_id, "outlet": item.outlet or "",
            "published_at": item.published_at.isoformat(), "link": str(item.canonical_link)}
    with tracing.observe("classify-headline", as_type="generation" if is_model else "span",
                         input={"headline": item.headline, "pension": pensions.ALL[pension_id].name},
                         metadata=meta, **extra) as obs:
        try:
            result = classifier.classify(item.headline, pensions.ALL[pension_id].name)
        except ClassifierError as exc:
            obs.update(level="ERROR", status_message=str(exc)[:200])
            raise
        usage = getattr(classifier, "last_usage", None)
        obs.update(output=result.model_dump(), **({"usage_details": usage} if is_model and usage else {}))
    return result


def process_one(con, raw: RawItem, classifier: Classifier, as_of: datetime, path: str) -> Outcome:
    """Stream path unit of work: one item, then the gold rows for its pension."""
    out = ingest(con, raw, classifier)
    if out.pension_id:
        store.rebuild_pension(con, out.pension_id, as_of)
    if out.item_id:
        store.record_latency(con, [out.item_id], {out.item_id: raw.fetched_at}, path)
    return out


def process_many(con, raws: list[RawItem], classifier: Classifier, as_of: datetime, path: str) -> dict:
    """Batch path unit of work: all items, then gold once per touched pension."""
    touched: set[str] = set()
    counts: dict[str, int] = {}
    failed: list[tuple[RawItem, str]] = []
    available: dict[str, datetime] = {}
    for raw in raws:
        try:
            out = ingest(con, raw, classifier)
        except ClassifierError as exc:
            store.reject(con, store.bronze_id(raw), "classify", str(exc), raw.model_dump(mode="json"))
            failed.append((raw, str(exc)))
            counts["failed"] = counts.get("failed", 0) + 1
            continue
        counts[out.status] = counts.get(out.status, 0) + 1
        if out.pension_id:
            touched.add(out.pension_id)
        if out.item_id:
            available.setdefault(out.item_id, raw.fetched_at)
    with tracing.observe("score-pensions", input={"pensions": sorted(touched), "as_of": as_of.isoformat()}) as obs:
        verdicts = [store.rebuild_pension(con, pid, as_of) for pid in sorted(touched)]
        obs.update(output={"verdicts": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
                                        for r in verdicts]})
    store.record_latency(con, list(available), available, path)
    counts["pensions_rebuilt"] = len(touched)
    return counts


def rebuild_all(con, as_of: datetime) -> int:
    pids = [r[0] for r in con.execute(
        "SELECT DISTINCT pension_id FROM silver_items WHERE pension_id IS NOT NULL ORDER BY 1").fetchall()]
    for pid in pids:
        store.rebuild_pension(con, pid, as_of)
    return len(pids)
