"""DuckDB store: bronze (as received), silver (typed, deduplicated), gold (signals, scores).

Every write is an upsert on a natural key, so replaying the same input twice changes nothing.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from .config import DEFAULT_DB, rulebook
from .models import Classification, NewsItem, RawItem
from .normalize import near_identical
from .scoring import inputs, score

SCHEMA = """
CREATE TABLE IF NOT EXISTS bronze_items (
  bronze_id VARCHAR PRIMARY KEY, source VARCHAR, query VARCHAR, fetched_at TIMESTAMPTZ, raw JSON);
CREATE TABLE IF NOT EXISTS silver_items (
  item_id VARCHAR PRIMARY KEY, source VARCHAR, canonical_link VARCHAR, headline VARCHAR, outlet VARCHAR,
  published_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ, quote VARCHAR, headline_key VARCHAR,
  pension_id VARCHAR, link_reason VARCHAR);
CREATE TABLE IF NOT EXISTS classified_items (
  item_id VARCHAR PRIMARY KEY, signal_type VARCHAR, confidence DOUBLE, quote VARCHAR, rationale VARCHAR,
  classifier VARCHAR, classified_at TIMESTAMPTZ, completed BOOLEAN DEFAULT false);
CREATE TABLE IF NOT EXISTS gold_signals (
  signal_id VARCHAR PRIMARY KEY, pension_id VARCHAR, signal_type VARCHAR, confidence DOUBLE, weight DOUBLE,
  published_at TIMESTAMPTZ, headline VARCHAR, quote VARCHAR, link VARCHAR, outlet VARCHAR,
  item_id VARCHAR, n_copies INTEGER, rulebook_version INTEGER, completed BOOLEAN DEFAULT false);
CREATE TABLE IF NOT EXISTS gold_scores (
  pension_id VARCHAR, as_of DATE, S DOUBLE, score DOUBLE, verdict VARCHAR, n_signals INTEGER,
  rulebook_version INTEGER, computed_at TIMESTAMPTZ, PRIMARY KEY (pension_id, as_of));
CREATE TABLE IF NOT EXISTS item_latency (
  item_id VARCHAR PRIMARY KEY, available_at TIMESTAMPTZ, verdict_at TIMESTAMPTZ, path VARCHAR);
CREATE TABLE IF NOT EXISTS rejected_rows (
  ref VARCHAR, stage VARCHAR, reason VARCHAR, rejected_at TIMESTAMPTZ, raw JSON);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id VARCHAR PRIMARY KEY, pension_id VARCHAR, action VARCHAR, owner VARCHAR, reason VARCHAR,
  decided_by VARCHAR, decided_at TIMESTAMPTZ, rulebook_version INTEGER, score DOUBLE, verdict VARCHAR,
  trace_id VARCHAR);
CREATE TABLE IF NOT EXISTS runs (
  run_id VARCHAR PRIMARY KEY, started_at TIMESTAMPTZ, trace_id VARCHAR, classifier VARCHAR, summary JSON);
-- Databases made before rulebook v2 lack these columns.
ALTER TABLE classified_items ADD COLUMN IF NOT EXISTS completed BOOLEAN DEFAULT false;
ALTER TABLE gold_signals ADD COLUMN IF NOT EXISTS completed BOOLEAN DEFAULT false;
"""


def connect(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(path or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone = 'UTC'")
    con.execute(SCHEMA)
    return con


def now() -> datetime:
    return datetime.now(timezone.utc)


def bronze_id(raw: RawItem) -> str:
    return hashlib.sha1(f"{raw.source}|{raw.link}|{raw.title}".encode()).hexdigest()[:16]


def insert_bronze(con, raw: RawItem) -> None:
    con.execute(
        "INSERT INTO bronze_items VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [bronze_id(raw), raw.source, raw.query, raw.fetched_at, raw.model_dump_json()],
    )


def reject(con, ref: str, stage: str, reason: str, raw: dict | None = None) -> None:
    con.execute("INSERT INTO rejected_rows VALUES (?, ?, ?, ?, ?)",
                [ref, stage, reason[:500], now(), json.dumps(raw or {}, default=str)])


def upsert_silver(con, item: NewsItem, pension_id: str | None, link_reason: str) -> None:
    # Keep the earliest sighting: fetched_at is the minimum over all deliveries.
    con.execute(
        """INSERT INTO silver_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (item_id) DO UPDATE SET fetched_at = LEAST(silver_items.fetched_at, excluded.fetched_at)""",
        [item.item_id, item.source, str(item.canonical_link), item.headline, item.outlet, item.published_at,
         item.fetched_at, item.quote, item.headline_key, pension_id, link_reason],
    )


def get_classification(con, item_id: str, classifier: str | None = None) -> Classification | None:
    """The stored label. With `classifier`, only a label from that exact adapter and version counts,
    so a rule or prompt change re-labels old items on the next run."""
    row = con.execute("SELECT signal_type, confidence, quote, rationale, completed, classifier FROM classified_items "
                      "WHERE item_id = ?", [item_id]).fetchone()
    if not row or (classifier is not None and row[5] != classifier):
        return None
    return Classification(signal_type=row[0], confidence=row[1], quote=row[2], rationale=row[3] or "",
                          completed=bool(row[4]))


def upsert_classification(con, item_id: str, c: Classification, classifier: str) -> None:
    con.execute(
        """INSERT INTO classified_items (item_id, signal_type, confidence, quote, rationale, classifier, classified_at,
                                         completed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (item_id) DO UPDATE SET signal_type = excluded.signal_type, confidence = excluded.confidence,
             quote = excluded.quote, rationale = excluded.rationale, classifier = excluded.classifier,
             classified_at = excluded.classified_at, completed = excluded.completed""",
        [item_id, c.signal_type, c.confidence, c.quote, c.rationale, classifier, now(), c.completed],
    )


def derive_signals(rows: list[tuple], story_window_days: int) -> list[dict]:
    """Collapse classified items for one pension into signals. Deterministic for a given set.

    rows: (item_id, published_at, headline_key, signal_type, confidence, headline, quote, link, outlet, completed),
    sorted by (published_at, item_id). Rule 1: near-identical headlines within the story window are one
    story; the earliest copy represents it. Rule 2: at most one signal per pension, type, stage and day
    (a closed sale and an open sale on one day stay two signals).
    """
    stories: list[dict] = []
    for r in rows:
        item_id, published_at, key, stype, conf, headline, quote, link, outlet, completed = r
        home = next((s for s in stories if near_identical(s["key"], key)
                     and published_at - s["published_at"] <= timedelta(days=story_window_days)), None)
        if home:
            home["n_copies"] += 1
            continue
        stories.append({"item_id": item_id, "published_at": published_at, "key": key, "signal_type": stype,
                        "confidence": conf, "headline": headline, "quote": quote, "link": link,
                        "outlet": outlet, "n_copies": 1, "completed": bool(completed)})
    best: dict[tuple, dict] = {}
    for s in stories:
        if s["signal_type"] == "none":
            continue
        k = (s["signal_type"], s["completed"], s["published_at"].date())
        cur = best.get(k)
        if cur is None or (s["confidence"], -s["published_at"].timestamp()) > (cur["confidence"], -cur["published_at"].timestamp()):
            if cur is not None:
                s["n_copies"] += cur["n_copies"]
            best[k] = s
        else:
            cur["n_copies"] += s["n_copies"]
    return sorted(best.values(), key=lambda s: (s["published_at"], s["item_id"]))


def rebuild_pension(con, pension_id: str, as_of: datetime) -> dict:
    """Recompute gold signals and the score for one pension. Same function in both paths."""
    rb = rulebook()
    rows = con.execute(
        """SELECT s.item_id, s.published_at, s.headline_key, c.signal_type, c.confidence, s.headline, c.quote,
                  s.canonical_link, s.outlet, coalesce(c.completed, false)
           FROM silver_items s JOIN classified_items c USING (item_id)
           WHERE s.pension_id = ? ORDER BY s.published_at, s.item_id""",
        [pension_id],
    ).fetchall()
    signals = derive_signals(rows, rb.story_window_days)
    con.execute("BEGIN")
    con.execute("DELETE FROM gold_signals WHERE pension_id = ?", [pension_id])
    for s in signals:
        sid = hashlib.sha1(f"{pension_id}|{s['item_id']}".encode()).hexdigest()[:16]
        s["signal_id"] = sid
        con.execute("""INSERT INTO gold_signals (signal_id, pension_id, signal_type, confidence, weight, published_at,
                         headline, quote, link, outlet, item_id, n_copies, rulebook_version, completed)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [sid, pension_id, s["signal_type"], s["confidence"], rb.weights[s["signal_type"]],
                     s["published_at"], s["headline"], s["quote"], s["link"], s["outlet"], s["item_id"],
                     s["n_copies"], rb.version, s["completed"]])
    sc = score(inputs(signals), as_of, rb)
    con.execute(
        """INSERT INTO gold_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (pension_id, as_of) DO UPDATE SET S = excluded.S, score = excluded.score,
             verdict = excluded.verdict, n_signals = excluded.n_signals,
             rulebook_version = excluded.rulebook_version, computed_at = excluded.computed_at""",
        [pension_id, as_of.date(), sc.S, sc.score, sc.verdict, len(sc.contributions), rb.version, now()],
    )
    con.execute("COMMIT")
    return {"pension_id": pension_id, "score": sc.score, "verdict": sc.verdict, "n_signals": len(sc.contributions)}


def record_latency(con, item_ids: list[str], available: dict[str, datetime], path: str) -> None:
    t = now()
    for iid in item_ids:
        con.execute("INSERT INTO item_latency VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    [iid, available.get(iid, t), t, path])


def load_signals(con, pension_id: str) -> list[dict]:
    cur = con.execute("SELECT * FROM gold_signals WHERE pension_id = ? ORDER BY published_at", [pension_id])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
