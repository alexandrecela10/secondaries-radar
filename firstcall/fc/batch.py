"""Batch path. A job that fetches and processes every N minutes. Same pipeline as the stream path.

Live mode: fetch every source, process all items (upserts make repeats free).
Replay mode: read a source file, process items past the watermark, then advance the watermark.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import pipeline, store
from .classify.base import Classifier
from .collect import fetch_all, save_landing
from .models import RawItem


def run_live_once(db_path, classifier: Classifier, as_of: datetime | None = None, use_gdelt: bool = True) -> dict:
    items, stats = fetch_all(use_gdelt=use_gdelt)
    save_landing(items)
    con = store.connect(db_path)
    try:
        counts = pipeline.process_many(con, items, classifier, as_of or datetime.now(timezone.utc), path="batch")
    finally:
        con.close()
    return {"fetched": stats, **counts}


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
