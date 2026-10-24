"""Our reference data, with point-in-time access.

Rule: a value "known at T" is the latest report with reported_at <= T. A fund's
value at a date can be restated later; a restatement we had not received at T
must not change what we compute for T.
"""
from __future__ import annotations

import datetime as dt
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb


def _rows(con: duckdb.DuckDBPyConnection, path: Path) -> list[dict]:
    cur = con.execute(f"SELECT * FROM read_parquet('{path}')")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _as_dt(d: dt.date | dt.datetime) -> dt.datetime:
    if isinstance(d, dt.datetime):
        return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return dt.datetime(d.year, d.month, d.day, 23, 59, 59)


@dataclass
class SeriesRow:
    row_id: str
    fund_id: str
    as_of_date: dt.date
    reported_at: dt.date
    called: float
    distributed: float
    nav: float


class Reference:
    def __init__(self, reference_dir: Path):
        self.dir = reference_dir
        con = duckdb.connect()
        self.managers = {m["manager_id"]: m for m in _rows(con, reference_dir / "managers.parquet")}
        self.funds = {f["fund_id"]: f for f in _rows(con, reference_dir / "funds.parquet")}
        self.holdings = {h["fund_id"]: h for h in _rows(con, reference_dir / "holdings.parquet")}
        self.series: dict[str, list[SeriesRow]] = defaultdict(list)
        for r in _rows(con, reference_dir / "fund_series.parquet"):
            self.series[r["fund_id"]].append(SeriesRow(r["row_id"], r["fund_id"], r["as_of_date"], r["reported_at"],
                                                       r["called"], r["distributed"], r["nav"]))
        fx = defaultdict(list)
        for r in _rows(con, reference_dir / "fx_rates.parquet"):
            fx[r["currency"]].append((r["rate_date"], r["usd_per_unit"], r["row_id"]))
        self.fx = {k: sorted(v) for k, v in fx.items()}
        con.close()
        self.by_key: dict[tuple, list[dict]] = defaultdict(list)
        for f in self.funds.values():
            self.by_key[(f["vintage"], f["strategy"], f["region"])].append(f)

    def manager_name(self, manager_id: str) -> str:
        return self.managers[manager_id]["name"]

    def candidates(self, vintage: int | None, strategy: str | None, region: str | None) -> list[dict]:
        out = []
        for f in self.funds.values():
            if vintage is not None and f["vintage"] != vintage:
                continue
            if strategy is not None and f["strategy"] != strategy:
                continue
            if region is not None and f["region"] != region:
                continue
            out.append(f)
        return sorted(out, key=lambda f: f["fund_id"])

    def series_at(self, fund_id: str, as_of: dt.date, known_at: dt.datetime) -> SeriesRow | None:
        """The report for quarter `as_of` as we knew it at `known_at`."""
        k = _as_dt(known_at)
        rows = [r for r in self.series.get(fund_id, []) if r.as_of_date == as_of and _as_dt(r.reported_at) <= k]
        return max(rows, key=lambda r: r.reported_at) if rows else None

    def latest(self, fund_id: str, known_at: dt.datetime) -> SeriesRow | None:
        """Our latest reported value for a fund, as known at `known_at`."""
        k = _as_dt(known_at)
        rows = [r for r in self.series.get(fund_id, []) if _as_dt(r.reported_at) <= k]
        return max(rows, key=lambda r: (r.as_of_date, r.reported_at)) if rows else None

    def fx_rate(self, currency: str, on: dt.date) -> tuple[float, str]:
        rows = self.fx[currency]
        i = bisect_right([r[0] for r in rows], on) - 1
        if i < 0:
            raise KeyError(f"no {currency} rate on or before {on}")
        return rows[i][1], rows[i][2]
