"""Batch path. A job that fetches and processes every N minutes. Same pipeline as the stream path.

Live mode: fetch every source, process all items (upserts make repeats free).
Replay mode: read a source file, process items past the watermark, then advance the watermark.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import pipeline, store, tracing
from .classify.base import Classifier
from .collect import fetch_all, save_landing
from .config import load_queries, rulebook
from .models import RawItem
from .normalize import item_id


def dedupe_links(items: list[RawItem]) -> list[RawItem]:
    """Keep the first copy of each canonical link. Story-level dedupe (near-identical headlines) runs at scoring."""
    seen: set[str] = set()
    out = []
    for r in items:
        k = item_id(r.link)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def run_live_once(db_path, classifier: Classifier, as_of: datetime | None = None, use_gdelt: bool = True) -> dict:
    """One batch run = one trace (when Langfuse keys are set): fetch, dedupe, classify, score."""
    as_of = as_of or datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex[:16]
    tag = f"{classifier.name}:{classifier.version}"
    meta = {"run_id": run_id, "classifier": tag, "rulebook_version": str(rulebook().version), "path": "batch"}
    with tracing.run_trace("run-batch", tags=["first-call", "batch", classifier.name], metadata=meta,
                           input={"as_of": as_of.isoformat(), "gdelt": use_gdelt, "queries": load_queries()}) as root:
        items, stats = fetch_all(use_gdelt=use_gdelt)
        save_landing(items)
        with tracing.observe("dedupe-items", input={"items": len(items)}) as obs:
            unique = dedupe_links(items)
            obs.update(output={"unique_links": len(unique), "dropped": len(items) - len(unique)})
        con = store.connect(db_path)
        try:
            counts = pipeline.process_many(con, unique, classifier, as_of, path="batch")
            verdicts = con.execute("SELECT verdict, count(*) FROM gold_scores WHERE as_of = ? GROUP BY 1",
                                   [as_of.date()]).fetchall()
            summary = {"fetched": stats, **counts, "verdicts": dict(verdicts)}
            root.update(output=summary)
            trace_id = tracing.current_trace_id()
            con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
                        [run_id, as_of, trace_id, tag, json.dumps(summary, default=str)])
        finally:
            con.close()
    tracing.flush()
    return {"run_id": run_id, "traced": bool(trace_id), **summary}


def _watermark(con) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS batch_watermark (source VARCHAR PRIMARY KEY, line INTEGER)")
    row = con.execute("SELECT line FROM batch_watermark WHERE source = 'replay'").fetchone()
    return row[0] if row else 0


def run_file_once(con, source_file: Path, classifier: Classifier, as_of: datetime) -> dict:
    start = _watermark(con)
    text = source_file.read_text() if source_file.exists() else ""
    lines = text.split("\n")[:-1]          # the part after the last newline may be a half-written line
    complete = lines[start:]
    raws = [RawItem.model_validate(json.loads(l)) for l in complete]
    counts = pipeline.process_many(con, raws, classifier, as_of, path="batch") if raws else {}
    # The watermark moves only after the gold write. A crash before this line replays the batch.
    con.execute("INSERT INTO batch_watermark VALUES ('replay', ?) ON CONFLICT DO UPDATE SET line = excluded.line",
                [start + len(complete)])
    return {"read": len(raws), **counts}


def run_file_loop(db_path, source_file: Path, classifier: Classifier, as_of: datetime, every_s: float,
                  stop_file: Path | None = None, idle_ticks: int = 3) -> dict:
    con = store.connect(db_path)
    totals = {"ticks": 0, "read": 0}
    idle = 0
    try:
        while True:
            t0 = time.monotonic()
            r = run_file_once(con, source_file, classifier, as_of)
            totals["ticks"] += 1
            totals["read"] += r.get("read", 0)
            idle = idle + 1 if not r.get("read") else 0
            if stop_file and stop_file.exists() and idle >= idle_ticks:
                break
            time.sleep(max(0.0, every_s - (time.monotonic() - t0)))
    finally:
        con.close()
    return totals
