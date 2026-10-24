"""Bronze to silver: parse, normalise, validate. Pure functions, no I/O."""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import QUOTE_MAX, NewsItem, RawItem

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|cmpid|ref$|oc$)", re.IGNORECASE)
_STOP = {
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "at", "by", "with", "as",
    "is", "its", "it", "from", "after", "over", "into", "new", "s",
}


def canonical_link(url: str) -> str:
    """Lower-case host, drop tracking params and fragments, strip trailing slash."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower().removeprefix("www."), path, urlencode(query), ""))


def split_outlet(title: str, outlet: str | None) -> tuple[str, str | None]:
    """Google News titles end with ' - Outlet'. Strip it so the outlet name never links or classifies."""
    title = html.unescape(title).strip()
    if outlet and title.endswith(f" - {outlet}"):
        return title[: -len(outlet) - 3].strip(), outlet
    if " - " in title and outlet is None:
        head, _, tail = title.rpartition(" - ")
        if 0 < len(tail) <= 60:
            return head.strip(), tail.strip()
    return title, outlet


def tokens(headline: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", headline.lower())
    return [w for w in words if w not in _STOP]


def headline_key(headline: str) -> str:
    return " ".join(sorted(set(tokens(headline))))


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def near_identical(key_a: str, key_b: str, threshold: float = 0.6) -> bool:
    return key_a == key_b or jaccard(key_a, key_b) >= threshold


def parse_time(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    value = value.strip()
    for parser in (_rfc822, _gdelt, _iso):
        try:
            dt = parser(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return fallback


def _rfc822(v: str) -> datetime:
    return parsedate_to_datetime(v)


def _gdelt(v: str) -> datetime:
    return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _iso(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def item_id(link: str) -> str:
    return hashlib.sha1(canonical_link(link).encode()).hexdigest()[:16]


def clip(text: str, n: int = QUOTE_MAX) -> str:
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def to_silver(raw: RawItem) -> NewsItem:
    """Raises pydantic.ValidationError when the row breaks the silver contract."""
    headline, outlet = split_outlet(raw.title, raw.outlet)
    # Quote: the headline, plus the source snippet when it adds text. Never the article body.
    snippet = clip(raw.summary or "")
    quote = headline if not snippet or headline_key(snippet).startswith(headline_key(headline)) or headline in snippet else f"{headline}. {snippet}"
    return NewsItem(
        item_id=item_id(raw.link),
        source=raw.source,
        canonical_link=canonical_link(raw.link),
        headline=headline,
        outlet=outlet,
        published_at=parse_time(raw.published, raw.fetched_at),
        fetched_at=raw.fetched_at,
        quote=clip(quote),
        headline_key=headline_key(headline),
    )
