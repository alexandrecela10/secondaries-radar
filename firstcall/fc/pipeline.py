"""Shared processing code. The batch job and the stream consumer both call these functions,
so any difference between the two paths comes from collection, not from processing.

Steps per item: bronze -> silver (contract) -> link -> classify (once per item) -> gold for the pension.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from . import pensions, store
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
    cached = store.get_classification(con, item.item_id)
    if cached is not None:      # redelivery: no second model call, no second row
        return Outcome(item.item_id, linked.pension_id, "cached", cached.signal_type)
    result = classifier.classify(item.headline, pensions.ALL[linked.pension_id].name)
    store.upsert_classification(con, item.item_id, result, f"{classifier.name}:{classifier.version}")
    return Outcome(item.item_id, linked.pension_id, "classified", result.signal_type)


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
    for pid in sorted(touched):
        store.rebuild_pension(con, pid, as_of)
    store.record_latency(con, list(available), available, path)
    counts["pensions_rebuilt"] = len(touched)
    return counts


def rebuild_all(con, as_of: datetime) -> int:
    pids = [r[0] for r in con.execute(
        "SELECT DISTINCT pension_id FROM silver_items WHERE pension_id IS NOT NULL ORDER BY 1").fetchall()]
    for pid in pids:
        store.rebuild_pension(con, pid, as_of)
    return len(pids)
