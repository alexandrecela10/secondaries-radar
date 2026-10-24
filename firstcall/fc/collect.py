"""Fetch every configured query from both sources. Writes the raw payload to data/bronze/ as received."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import BRONZE_DIR, load_queries
from .models import RawItem
from . import tracing
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
    plan = [("google_news", google_news, q) for q in queries["google_news"]]
    if use_gdelt:
        plan += [("gdelt", gdelt, q) for q in queries["gdelt"]]
    for name, source, q in plan:
        with tracing.observe("fetch-source", as_type="retriever", input={"source": name, "query": q}) as obs:
            try:
                body, got = source.fetch(q)
                _save_raw(name, q, body)
                items += got
                stats[name] += len(got)
                obs.update(output={"items": len(got)}, metadata={"source": name})
            except Exception as exc:  # one bad feed must not stop the run
                stats["errors"].append(f"{name} '{q}': {exc}")
                obs.update(output={"items": 0}, level="ERROR", status_message=str(exc)[:200],
                           metadata={"source": name})
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
