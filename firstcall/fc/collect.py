"""Fetch every configured query from both sources. Writes the raw payload to data/bronze/ as received."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import BRONZE_DIR, load_queries
from .models import RawItem
from .sources import gdelt, google_news

log = logging.getLogger(__name__)


def _save_raw(source: str, query: str, body: bytes) -> None:
    ts = datetime.now(timezone.utc)
    d = BRONZE_DIR / source / ts.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in query)[:60]
    ext = "xml" if source == "google_news" else "json"
    (d / f"{ts.strftime('%H%M%S')}_{safe}.{ext}").write_bytes(body)


def fetch_all(use_gdelt: bool = True) -> tuple[list[RawItem], dict]:
    queries = load_queries()
    items: list[RawItem] = []
    stats = {"google_news": 0, "gdelt": 0, "errors": []}
    for q in queries["google_news"]:
        try:
            body, got = google_news.fetch(q)
            _save_raw("google_news", q, body)
            items += got
            stats["google_news"] += len(got)
        except Exception as exc:  # one bad feed must not stop the run
            stats["errors"].append(f"google_news '{q}': {exc}")
    if use_gdelt:
        for q in queries["gdelt"]:
            try:
                body, got = gdelt.fetch(q)
                _save_raw("gdelt", q, body)
                items += got
                stats["gdelt"] += len(got)
            except Exception as exc:
                stats["errors"].append(f"gdelt '{q}': {exc}")
    for e in stats["errors"]:
        log.warning(e)
    return items, stats


def items_to_jsonl(items: list[RawItem]) -> str:
    return "\n".join(json.dumps(i.model_dump(mode="json")) for i in items)


def save_landing(items: list[RawItem]) -> str:
    """Append fetched items to data/landing/ so any run can be replayed later."""
    from .config import DATA_DIR

    d = DATA_DIR / "landing"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"fetch_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.jsonl"
    path.write_text(items_to_jsonl(items) + "\n")
    return str(path)


def load_landing() -> list[RawItem]:
    from .config import DATA_DIR

    out = []
    for p in sorted((DATA_DIR / "landing").glob("*.jsonl")):
        out += [RawItem.model_validate_json(line) for line in p.read_text().splitlines() if line.strip()]
    return out
