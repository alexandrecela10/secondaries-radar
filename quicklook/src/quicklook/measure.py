"""Collection architecture experiment (PRD section 9). Same pipeline, two intake paths.

Measures 7 criteria and writes raw results to measurements/*.json:
 1 p95 latency, send to brief written, vs 10% of a 1-working-day window (48 minutes)
 2 output parity between paths on the same emails
 3 recovery: kill -9 mid-run, restart: duplicates and missed emails
 4 moving parts   5 path-specific lines of code   6 always-on hours per month   7 replay time from bronze
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import math
import os
import random
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import duckdb

from . import pipeline, store
from .config import DATA_DIR, ROOT, default_workspace, offers_dir
from .intake import batch as batch_mod
from .intake import sender
from .parse import OfflineParser

OUT = ROOT / "measurements"
WINDOW_S = 8 * 3600  # one working day
LIMIT_S = 0.10 * WINDOW_S
PORT = 2525


def _emails(rounds: int) -> list[bytes]:
    files = sorted(offers_dir().glob("*.eml"))
    return [sender.with_suffix(f.read_bytes(), f"r{r}" if r else None) for r in range(rounds) for f in files]


def _fresh(name: str):
    ws = default_workspace(name)
    shutil.rmtree(ws.root, ignore_errors=True)
    return ws


def _wait_port(port: int, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"port {port} did not open")


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _server(ws_name: str, slow: float = 0.0) -> subprocess.Popen:
    env = dict(os.environ, QUICKLOOK_SLOW_SECONDS=str(slow))
    p = subprocess.Popen([sys.executable, "-m", "quicklook.intake.smtp_server", "--workspace", ws_name,
                          "--port", str(PORT), "--offline"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _wait_port(PORT)
    return p


def _batch(ws_name: str, maildir: Path, interval: float, slow: float = 0.0) -> subprocess.Popen:
    env = dict(os.environ, QUICKLOOK_SLOW_SECONDS=str(slow))
    return subprocess.Popen([sys.executable, "-m", "quicklook.intake.batch", "--workspace", ws_name, "--maildir",
                             str(maildir), "--interval", str(interval), "--offline"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _stop(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    # free the port fully before the next server
    for _ in range(100):
        if _port_free(PORT):
            return
        time.sleep(0.05)


def _processed(ws) -> dict[str, dict]:
    con = duckdb.connect(str(ws.db_path), read_only=True)
    out = {r[0]: dict(status=r[1], processed_at=r[2], attempts=r[3]) for r in
           con.execute("SELECT message_id, status, processed_at, attempts FROM bronze_emails").fetchall()}
    con.close()
    return out


def _epoch(t: dt.datetime) -> float:
    return t.replace(tzinfo=dt.timezone.utc).timestamp()


def p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)]


def _wait_maildir_empty(maildir: Path, timeout: float) -> None:
    end = time.time() + timeout
    while time.time() < end and os.listdir(maildir / "new"):
        time.sleep(0.1)


# ------------------------------------------------------------------ criteria 1 and 2

def latency_realtime(rounds: int) -> dict:
    ws = _fresh("exp-realtime")
    p = _server("exp-realtime")
    sends = []
    for raw in _emails(rounds):
        sends.append(sender.send_smtp(raw, port=PORT))
        time.sleep(0.1)
    _stop(p)
    done = _processed(ws)
    lat = [_epoch(done[s["message_id"]]["processed_at"]) - s["sent_at"] for s in sends]
    return dict(path="realtime", emails=len(sends), latencies_s=lat, p50_s=statistics.median(lat), p95_s=p95(lat),
                max_s=max(lat), limit_s=LIMIT_S, passes=p95(lat) < LIMIT_S, sends=sends)


def latency_batch_real(rounds: int, interval: float) -> dict:
    ws = _fresh("exp-batch")
    maildir = ROOT / "outbox" / "exp-batch"
    shutil.rmtree(maildir, ignore_errors=True)
    p = _batch("exp-batch", maildir, interval)
    time.sleep(1.0)
    sends = []
    for raw in _emails(rounds):
        sends.append(sender.drop_maildir(raw, maildir))
        time.sleep(0.1)
    _wait_maildir_empty(maildir, 120)
    time.sleep(1.0)
    _stop(p)
    done = _processed(ws)
    lat = [_epoch(done[s["message_id"]]["processed_at"]) - s["sent_at"] for s in sends]
    return dict(path="batch", interval_s=interval, emails=len(sends), latencies_s=lat, p50_s=statistics.median(lat),
                p95_s=p95(lat), max_s=max(lat), limit_s=LIMIT_S, passes=p95(lat) < LIMIT_S)


def latency_batch_simulated(interval_min: float, proc_s: float, n: int = 20000, seed: int = 1) -> dict:
    """Arrivals uniform over a 09:00-17:00 day; a scheduler runs every N minutes on the clock."""
    rng = random.Random(seed)
    step = interval_min * 60
    lat = []
    for _ in range(n):
        t = 9 * 3600 + rng.random() * WINDOW_S
        nxt = math.ceil(t / step) * step
        lat.append(nxt - t + proc_s)
    return dict(path="batch (simulated)", interval_min=interval_min, processing_s=proc_s, samples=n,
                p50_s=statistics.median(lat), p95_s=p95(lat), max_s=max(lat), limit_s=LIMIT_S, passes=p95(lat) < LIMIT_S)


PARITY_SQL = {
    "silver_offers": "SELECT * FROM silver_offers",
    "silver_offer_lines": "SELECT * FROM silver_offer_lines",
    "silver_rejects": "SELECT row_id, offer_id, line_ref, reason, raw FROM silver_rejects",
    "review_queue": "SELECT message_id, reason FROM review_queue",
    "gold_line_matches": "SELECT * FROM gold_line_matches",
    "gold_offer_metrics": "SELECT * FROM gold_offer_metrics",
    "gold_exposure": "SELECT * FROM gold_exposure",
    "gold_rule_results": "SELECT * FROM gold_rule_results",
    "gold_verdicts": "SELECT * FROM gold_verdicts",
    "gold_briefs": "SELECT offer_id, path, sha256, valid FROM gold_briefs",
    "bronze_emails": "SELECT message_id, sha256, status FROM bronze_emails",
}


def table_hashes(ws) -> dict[str, dict]:
    con = duckdb.connect(str(ws.db_path), read_only=True)
    out = {}
    for t, q in PARITY_SQL.items():
        rows = con.execute(f"SELECT * FROM ({q}) ORDER BY ALL").fetchall()
        out[t] = dict(rows=len(rows), sha256=hashlib.sha256(repr(rows).encode()).hexdigest())
    con.close()
    return out


def parity(ws_a, ws_b) -> dict:
    a, b = table_hashes(ws_a), table_hashes(ws_b)
    diffs = [t for t in a if a[t] != b[t]]
    return dict(realtime=a, batch=b, differing_tables=diffs, identical=not diffs)


# ------------------------------------------------------------------ criterion 3

def _recovery_check(ws, expected_ids: set[str], expected_lines: int) -> dict:
    con = duckdb.connect(str(ws.db_path), read_only=True)
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    res = dict(
        bronze_rows=q("SELECT count(*) FROM bronze_emails"),
        distinct_ids=q("SELECT count(DISTINCT message_id) FROM bronze_emails"),
        offers=q("SELECT count(*) FROM silver_offers"),
        distinct_offer_messages=q("SELECT count(DISTINCT message_id) FROM silver_offers"),
        line_rows=q("SELECT count(*) FROM gold_line_matches"),
        brief_rows=q("SELECT count(*) FROM gold_briefs"),
        not_finished=[r[0] for r in con.execute(
            "SELECT message_id FROM bronze_emails WHERE status NOT IN ('done', 'parse_failed')").fetchall()],
        retried_in_pipeline=[r[0] for r in con.execute("SELECT message_id FROM bronze_emails WHERE attempts > 1").fetchall()],
    )
    got = {r[0] for r in con.execute("SELECT message_id FROM bronze_emails WHERE status IN ('done','parse_failed')").fetchall()}
    con.close()
    res["missed"] = sorted(expected_ids - got)
    res["duplicates"] = (res["bronze_rows"] - res["distinct_ids"]) + (res["offers"] - res["distinct_offer_messages"]) + \
        max(0, res["line_rows"] - expected_lines)
    res["passes"] = res["duplicates"] == 0 and not res["missed"] and not res["not_finished"] and res["line_rows"] == expected_lines
    return res


def _expected() -> tuple[set[str], int]:
    raws = _emails(1)
    labels = json.loads((DATA_DIR / "fixtures" / "labels.json").read_text())
    return {sender.message_id(r) for r in raws}, sum(len(o["lines"]) for o in labels["offers"])


def recovery_realtime(kill_after: float = 2.5, slow: float = 0.8) -> dict:
    ws = _fresh("exp-rt-kill")
    ids, n_lines = _expected()
    p = _server("exp-rt-kill", slow=slow)
    sends: list[dict] = []

    def run():
        for raw in _emails(1):
            sends.append(sender.send_smtp(raw, port=PORT, retries=200, backoff=0.1))

    th = threading.Thread(target=run)
    th.start()
    time.sleep(kill_after)
    p.send_signal(signal.SIGKILL)
    p.wait()
    killed_at = time.time()
    time.sleep(1.0)
    p2 = _server("exp-rt-kill")
    th.join(120)
    _stop(p2)
    res = _recovery_check(ws, ids, n_lines)
    res.update(path="realtime", killed_after_s=kill_after, slow_s=slow, sends_retried=sum(1 for s in sends if s["attempts"] > 1),
               sends_failed=sum(1 for s in sends if not s["ok"]), killed_at=killed_at)
    return res


def recovery_batch(kill_after: float = 2.5, slow: float = 0.8) -> dict:
    ws = _fresh("exp-batch-kill")
    ids, n_lines = _expected()
    maildir = ROOT / "outbox" / "exp-batch-kill"
    shutil.rmtree(maildir, ignore_errors=True)
    for raw in _emails(1):
        sender.drop_maildir(raw, maildir)
    p = _batch("exp-batch-kill", maildir, interval=1.0, slow=slow)
    time.sleep(kill_after)
    p.send_signal(signal.SIGKILL)
    p.wait()
    left = len(os.listdir(maildir / "new"))
    p2 = _batch("exp-batch-kill", maildir, interval=1.0)
    _wait_maildir_empty(maildir, 120)
    time.sleep(1.5)
    _stop(p2)
    res = _recovery_check(ws, ids, n_lines)
    res.update(path="batch", killed_after_s=kill_after, slow_s=slow, left_in_new_after_kill=left)
    return res


# ------------------------------------------------------------------ criteria 4 to 7

def code_lines(path: Path) -> int:
    """Lines of code: not blank, not a comment, not a docstring."""
    src = path.read_text()
    tree = ast.parse(src)
    doc = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant) \
                    and isinstance(node.body[0].value.value, str):
                doc.update(range(node.body[0].lineno, node.body[0].end_lineno + 1))
    return sum(1 for i, l in enumerate(src.splitlines(), start=1)
               if l.strip() and not l.strip().startswith("#") and i not in doc)


MOVING_PARTS = {
    "realtime": ["SMTP listener process, always on", "process supervisor to restart it (launchd or systemd)",
                 "mail routing rule that forwards offers to the listener"],
    "batch": ["scheduler entry (cron or launchd) that starts the job every N minutes",
              "mailbox the job reads (Maildir here, IMAP or Graph in production)"],
}


def idle_cost(interval_min: float) -> dict:
    ws = _fresh("exp-idle")
    maildir = ROOT / "outbox" / "exp-idle"
    shutil.rmtree(maildir, ignore_errors=True)
    parser = OfflineParser()
    batch_mod.run_once(ws, maildir, parser)  # warm up
    t0 = time.perf_counter()
    k = 20
    for _ in range(k):
        batch_mod.run_once(ws, maildir, parser)
    per_run = (time.perf_counter() - t0) / k
    runs = 30.4 * 24 * 60 / interval_min
    # a fresh process per run also pays interpreter start-up; measure it once
    t1 = time.perf_counter()
    subprocess.run([sys.executable, "-m", "quicklook.intake.batch", "--workspace", "exp-idle", "--maildir",
                    str(maildir), "--once", "--offline"], check=True, stdout=subprocess.DEVNULL)
    per_process = time.perf_counter() - t1
    return dict(realtime_always_on_h=730.0, batch_always_on_h=0.0, batch_interval_min=interval_min,
                batch_empty_scan_s=per_run, batch_cold_start_s=per_process, batch_runs_per_month=runs,
                batch_busy_h_per_month=runs * per_process / 3600)


def replay_time(ws_name: str) -> dict:
    ws = default_workspace(ws_name)
    t0 = time.perf_counter()
    res = pipeline.replay(ws, OfflineParser())
    return dict(workspace=ws_name, emails=len(res), seconds=time.perf_counter() - t0)


# ------------------------------------------------------------------ run and decide

def _save(name: str, obj) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=2, default=str))


def run(quick: bool = False) -> dict:
    if not _port_free(PORT):
        raise SystemExit(f"Port {PORT} is busy. Stop the other SMTP server first.")
    if not (DATA_DIR / "fixtures" / "labels.json").exists():
        from .synth import run as gen

        gen(DATA_DIR)
    rounds = 2 if quick else 3
    print("1. latency: real-time path ...", flush=True)
    rt = latency_realtime(rounds)
    _save("latency_realtime", rt)
    print("1. latency: batch path, real 5 s interval ...", flush=True)
    bt = latency_batch_real(rounds, interval=5.0)
    _save("latency_batch_real", bt)
    proc = p95([s["acked_at"] - s["sent_at"] for s in rt["sends"]])
    sims = {f"N={m:g}min": latency_batch_simulated(m, proc) for m in (1, 5, 15, 60)}
    _save("latency_batch_simulated", sims)
    print("2. parity ...", flush=True)
    par = parity(default_workspace("exp-realtime"), default_workspace("exp-batch"))
    _save("parity", par)
    print("3. recovery: kill -9 mid-run ...", flush=True)
    rec = dict(realtime=recovery_realtime(), batch=recovery_batch())
    _save("recovery", rec)
    print("4-7. moving parts, code size, idle cost, replay ...", flush=True)
    loc = dict(realtime=code_lines(Path(__file__).parent / "intake" / "smtp_server.py"),
               batch=code_lines(Path(__file__).parent / "intake" / "batch.py"),
               shared_pipeline=code_lines(Path(__file__).parent / "pipeline.py"))
    idle = idle_cost(15)
    rep = dict(realtime=replay_time("exp-realtime"), batch=replay_time("exp-batch"))
    _save("static", dict(moving_parts=MOVING_PARTS, lines_of_code=loc, idle=idle, replay=rep))

    sim15 = sims["N=15min"]
    summary = {
        "window": "1 working day = 8 h; limit = 10% = 48 min",
        "criteria": {
            "1_p95_latency_s": {"realtime": rt["p95_s"], "batch_real_5s": bt["p95_s"], "batch_sim_15min": sim15["p95_s"],
                                "batch_sim_1min": sims["N=1min"]["p95_s"], "limit_s": LIMIT_S},
            "2_parity": par["identical"],
            "3_recovery": {"realtime": dict(duplicates=rec["realtime"]["duplicates"], missed=len(rec["realtime"]["missed"]),
                                            passes=rec["realtime"]["passes"]),
                           "batch": dict(duplicates=rec["batch"]["duplicates"], missed=len(rec["batch"]["missed"]),
                                         passes=rec["batch"]["passes"])},
            "4_moving_parts": {k: len(v) for k, v in MOVING_PARTS.items()},
            "5_lines_of_code": loc,
            "6_always_on_h_per_month": {"realtime": 730.0, "batch": 0.0,
                                        "batch_busy_h_per_month_at_15min": idle["batch_busy_h_per_month"]},
            "7_replay_s": {"realtime": rep["realtime"]["seconds"], "batch": rep["batch"]["seconds"],
                           "emails": rep["realtime"]["emails"]},
        },
    }
    passing = []
    for path, lat_ok, rec_ok in (("realtime", rt["passes"], rec["realtime"]["passes"]),
                                 ("batch", sim15["passes"] and bt["passes"], rec["batch"]["passes"])):
        if lat_ok and par["identical"] and rec_ok:
            passing.append(path)
    choice = min(passing, key=lambda p: len(MOVING_PARTS[p])) if passing else None
    summary["passing_1_to_3"] = passing
    summary["decision"] = choice
    _save("summary", summary)
    print(json.dumps(summary, indent=2, default=str))
    return summary
