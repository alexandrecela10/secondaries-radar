"""Price check: what our fund reports told us after the broker's date, point in time, and the eval
against each fund's next reported value (which the pipeline never sees)."""
import datetime as dt

import duckdb
import pytest

from quicklook import store

from .conftest import EVALS

REF = dt.date(2026, 6, 30)


def _line_fund(env, code: str, line_no: int) -> str:
    offer = next(o for o in env.labels["offers"] if o["code"] == code)
    return next(l["true_fund_id"] for l in offer["lines"] if l["line_no"] == line_no)


def test_events_are_point_in_time(env):
    fid = _line_fund(env, "Cedar", 1)  # Nordhavn Logistics sale, told on 19 August
    assert env.ref.events_since(fid, REF, dt.datetime(2026, 8, 18, 23, 0)) == []
    got = env.ref.events_since(fid, REF, dt.datetime(2026, 10, 23, 9, 0))
    assert [e.company for e in got] == ["Nordhavn Logistics"]
    assert env.ref.events_since(fid, dt.date(2026, 9, 30), dt.datetime(2026, 10, 23)) == []  # only after the ref date


def test_cedar_looks_cheap_and_hornbeam_expensive(env):
    con = env.con()
    v = {r["code_name"]: r for r in store.rows(con, """SELECT o.code_name, v.* FROM gold_verdicts v
                                                        JOIN silver_offers o USING (offer_id)""")}
    adj = store.rows(con, """SELECT a.* FROM gold_price_adjustments a JOIN silver_offers o USING (offer_id)
                             WHERE o.code_name = 'Cedar' ORDER BY a.line_no""")
    con.close()
    assert v["Cedar"]["verdict"] == "LOOK CLOSER" and v["Cedar"]["price_view"] == "cheap"
    assert v["Hornbeam"]["verdict"] == "PASS" and v["Hornbeam"]["veto_rule"] == "price_veto"
    assert [a["company"] for a in adj] == ["Nordhavn Logistics", "Kestrel Testing"]
    assert all(a["line_change_usd"] > 0 for a in adj)


def test_eval_estimate_beats_broker_value(env):
    """For each fund we own that told us something after the reference date: is our estimate
    (broker's value plus the events) closer to the next reported value than the broker's value?
    Synthetic: the generator made both the events and the next value, so this shows the mechanism
    works, not that it works on real funds. The real test replays our own past fund reports."""
    nxt = duckdb.sql(f"SELECT * FROM read_parquet('{env.data / 'market' / 'fund_next.parquet'}')").fetchall()
    cols = [d[0] for d in duckdb.sql(f"SELECT * FROM read_parquet('{env.data / 'market' / 'fund_next.parquet'}')").description]
    known_at = dt.datetime(2026, 10, 25)
    wins = n = 0
    err_ours = err_broker = 0.0
    for row in (dict(zip(cols, r)) for r in nxt):
        evs = env.ref.events_since(row["fund_id"], REF, known_at)
        if not evs:
            continue
        broker = row["fund_value_ref"]
        ours = broker + sum(e.value_after - e.value_before for e in evs)
        n += 1
        wins += abs(ours - row["nav_next"]) < abs(broker - row["nav_next"])
        err_ours += abs(ours - row["nav_next"]) / broker
        err_broker += abs(broker - row["nav_next"]) / broker
    EVALS["price_check_vs_next_value"] = {
        "funds_with_news": n, "ours_closer": wins, "share": wins / n,
        "mean_error_ours": err_ours / n, "mean_error_broker_value": err_broker / n, "target": 0.7,
        "note": "synthetic: events and next values come from the same generator"}
    assert n >= 10 and wins / n >= 0.7
