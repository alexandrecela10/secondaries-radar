"""Real-time path: local SMTP server. Each received email runs the pipeline at once.

The server replies 250 only after the brief is written. If it dies mid-run the
sender gets no 250 and retries; the Message-ID makes the retry safe.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
from concurrent.futures import ThreadPoolExecutor

from aiosmtpd.controller import Controller

from .. import pipeline
from ..config import default_workspace
from ..parse import get_parser


class PipelineHandler:
    def __init__(self, ws, parser):
        self.ws, self.parser = ws, parser
        self.pool = ThreadPoolExecutor(max_workers=1)  # one writer for DuckDB

    async def handle_DATA(self, server, session, envelope):
        raw = envelope.original_content or envelope.content
        raw = raw.replace(b"\r\n", b"\n")  # local delivery stores LF line endings, as a Maildir does
        loop = asyncio.get_running_loop()
        try:
            r = await loop.run_in_executor(self.pool, pipeline.process_email, raw, self.ws, self.parser, "smtp")
        except Exception as e:  # tell the sender to retry later
            return f"451 processing failed: {type(e).__name__}"
        return f"250 OK {r.status}"


def serve(workspace: str, host: str, port: int, offline: bool) -> None:
    ws = default_workspace(workspace)
    parser = get_parser(force_offline=offline)
    pipeline.resume_pending(ws, parser)  # finish anything a crash left half-done
    # server_hostname avoids a reverse DNS lookup per connection
    controller = Controller(PipelineHandler(ws, parser), hostname=host, port=port, server_hostname="localhost")
    controller.start()
    print(f"SMTP intake listening on {host}:{port}, workspace {ws.root}", flush=True)
    stop = asyncio.Event()

    async def wait() -> None:
        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, stop.set)
        await stop.wait()

    try:
        asyncio.run(wait())
    finally:
        controller.stop()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", default="realtime")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2525)
    p.add_argument("--offline", action="store_true")
    a = p.parse_args()
    serve(a.workspace, a.host, a.port, a.offline)


if __name__ == "__main__":
    main()
