"""radar serve | batch | send."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import ROOT, offers_dir


def register(sub) -> None:
    s = sub.add_parser("serve", help="real-time path: local SMTP server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=2525)
    s.add_argument("--offline", action="store_true")
    s.set_defaults(fn=_serve, workspace_default="realtime")
    b = sub.add_parser("batch", help="batch path: scan a Maildir every N seconds")
    b.add_argument("--maildir", default=str(ROOT / "outbox" / "maildir"))
    b.add_argument("--interval", type=float, default=900)
    b.add_argument("--once", action="store_true")
    b.add_argument("--offline", action="store_true")
    b.set_defaults(fn=_batch)
    t = sub.add_parser("send", help="send test emails by SMTP or into a Maildir")
    t.add_argument("files", nargs="*")
    t.add_argument("--to", choices=["smtp", "maildir", "both"], default="smtp")
    t.add_argument("--port", type=int, default=2525)
    t.add_argument("--maildir", default=str(ROOT / "outbox" / "maildir"))
    t.add_argument("--suffix")
    t.set_defaults(fn=_send)
    m = sub.add_parser("measure", help="run the collection architecture experiment")
    m.add_argument("--quick", action="store_true", help="fewer rounds")
    m.set_defaults(fn=_measure)


def _serve(args) -> int:
    from .smtp_server import serve

    serve("realtime" if args.workspace == "main" else args.workspace, args.host, args.port, args.offline)
    return 0


def _batch(args) -> int:
    from .batch import run_loop

    run_loop("batch" if args.workspace == "main" else args.workspace, Path(args.maildir), args.interval,
             args.offline, args.once)
    return 0


def _send(args) -> int:
    from . import sender

    paths = [Path(f) for f in args.files] or sorted(offers_dir().glob("*.eml"))
    for p in paths:
        raw = sender.with_suffix(p.read_bytes(), args.suffix)
        if args.to in ("smtp", "both"):
            print(json.dumps(dict(file=p.name, path="smtp", **sender.send_smtp(raw, port=args.port))))
        if args.to in ("maildir", "both"):
            print(json.dumps(dict(file=p.name, path="maildir", **sender.drop_maildir(raw, Path(args.maildir)))))
    return 0


def _measure(args) -> int:
    from .. import measure

    measure.run(quick=args.quick)
    return 0
