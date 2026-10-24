"""Real-time path, producer side. Polls the sources and publishes each new item as one event.

Key = linked pension id, or 'unlinked'. All events for one pension land on one partition,
so the consumer sees them in order and can keep per-pension state.
"""
from __future__ import annotations

import json
import logging
import time

from .. import pensions
from ..collect import fetch_all
from ..config import DATA_DIR, RSS_POLL_MINUTES
from ..models import RawItem
from ..normalize import item_id, split_outlet
from .log import TOPIC_RAW, Log

log = logging.getLogger(__name__)
SEEN = DATA_DIR / "poller_seen.json"


def event_key(raw: RawItem) -> str:
    headline, _ = split_outlet(raw.title, raw.outlet)
    return pensions.link(headline).pension_id or "unlinked"


def publish(log_: Log, items: list[RawItem], seen: set[str]) -> int:
    prod = log_.producer()
    n = 0
    for raw in items:
        iid = item_id(raw.link)
        if iid in seen:
            continue
        prod.send(TOPIC_RAW, event_key(raw), raw.model_dump(mode="json"))
        seen.add(iid)
        n += 1
    prod.flush()
    return n


def run(log_: Log, every_minutes: float = RSS_POLL_MINUTES, once: bool = False, use_gdelt: bool = True) -> None:
    log_.ensure_topic(TOPIC_RAW)
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    while True:
        items, stats = fetch_all(use_gdelt=use_gdelt)
        n = publish(log_, items, seen)
        SEEN.write_text(json.dumps(sorted(seen)))   # after publish: a crash here re-sends, the sink dedupes
        log.info("poll: fetched %s, published %s new", len(items), n)
        print(f"poll: fetched {len(items)}, published {n} new events to {TOPIC_RAW} ({log_.name})", flush=True)
        if once:
            return
        time.sleep(every_minutes * 60)
