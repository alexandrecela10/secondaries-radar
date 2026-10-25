"""DuckDB layers. bronze: the email as received. silver: parsed and validated rows.
gold: matches, metrics, verdicts, briefs. Reference data is read from parquet views.

Writes for one offer happen in one transaction, after deleting that offer's old
rows, so a re-run replaces and never duplicates.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb

from .config import Workspace

SCHEMA = """
CREATE TABLE IF NOT EXISTS bronze_emails (
    message_id VARCHAR PRIMARY KEY, sha256 VARCHAR, raw_path VARCHAR, channel VARCHAR,
    received_at TIMESTAMP, status VARCHAR, error VARCHAR, processed_at TIMESTAMP, attempts INTEGER);
CREATE TABLE IF NOT EXISTS review_queue (
    message_id VARCHAR PRIMARY KEY, reason VARCHAR, logged_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS silver_offers (
    offer_id VARCHAR PRIMARY KEY, message_id VARCHAR, code_name VARCHAR, seller_type VARCHAR,
    reference_date DATE, email_date TIMESTAMP, sender VARCHAR, currency VARCHAR, total_value DOUBLE,
    asking_price_text VARCHAR, bid_deadline DATE, parser VARCHAR, parser_version VARCHAR, parser_offline BOOLEAN);
CREATE TABLE IF NOT EXISTS silver_offer_lines (
    line_id VARCHAR PRIMARY KEY, offer_id VARCHAR, line_no INTEGER, label VARCHAR, description VARCHAR,
    strategy VARCHAR, region VARCHAR, vintage INTEGER, size_band VARCHAR, currency VARCHAR,
    commitment DOUBLE, pct_called DOUBLE, dpi DOUBLE, tvpi DOUBLE, value DOUBLE, unfunded DOUBLE,
    name_fragment VARCHAR);
CREATE TABLE IF NOT EXISTS silver_rejects (
    row_id VARCHAR PRIMARY KEY, offer_id VARCHAR, line_ref VARCHAR, reason VARCHAR, raw VARCHAR, logged_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS gold_line_matches (
    line_id VARCHAR PRIMARY KEY, offer_id VARCHAR, line_no INTEGER, label VARCHAR, tier VARCHAR,
    fund_id VARCHAR, fund_name VARCHAR, manager_id VARCHAR, manager_name VARCHAR, confidence DOUBLE,
    resolved BOOLEAN, method VARCHAR, candidates_json VARCHAR, flags_json VARCHAR, value_usd DOUBLE,
    fx_rate DOUBLE, fx_row_id VARCHAR, price_low DOUBLE, price_high DOUBLE, price_method VARCHAR,
    our_value_row VARCHAR, compared_series_row VARCHAR, match_version VARCHAR, price_version VARCHAR,
    worth_usd DOUBLE);
CREATE TABLE IF NOT EXISTS gold_price_adjustments (
    row_id VARCHAR PRIMARY KEY, offer_id VARCHAR, line_id VARCHAR, line_no INTEGER, label VARCHAR,
    fund_id VARCHAR, fund_name VARCHAR, event_id VARCHAR, kind VARCHAR, company VARCHAR, event_date DATE,
    reported_at DATE, note VARCHAR, fund_change_pct DOUBLE, line_change_usd DOUBLE, version VARCHAR);
CREATE TABLE IF NOT EXISTS gold_offer_metrics (
    row_id VARCHAR PRIMARY KEY, offer_id VARCHAR, metric VARCHAR, version VARCHAR, value DOUBLE, note VARCHAR);
CREATE TABLE IF NOT EXISTS gold_exposure (
    row_id VARCHAR PRIMARY KEY, offer_id VARCHAR, manager_id VARCHAR, manager_name VARCHAR,
    funds_held INTEGER, our_commitment_usd DOUBLE, our_value_usd DOUBLE, version VARCHAR);
CREATE TABLE IF NOT EXISTS gold_rule_results (
    row_id VARCHAR PRIMARY KEY, offer_id VARCHAR, rule_id VARCHAR, description VARCHAR, metric VARCHAR,
    op VARCHAR, threshold DOUBLE, weight DOUBLE, veto BOOLEAN, status VARCHAR, value DOUBLE,
    rank INTEGER, rulebook_version VARCHAR);
CREATE TABLE IF NOT EXISTS gold_verdicts (
    offer_id VARCHAR PRIMARY KEY, verdict VARCHAR, score DOUBLE, threshold DOUBLE, veto_rule VARCHAR,
    data_matched DOUBLE, margin DOUBLE, price_view VARCHAR, band VARCHAR, headline VARCHAR, headline_json VARCHAR, rulebook_version VARCHAR,
    rulebook_sha VARCHAR, decided_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS gold_briefs (
    offer_id VARCHAR PRIMARY KEY, path VARCHAR, sha256 VARCHAR, valid BOOLEAN, problems VARCHAR,
    written_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS overrides (
    override_id VARCHAR PRIMARY KEY, offer_id VARCHAR, user_name VARCHAR, created_at TIMESTAMP,
    rulebook_version VARCHAR, rulebook_sha VARCHAR, original_verdict VARCHAR, new_verdict VARCHAR,
    reason VARCHAR);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id VARCHAR PRIMARY KEY, offer_id VARCHAR, user_name VARCHAR, created_at TIMESTAMP,
    verdict VARCHAR, band VARCHAR, rulebook_version VARCHAR, rulebook_sha VARCHAR);
CREATE TABLE IF NOT EXISTS line_resolutions (
    resolution_id VARCHAR PRIMARY KEY, offer_id VARCHAR, line_no INTEGER, label VARCHAR, fund_id VARCHAR,
    fund_name VARCHAR, user_name VARCHAR, created_at TIMESTAMP, previous_tier VARCHAR, previous_method VARCHAR);
CREATE TABLE IF NOT EXISTS offer_traces (
    trace_id VARCHAR PRIMARY KEY, offer_id VARCHAR, kind VARCHAR, created_at TIMESTAMP);
"""

OFFER_TABLES = ["silver_offer_lines", "silver_rejects", "gold_line_matches", "gold_price_adjustments", "gold_offer_metrics", "gold_exposure",
                "gold_rule_results", "gold_verdicts", "gold_briefs", "silver_offers"]

# Tables a brief number may cite, with their row key.
LINEAGE_KEYS = {
    "silver_offers": "offer_id", "silver_offer_lines": "line_id", "gold_line_matches": "line_id",
    "gold_exposure": "row_id", "gold_price_adjustments": "row_id", "ref_fund_events": "event_id", "gold_rule_results": "row_id", "gold_verdicts": "offer_id",
    "silver_rejects": "row_id", "overrides": "override_id", "decisions": "decision_id",
    "line_resolutions": "resolution_id", "ref_fund_series": "row_id", "ref_fx_rates": "row_id", "bronze_emails": "message_id",
}


def connect(ws: Workspace, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    ws.ensure()
    con = duckdb.connect(str(ws.db_path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    for name in ("funds", "managers", "fund_series", "holdings", "fx_rates", "fund_events"):
        p = ws.reference_dir / f"{name}.parquet"
        if p.exists():
            con.execute(f"CREATE OR REPLACE TEMP VIEW ref_{name} AS SELECT * FROM read_parquet('{p}')")
    return con


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con, sql: str, params: list | None = None) -> dict | None:
    r = rows(con, sql, params)
    return r[0] if r else None


def lookup(con: duckdb.DuckDBPyConnection, table: str, row_id: str, metric: str):
    """Resolve a lineage pointer to the stored value. Raises KeyError if it does not exist."""
    if table == "gold_offer_metrics":
        r = one(con, "SELECT value FROM gold_offer_metrics WHERE row_id = ? AND metric = ?", [row_id, metric])
        if r is None:
            raise KeyError((table, row_id, metric))
        return r["value"]
    key = LINEAGE_KEYS.get(table)
    if key is None:
        raise KeyError(f"table {table} is not citable")
    cols = {c[0] for c in con.execute(f"DESCRIBE {table}").fetchall()}
    if metric not in cols:
        raise KeyError((table, row_id, metric))
    r = one(con, f"SELECT {metric} AS v FROM {table} WHERE {key} = ?", [row_id])
    if r is None:
        raise KeyError((table, row_id, metric))
    return r["v"]


def delete_offer(con: duckdb.DuckDBPyConnection, offer_id: str) -> None:
    for t in OFFER_TABLES:
        con.execute(f"DELETE FROM {t} WHERE offer_id = ?", [offer_id])


def insert(con: duckdb.DuckDBPyConnection, table: str, row: dict) -> None:
    cols = list(row)
    con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [row[c] for c in cols])


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def resolve_offer(con, code_or_id: str) -> dict | None:
    r = one(con, "SELECT * FROM silver_offers WHERE offer_id = ?", [code_or_id])
    if r:
        return r
    return one(con, "SELECT * FROM silver_offers WHERE lower(code_name) = lower(?) ORDER BY email_date DESC LIMIT 1",
               [code_or_id])


def latest_trace_id(con, offer_id: str) -> str | None:
    r = one(con, "SELECT trace_id FROM offer_traces WHERE offer_id = ? ORDER BY created_at DESC LIMIT 1", [offer_id])
    return r["trace_id"] if r else None


def team_decision(con, offer_id: str) -> dict | None:
    """The latest keep or override, whichever came last. kind is 'keep' or 'override'."""
    ov = one(con, "SELECT * FROM overrides WHERE offer_id = ? ORDER BY created_at DESC LIMIT 1", [offer_id])
    kp = one(con, "SELECT * FROM decisions WHERE offer_id = ? ORDER BY created_at DESC LIMIT 1", [offer_id])
    if ov and (not kp or ov["created_at"] >= kp["created_at"]):
        return dict(ov, kind="override")
    if kp:
        return dict(kp, kind="keep")
    return None
