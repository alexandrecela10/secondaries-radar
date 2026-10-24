"""Test sender: delivers .eml files by SMTP or into a Maildir and records send times."""
from __future__ import annotations

import re
import smtplib
import socket
import time
import uuid
from pathlib import Path


def with_suffix(raw: bytes, suffix: str | None) -> bytes:
    """Give a copy a new Message-ID (for repeated measurement rounds)."""
    if not suffix:
        return raw
    return re.sub(rb"(?im)^(Message-ID:\s*<)([^@>]+)", lambda m: m[1] + m[2] + b"-" + suffix.encode(), raw, count=1)


def message_id(raw: bytes) -> str:
    m = re.search(rb"(?im)^Message-ID:\s*<([^>]+)>", raw)
    return m[1].decode().strip().lower() if m else ""


def send_smtp(raw: bytes, host: str = "127.0.0.1", port: int = 2525, retries: int = 40,
              backoff: float = 0.25) -> dict:
    wire = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # SMTP lines end in CRLF
    sent_at = time.time()
    last = ""
    for attempt in range(1, retries + 1):
        try:
            with smtplib.SMTP(host, port, local_hostname="localhost", timeout=30) as s:
                s.sendmail("deals@broker.example", ["secondaries@ourfund.example"], wire)
            return dict(message_id=message_id(raw), sent_at=sent_at, acked_at=time.time(), attempts=attempt, ok=True)
        except smtplib.SMTPResponseException as e:
            if e.smtp_code >= 500:  # permanent: do not retry
                return dict(message_id=message_id(raw), sent_at=sent_at, attempts=attempt, ok=False,
                            error=f"{e.smtp_code} {e.smtp_error!r}")
            last = f"{e.smtp_code} {e.smtp_error!r}"
            time.sleep(backoff)
        except (smtplib.SMTPException, ConnectionError, socket.timeout, OSError) as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(backoff)
    return dict(message_id=message_id(raw), sent_at=sent_at, attempts=retries, ok=False, error=last)


def drop_maildir(raw: bytes, maildir: Path) -> dict:
    """Maildir delivery: write to tmp/, then rename into new/ so readers never see half a file."""
    for d in ("tmp", "new", "cur"):
        (maildir / d).mkdir(parents=True, exist_ok=True)
    name = f"{time.time():.6f}.{uuid.uuid4().hex[:8]}.quicklook"
    sent_at = time.time()
    (maildir / "tmp" / name).write_bytes(raw)
    (maildir / "tmp" / name).rename(maildir / "new" / name)
    return dict(message_id=message_id(raw), sent_at=sent_at, ok=True, attempts=1)


def files(paths: list[Path]) -> list[bytes]:
    return [p.read_bytes() for p in paths]
