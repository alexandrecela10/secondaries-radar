"""Replay producer. Emits stored items in event-time order at an accelerated rate, to the stream
topic and to the batch source file at the same moment, so both paths see identical input."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import RawItem
from .normalize import parse_time


def event_order(items: list[RawItem]) -> list[RawItem]:
    return sorted(items, key=lambda r: (parse_time(r.published, r.fetched_at), r.link))


def emit(items: list[RawItem], rate_per_s: float, log_=None, source_file: Path | None = None,
         shuffle_seed: int | None = None, topic: str | None = None) -> int:
    from .stream.log import TOPIC_RAW
    from .stream.poller import event_key

    topic = topic or TOPIC_RAW
    ordered = event_order(items)
    if shuffle_seed is not None:      # out-of-order delivery test
        import random

        random.Random(shuffle_seed).shuffle(ordered)
    prod = log_.producer() if log_ else None
    if log_:
        log_.ensure_topic(topic)
    fh = source_file.open("a") if source_file else None
    gap = 1.0 / rate_per_s if rate_per_s > 0 else 0.0
    t0 = time.monotonic()
    for i, raw in enumerate(ordered):
        target = t0 + i * gap
        if (d := target - time.monotonic()) > 0:
            time.sleep(d)
        ev = raw.model_copy(update={"fetched_at": datetime.now(timezone.utc)})   # processing time = emit time
        payload = ev.model_dump(mode="json")
        if prod:
            prod.send(topic, event_key(ev), payload)
        if fh:
            fh.write(json.dumps(payload) + "\n")
            fh.flush()
    if prod:
        prod.flush()
    if fh:
        fh.close()
    return len(ordered)
