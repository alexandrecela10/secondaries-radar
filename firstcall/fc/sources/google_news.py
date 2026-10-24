"""Google News RSS search feeds. Headline, link, outlet, time only."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser

from ..models import RawItem
from .http import Throttle, client

log = logging.getLogger(__name__)
BASE = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
_throttle = Throttle(1.0)


def feed_url(query: str) -> str:
    return BASE.format(q=quote_plus(query))


def parse_feed(xml: bytes | str, query: str, fetched_at: datetime) -> list[RawItem]:
    parsed = feedparser.parse(xml)
    items = []
    for e in parsed.entries:
        outlet = (e.get("source") or {}).get("title")
        items.append(
            RawItem(
                source="google_news",
                query=query,
                title=e.get("title", ""),
                link=e.get("link", ""),
                outlet=outlet,
                published=e.get("published"),
                summary=None,  # Google's description repeats the headline as HTML; nothing to add
                fetched_at=fetched_at,
            )
        )
    return items


def fetch(query: str) -> tuple[bytes, list[RawItem]]:
    _throttle.wait()
    with client() as c:
        r = c.get(feed_url(query))
        r.raise_for_status()
    now = datetime.now(timezone.utc)
    return r.content, parse_feed(r.content, query, now)
