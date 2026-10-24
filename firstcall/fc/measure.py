"""Collection architecture experiment (PRD section 9). Same input, same processing, two paths.

Writes raw results to measurements/*.json and a summary to measurements/summary.json.
Each run uses fresh topics (prefix) and fresh DuckDB files under data/measure/<run>/.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timezone
from pathlib import Path

import duckdb

from . import pipeline, replay, store
from .cases import corpus, load_cases
from .classify.keyword import KeywordClassifier
from .classify.testing import FlakyClassifier, SlowClassifier
from .collect import load_landing
from .config import DATA_DIR, GDELT_UPDATE_MINUTES, MEASURE_DIR, ROOT, RSS_POLL_MINUTES
from .models import RawItem
from .normalize import parse_time
from .stream import consumer
from .stream.log import FileLog, get_log
from .stream.poller import event_key

AS_OF = "2026-10-24"
AS_OF_DT = datetime.combine(datetime.fromisoformat(AS_OF).date(), dtime(23, 59), timezone.utc)
DECISION_WINDOW_H = 4 * 7 * 24
BATCH_EVERY_PROD_S = RSS_POLL_MINUTES * 60
PY = sys.executable


def replay_input() -> list[RawItem]:
    items = {r.link: r for r in load_landing()}
    for c in load_cases():
        for r in corpus(c):
            items.setdefault(r.link, r)
    return replay.event_order(list(items.values()))


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))
    return xs[k]


def gold_snapshot(db: Path) -> dict:
    con = duckdb.connect(str(db), read_only=True)
    sig = con.execute("SELECT signal_id, pension_id, signal_type, round(confidence, 6), published_at, n_copies "
                      "FROM gold_signals ORDER BY 1").fetchall()
    sco = con.execute("SELECT pension_id, as_of, round(score, 9), verdict, n_signals FROM gold_scores "
                      "WHERE as_of = ? ORDER BY 1", [datetime.fromisoformat(AS_OF).date()]).fetchall()
    cls = con.execute("SELECT count(*), count(DISTINCT item_id) FROM classified_items").fetchone()
    con.close()
    return {"signals": [list(map(str, r)) for r in sig], "scores": [list(map(str, r)) for r in sco],
            "classified_rows": cls[0], "classified_distinct": cls[1]}


def diff(a: dict, b: dict) -> dict:
    sa, sb = {tuple(r) for r in a["signals"]}, {tuple(r) for r in b["signals"]}
    ca, cb = {tuple(r) for r in a["scores"]}, {tuple(r) for r in b["scores"]}
    return {"signals_only_left": len(sa - sb), "signals_only_right": len(sb - sa),
            "scores_only_left": len(ca - cb), "scores_only_right": len(cb - ca),
            "identical": sa == sb and ca == cb}


def _env(prefix: str, log_kind: str, log_dir: Path) -> dict:
    env = dict(os.environ, FC_TOPIC_PREFIX=prefix, FC_LOG=log_kind, FC_LOG_DIR=str(log_dir), PYTHONUNBUFFERED="1")
    env.pop("ANTHROPIC_API_KEY", None)   # the experiment compares pipelines, not models
    return env


def _consumer_cmd(db: Path, idle: float) -> list[str]:
    return [PY, "-m", "fc.cli", "--db", str(db), "--classifier", "keyword", "consume", "--idle-exit", str(idle),
            "--as-of", AS_OF]


def _batch_cmd(db: Path, src: Path, every_s: float, stop: Path) -> list[str]:
    return [PY, "-m", "fc.cli", "--db", str(db), "--classifier", "keyword", "batch-file", "--source", str(src),
            "--every-s", str(every_s), "--stop-file", str(stop), "--as-of", AS_OF]


def latencies(db: Path) -> list[float]:
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT epoch(verdict_at) - epoch(available_at) FROM item_latency").fetchall()
    con.close()
    return [r[0] for r in rows]


# --------------------------------------------------------------------- runs
def run_parity_latency(log_kind: str, items: list[RawItem], run: Path, rate: float, batch_every_s: float) -> dict:
    """Criteria 1 and 2. Both paths consume the same replay at the same time."""
    prefix = f"m{int(time.time())}p."
    log_dir = run / "log"
    env = _env(prefix, log_kind, log_dir)
    src, stop = run / "source.jsonl", run / "stop"
    src.touch()
    lg = get_log(log_kind) if log_kind == "redpanda" else FileLog(log_dir)
    lg.ensure_topic(prefix + "news.raw")
    lg.ensure_topic(prefix + "news.dlq", partitions=1)
    cons = subprocess.Popen(_consumer_cmd(run / "stream.duckdb", 6), cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    bat = subprocess.Popen(_batch_cmd(run / "batch.duckdb", src, batch_every_s, stop), cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, text=True)
    time.sleep(2.0)                      # let both paths start before the first event
    t0 = time.monotonic()
    n = replay.emit(items, rate, log_=lg, source_file=src, topic=prefix + "news.raw")
    emit_s = time.monotonic() - t0
    stop.touch()
    cons_out, _ = cons.communicate(timeout=600)
    bat_out, _ = bat.communicate(timeout=600)
    ls, lb = latencies(run / "stream.duckdb"), latencies(run / "batch.duckdb")
    a, b = gold_snapshot(run / "stream.duckdb"), gold_snapshot(run / "batch.duckdb")
    return {
        "log": lg.name, "topic": prefix + "news.raw", "items": n, "emit_rate_per_s": rate, "emit_seconds": round(emit_s, 2),
        "batch_every_s": batch_every_s,
        "stream_latency_s": {"p50": pct(ls, 50), "p95": pct(ls, 95), "max": max(ls), "n": len(ls)},
        "batch_latency_s": {"p50": pct(lb, 50), "p95": pct(lb, 95), "max": max(lb), "n": len(lb)},
        "parity": diff(a, b), "signals": len(a["signals"]), "pensions_scored": len(a["scores"]),
        "consumer_stdout": cons_out.strip().splitlines()[-1:], "batch_stdout": bat_out.strip().splitlines()[-1:],
    }


def run_recovery_stream(log_kind: str, items: list[RawItem], run: Path, reference: dict, kill_after_s: float) -> dict:
    """Criterion 3, stream: SIGKILL the consumer mid-run, restart, compare with the reference."""
    prefix = f"m{int(time.time())}r."
    log_dir = run / "log_rec"
    env = _env(prefix, log_kind, log_dir)
    lg = get_log(log_kind) if log_kind == "redpanda" else FileLog(log_dir)
    topic = prefix + "news.raw"
    lg.ensure_topic(topic)
    lg.ensure_topic(prefix + "news.dlq", partitions=1)
    replay.emit(items, 0, log_=lg, topic=topic)
    total = sum(lg.end_offsets(topic).values())
    db = run / "stream_recovery.duckdb"
    p = subprocess.Popen(_consumer_cmd(db, 4), cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    time.sleep(kill_after_s)
    p.send_signal(signal.SIGKILL)
    p.wait()
    committed_at_kill = sum(lg.committed(topic, consumer.GROUP).values())
    # Restart idle window must outlive the dead member's session (6 s) plus the rebalance.
    p2 = subprocess.run(_consumer_cmd(db, 15), cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    second = ast.literal_eval(p2.stdout.strip().splitlines()[-1])     # {'processed': n, 'dlq': m}
    got = gold_snapshot(db)
    d = diff(got, reference)
    return {"total_records": total, "committed_at_kill": committed_at_kill,
            "processed_after_restart": second["processed"],
            "restart_to_first_record_s": second.get("first_record_s"),
            "redelivered": committed_at_kill + second["processed"] - total,
            "duplicate_rows": got["classified_rows"] - got["classified_distinct"],
            "missed_signals": d["signals_only_right"], "extra_signals": d["signals_only_left"],
            "matches_reference": d["identical"]}


def run_recovery_batch(items: list[RawItem], run: Path, reference: dict, kill_after_s: float) -> dict:
    """Criterion 3, batch: SIGKILL the job mid-batch, rerun, compare with the reference."""
    src = run / "source_rec.jsonl"
    src.write_text("".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in items))
    db, stop = run / "batch_recovery.duckdb", run / "stop_rec"
    env = _env("unused.", "file", run / "unused")
    p = subprocess.Popen(_batch_cmd(db, src, 1.0, stop), cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    time.sleep(kill_after_s)
    p.send_signal(signal.SIGKILL)
    p.wait()
    con = store.connect(db)
    from .batch import _watermark

    wm = _watermark(con)
    con.close()
    stop.touch()
    subprocess.run(_batch_cmd(db, src, 1.0, stop), cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    got = gold_snapshot(db)
    d = diff(got, reference)
    return {"watermark_at_kill": wm, "total_records": len(items),
            "duplicate_rows": got["classified_rows"] - got["classified_distinct"],
            "missed_signals": d["signals_only_right"], "extra_signals": d["signals_only_left"],
            "matches_reference": d["identical"]}


def run_replay_time(log_kind: str, run: Path, reference: dict, topic: str) -> dict:
    """Criterion 7: rebuild every output from bronze. Batch: one pass. Stream: offsets to 0, new group."""
    src_db = run / "batch.duckdb"
    con = duckdb.connect(str(src_db), read_only=True)
    raws = [RawItem.model_validate_json(r[0]) for r in con.execute("SELECT raw FROM bronze_items ORDER BY bronze_id").fetchall()]
    con.close()
    db_b = run / "rebuild_batch.duckdb"
    t0 = time.monotonic()
    c = store.connect(db_b)
    pipeline.process_many(c, raws, KeywordClassifier(), AS_OF_DT, path="batch")
    c.close()
    batch_s = time.monotonic() - t0
    lg = get_log(log_kind) if log_kind == "redpanda" else FileLog(run / "log")
    # the parity run's topic still holds every event: replay = a new group reading from offset 0
    t0 = time.monotonic()
    consumer.run(lg, run / "rebuild_stream.duckdb", KeywordClassifier(), as_of=AS_OF_DT, group="rebuild-from-0",
                 idle_exit=3.0, topic=topic, dlq_topic=topic.replace("news.raw", "news.dlq"))
    stream_s = time.monotonic() - t0 - 3.0      # minus the idle wait that ends the run
    linked = sum(1 for _ in raws)
    return {"bronze_rows": len(raws), "batch_rebuild_s": round(batch_s, 2), "stream_rebuild_s": round(stream_s, 2),
            "batch_matches_reference": diff(gold_snapshot(db_b), reference)["identical"],
            "stream_matches_reference": diff(gold_snapshot(run / "rebuild_stream.duckdb"), reference)["identical"],
            "note": "keyword classifier; with the model (about 1 s per linked item) both paths add the same model time",
            "items": linked}


def run_out_of_order(items: list[RawItem], run: Path, reference: dict) -> dict:
    """Event time vs processing time: deliver in random order; per-key recompute gives the same gold."""
    lg = FileLog(run / "log_ooo")
    replay.emit(items, 0, log_=lg, shuffle_seed=7, topic="ooo.news.raw")
    consumer.run(lg, run / "ooo.duckdb", KeywordClassifier(), as_of=AS_OF_DT, group="ooo", idle_exit=1.0,
                 topic="ooo.news.raw", dlq_topic="ooo.news.dlq")
    return {"shuffled": True, "matches_reference": diff(gold_snapshot(run / "ooo.duckdb"), reference)["identical"]}


def run_dlq(items: list[RawItem], run: Path, reference: dict) -> dict:
    """Dead-letter topic: a flaky classifier sends failures to the DLQ; a redrive restores parity."""
    lg = FileLog(run / "log_dlq")
    replay.emit(items, 0, log_=lg, topic="dlq.news.raw")
    s1 = consumer.run(lg, run / "dlq.duckdb", FlakyClassifier(7), as_of=AS_OF_DT, group="g", idle_exit=1.0,
                      topic="dlq.news.raw", dlq_topic="dlq.news.dlq")
    before = diff(gold_snapshot(run / "dlq.duckdb"), reference)
    redriven = consumer.redrive_dlq(lg, topic="dlq.news.raw", dlq_topic="dlq.news.dlq")
    consumer.run(lg, run / "dlq.duckdb", KeywordClassifier(), as_of=AS_OF_DT, group="g", idle_exit=1.0,
                 topic="dlq.news.raw", dlq_topic="dlq.news.dlq")
    after = diff(gold_snapshot(run / "dlq.duckdb"), reference)
    return {"dead_lettered": s1["dlq"], "processed_first_pass": s1["processed"], "missing_before_redrive": before["signals_only_right"],
            "redriven": redriven, "matches_reference_after_redrive": after["identical"]}


def run_backpressure(items: list[RawItem], run: Path, delay: float) -> dict:
    """The model call is the bottleneck. A slow classifier lets lag grow while events keep arriving."""
    import threading

    lg = FileLog(run / "log_bp")
    topic = "bp.news.raw"
    lg.ensure_topic(topic)
    lg.ensure_topic("bp.news.dlq", partitions=1)
    linked_first = [r for r in items if event_key(r) != "unlinked"][:60]
    trace: list = []
    t = threading.Thread(target=replay.emit, args=(linked_first, 10.0), kwargs={"log_": lg, "topic": topic})
    t0 = time.monotonic()
    t.start()
    samples = []
    cons = threading.Thread(target=consumer.run, args=(lg, run / "bp.duckdb", SlowClassifier(delay)),
                            kwargs={"as_of": AS_OF_DT, "group": "bp", "idle_exit": 2.0, "topic": topic,
                                    "dlq_topic": "bp.news.dlq", "lag_trace": trace})
    cons.start()
    while cons.is_alive():
        ends = sum(lg.end_offsets(topic).values())
        com = sum(lg.committed(topic, "bp").values())
        samples.append((round(time.monotonic() - t0, 1), ends - com))
        time.sleep(1.0)
    t.join()
    return {"events": len(linked_first), "arrival_rate_per_s": 10.0, "classifier_delay_s": delay,
            "service_rate_per_s": round(1 / delay, 2), "lag_samples": samples,
            "max_lag": max(l for _, l in samples), "drain_seconds": samples[-1][0]}


def source_cadence() -> dict:
    """How fresh the sources are. This caps freshness for both paths."""
    lags = []
    for r in load_landing():
        pub = parse_time(r.published, r.fetched_at)
        h = (r.fetched_at - pub).total_seconds() / 3600
        if 0 <= h <= 72:
            lags.append(h)
    return {"gdelt_update_minutes": GDELT_UPDATE_MINUTES, "rss_poll_minutes": RSS_POLL_MINUTES,
            "rss_items_published_last_72h": len(lags),
            "freshest_item_age_at_fetch_hours": round(min(lags), 2) if lags else None,
            "note": ("age of the newest item when we fetched it, an upper bound on index delay. Pensions publish "
                     "board materials days before meetings, so the news itself arrives in days, not seconds.")}


def live_job_duration() -> dict:
    """Time one real batch fetch (RSS only), to size the batch path's monthly running hours."""
    from .collect import fetch_all

    t0 = time.monotonic()
    items, stats = fetch_all(use_gdelt=False)
    return {"rss_fetch_seconds": round(time.monotonic() - t0, 1), "items": len(items), "errors": stats["errors"]}


def code_size() -> dict:
    def loc(paths):
        n = 0
        for p in paths:
            for line in (ROOT / p).read_text().splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    n += 1
        return n

    stream = ["fc/stream/log.py", "fc/stream/poller.py", "fc/stream/consumer.py", "docker-compose.yml"]
    batch = ["fc/batch.py"]
    shared = ["fc/pipeline.py", "fc/store.py", "fc/normalize.py", "fc/scoring.py", "fc/collect.py",
              "fc/pensions.py", "fc/classify/keyword.py", "fc/classify/claude.py", "fc/models.py"]
    return {"stream_specific_lines": loc(stream), "batch_specific_lines": loc(batch), "shared_lines": loc(shared),
            "stream_files": stream, "batch_files": batch}


def main(log_kind: str | None = None, quick: bool = False) -> None:
    lg = get_log(log_kind)
    kind = lg.name
    run = DATA_DIR / "measure" / datetime.now().strftime("%Y%m%dT%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    MEASURE_DIR.mkdir(exist_ok=True)
    items = replay_input()
    print(f"replay input: {len(items)} real items; log adapter: {kind}; run dir: {run}", flush=True)
    results: dict = {"run": run.name, "log": kind, "as_of": AS_OF}

    def save(name, obj):
        results[name] = obj
        (MEASURE_DIR / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))
        print(f"- {name}: {json.dumps(obj, default=str)[:400]}", flush=True)

    rate = 40.0 if quick else 20.0
    pl = run_parity_latency(kind, items, run, rate=rate, batch_every_s=5.0)
    save("parity_latency", pl)
    reference = gold_snapshot(run / "batch.duckdb")
    save("recovery_stream", run_recovery_stream(kind, items, run, reference, kill_after_s=4.0))
    save("recovery_batch", run_recovery_batch(items, run, reference, kill_after_s=4.0))
    save("replay_time", run_replay_time(kind, run, reference, pl["topic"]))
    save("out_of_order", run_out_of_order(items, run, reference))
    save("dead_letter", run_dlq(items, run, reference))
    save("backpressure", run_backpressure(items, run, delay=0.5 if quick else 1.0))
    save("source_cadence", source_cadence())
    job = {"rss_fetch_seconds": 30.0, "note": "estimate (quick mode)"} if quick else live_job_duration()
    save("live_job", job)
    cs = code_size()
    save("code_size", cs)

    # ---- the 7 criteria
    proc_p95_b = pl["batch_latency_s"]["p95"]
    batch_prod_p95_s = 0.95 * BATCH_EVERY_PROD_S + proc_p95_b + job["rss_fetch_seconds"]
    src_cap_s = GDELT_UPDATE_MINUTES * 60
    limit_h = 0.10 * DECISION_WINDOW_H
    job_s = job["rss_fetch_seconds"] + 2.0
    runs_per_month = 30 * 24 * 60 / RSS_POLL_MINUTES
    rec_s, rec_b = results["recovery_stream"], results["recovery_batch"]
    summary = {
        "decision_window_hours": DECISION_WINDOW_H, "freshness_limit_hours": limit_h,
        "criteria": {
            "1_p95_latency_hours": {
                "stream_measured": round(pl["stream_latency_s"]["p95"] / 3600, 5),
                "batch_measured_every_5s": round(proc_p95_b / 3600, 5),
                "batch_projected_every_15min": round(batch_prod_p95_s / 3600, 3),
                "source_cadence_cap_hours": src_cap_s / 3600,
                "limit_hours": limit_h,
                "stream_passes": pl["stream_latency_s"]["p95"] / 3600 + src_cap_s / 3600 < limit_h,
                "batch_passes": batch_prod_p95_s / 3600 + src_cap_s / 3600 < limit_h},
            "2_parity_identical": pl["parity"]["identical"],
            "3_recovery": {"stream_duplicates": rec_s["duplicate_rows"], "stream_missed": rec_s["missed_signals"],
                           "stream_redelivered_records": rec_s["redelivered"],
                           "stream_restart_to_first_record_s": rec_s.get("restart_to_first_record_s"),
                           "batch_duplicates": rec_b["duplicate_rows"], "batch_missed": rec_b["missed_signals"],
                           "stream_passes": rec_s["matches_reference"] and rec_s["duplicate_rows"] == 0,
                           "batch_passes": rec_b["matches_reference"] and rec_b["duplicate_rows"] == 0},
            "4_moving_parts": {"stream": 3 if kind == "redpanda" else 2, "batch": 1,
                               "stream_detail": "poller + consumer + broker" if kind == "redpanda" else "poller + consumer + log directory",
                               "batch_detail": "one scheduled job"},
            "5_path_specific_lines": {"stream": cs["stream_specific_lines"], "batch": cs["batch_specific_lines"]},
            "6_always_on_hours_per_month": {"stream": 720 * (3 if kind == "redpanda" else 2),
                                            "batch": round(runs_per_month * job_s / 3600, 1),
                                            "batch_basis": f"{runs_per_month:.0f} runs x {job_s:.0f} s"},
            "7_replay_seconds": {"stream": results["replay_time"]["stream_rebuild_s"],
                                 "batch": results["replay_time"]["batch_rebuild_s"]},
        },
    }
    c = summary["criteria"]
    both_pass = {p: c["1_p95_latency_hours"][f"{p}_passes"] and c["2_parity_identical"] and c["3_recovery"][f"{p}_passes"]
                 for p in ("stream", "batch")}
    summary["passes_1_to_3"] = both_pass
    summary["decision"] = ("batch" if both_pass["batch"] else "stream" if both_pass["stream"] else "neither")
    summary["rule"] = "simplest path that passes 1 to 3; ties go to fewer moving parts"
    save("summary", summary)
    print(f"\nDecision: {summary['decision']} (log adapter used for stream: {kind})")
    shutil.rmtree(run / "unused", ignore_errors=True)
