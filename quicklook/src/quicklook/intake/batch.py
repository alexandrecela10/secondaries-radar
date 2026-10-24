"""Batch path: scan a Maildir every N minutes and run the pipeline on new mail.

A message moves from new/ to cur/ only after the pipeline finishes. If the job
dies mid-run, the message is still in new/ and the next run picks it up; the
Message-ID makes the second pass safe.
"""
from __future__ import annotations

import argparse
import os
import signal
import time
from pathlib import Path

from .. import pipeline
from ..config import default_workspace
from ..parse import get_parser


def run_once(ws, maildir: Path, parser) -> int:
    new, cur = maildir / "new", maildir / "cur"
    for d in (new, cur, maildir / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    pipeline.resume_pending(ws, parser)
    done = 0
    for name in sorted(os.listdir(new)):
        pipeline.process_email((new / name).read_bytes(), ws, parser, channel="maildir")
        os.replace(new / name, cur / f"{name}:2,S")
        done += 1
    return done


def run_loop(workspace: str, maildir: Path, interval: float, offline: bool, once: bool = False) -> None:
    ws = default_workspace(workspace)
    parser = get_parser(force_offline=offline)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    print(f"Batch intake on {maildir} every {interval:g}s, workspace {ws.root}", flush=True)
    while not stopping:
        run_once(ws, maildir, parser)
        if once:
            return
        end = time.monotonic() + interval
        while not stopping and time.monotonic() < end:
            time.sleep(min(0.2, interval))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", default="batch")
    p.add_argument("--maildir", default="outbox/maildir")
    p.add_argument("--interval", type=float, default=900, help="seconds between scans (default 15 minutes)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--offline", action="store_true")
    a = p.parse_args()
    run_loop(a.workspace, Path(a.maildir), a.interval, a.offline, a.once)


if __name__ == "__main__":
    main()
