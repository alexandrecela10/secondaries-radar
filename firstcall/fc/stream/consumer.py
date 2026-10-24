"""Real-time path, consumer side. One consumer group, manual commits, idempotent sink.

Loop: poll -> process (shared pipeline) -> commit. A classifier failure goes to the dead-letter
topic and is committed, so one bad item never blocks the partition. Any other error stops the
consumer without committing, so the record is replayed after restart (at-least-once).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .. import pipeline, store
from ..classify.base import Classifier, ClassifierError
from ..models import RawItem
from .log import TOPIC_DLQ, TOPIC_RAW, Log

log = logging.getLogger(__name__)
GROUP = "firstcall-classifier"


def run(log_: Log, db_path, classifier: Classifier, as_of: datetime | None = None, group: str = GROUP,
        idle_exit: float | None = None, max_records: int | None = None, lag_trace: list | None = None,
        topic: str = TOPIC_RAW, dlq_topic: str = TOPIC_DLQ) -> dict:
    log_.ensure_topic(topic)
    log_.ensure_topic(dlq_topic, partitions=1)
    con = store.connect(db_path)
    consumer = log_.consumer(topic, group)
    dlq = log_.producer()
    stats = {"processed": 0, "dlq": 0, "first_record_s": None}
    started = idle_since = time.monotonic()
    try:
        while True:
            rec = consumer.poll(0.5)
            if rec is None:
                if idle_exit is not None and time.monotonic() - idle_since > idle_exit:
                    break
                continue
            idle_since = time.monotonic()
            if stats["first_record_s"] is None:   # on Redpanda this includes the group rebalance
                stats["first_record_s"] = round(idle_since - started, 2)
            raw = RawItem.model_validate(rec.value)
            try:
                pipeline.process_one(con, raw, classifier, as_of or datetime.now(timezone.utc), path="stream")
            except ClassifierError as exc:
                dlq.send(dlq_topic, rec.key, {"error": str(exc), "record": rec.value,
                                              "source_offset": [rec.partition, rec.offset]})
                dlq.flush()
                stats["dlq"] += 1
            consumer.commit(rec)          # commit after the sink write: at-least-once
            stats["processed"] += 1
            if lag_trace is not None and stats["processed"] % 10 == 0:
                lag_trace.append((time.monotonic(), consumer.lag()))
            if max_records and stats["processed"] >= max_records:
                break
    finally:
        consumer.close()
        con.close()
    return stats


def redrive_dlq(log_: Log, group: str = "firstcall-dlq-redrive", topic: str = TOPIC_RAW,
                dlq_topic: str = TOPIC_DLQ) -> int:
    """Send dead-lettered records back to the raw topic, for example after an API outage."""
    c = log_.consumer(dlq_topic, group)
    p = log_.producer()
    n = 0
    while (rec := c.poll(1.0)) is not None:
        p.send(topic, rec.key, rec.value["record"])
        c.commit(rec)
        n += 1
    p.flush()
    c.close()
    return n
