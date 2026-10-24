"""GDELT DOC 2.0 API, artlist mode. GDELT allows one request every 5 seconds.

We wait at least 5 s between calls and back off on any refusal (HTTP 429 or the plain-text
"Please limit requests" reply). We never retry more than 3 times per query.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from ..config import GDELT_MIN_INTERVAL_S
from ..models import RawItem
from .http import Throttle, client

log = logging.getLogger(__name__)
API = "https://api.gdeltproject.org/api/v2/doc/doc"
_throttle = Throttle(GDELT_MIN_INTERVAL_S)


class GdeltRefused(RuntimeError):
    pass


def parse_artlist(payload: dict, query: str, fetched_at: datetime) -> list[RawItem]:
    out = []
    for a in payload.get("articles", []):
        if a.get("language") not in (None, "English"):
            continue
        out.append(
            RawItem(
                source="gdelt",
                query=query,
                title=a.get("title", ""),
                link=a.get("url", ""),
                outlet=a.get("domain"),
                published=a.get("seendate"),
                summary=None,
                fetched_at=fetched_at,
            )
        )
    return out


def fetch(query: str, timespan: str = "2w", maxrecords: int = 75, retries: int = 3) -> tuple[bytes, list[RawItem]]:
    params = {"query": f"{query} sourcelang:english", "mode": "artlist", "format": "json",
              "maxrecords": str(maxrecords), "timespan": timespan, "sort": "datedesc"}
    delay = 10.0
    for attempt in range(retries):
        _throttle.wait()
        with client() as c:
            r = c.get(API, params=params)
        body = r.content
        if r.status_code == 429 or body.lstrip().startswith(b"Please limit"):
            log.warning("GDELT refused (attempt %s); backing off %.0fs", attempt + 1, delay)
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
        try:
            payload = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError as exc:
            raise GdeltRefused(f"non-JSON reply: {body[:120]!r}") from exc
        return body, parse_artlist(payload, query, datetime.now(timezone.utc))
    raise GdeltRefused("GDELT refused after retries")
