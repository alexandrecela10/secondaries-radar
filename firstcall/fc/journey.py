"""The partner's Monday steps that are not the weekly list itself: find a pension, see its history,
record a decision. Spec: outputs/journey-maps/first-call-partner-journey.md.

Decisions are stored with who, when, the rulebook version and the score at that moment, and sent to
Langfuse as a user-feedback score on the latest batch-run trace when tracing is on.
"""
from __future__ import annotations

import getpass
import hashlib
import os
from datetime import datetime, time, timedelta, timezone

from . import pensions, store, tracing
from .config import rulebook
from .scoring import inputs, score

ACTIONS = ("prepare", "dismiss")


def resolve(name: str) -> str | None:
    """Pension id from an id, an alias or a short form: 'nyc_funds', 'WSIB', 'Washington State', 'nyc'."""
    key = name.strip().lower()
    if key in pensions.ALL:
        return key
    for p in pensions.ALL.values():
        if key in (a.lower() for a in p.aliases) or key == p.name.lower():
            return p.id
    hit = pensions.link(name).pension_id or pensions.link(f"{name} pension").pension_id
    if hit:
        return hit
    partial = [p for p in pensions.ALL if key in p]
    return partial[0] if len(partial) == 1 else None


def unknown(name: str) -> str:
    return f"Unknown pension '{name}'. Run `firstcall pensions` for ids and aliases."


def who() -> str:
    return os.environ.get("FC_USER") or getpass.getuser()


def _eod(d) -> datetime:
    return datetime.combine(d, time(23, 59), timezone.utc)


def timeline_text(con, pension_id: str, as_of: datetime, weeks: int = 26, width: int = 40) -> str:
    """Weekly score as a bar, the PREPARE line as '|', and each signal on the week it was published."""
    rb = rulebook()
    sigs = store.load_signals(con, pension_id)
    sig_inputs = inputs(sigs)
    end = as_of.date()
    starts = [end - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]
    line_col = round(rb.prepare_threshold * width)
    p = pensions.ALL[pension_id]
    out = [f"TIMELINE {p.name} ({pension_id})   {weeks} weeks to {end}   rulebook v{rb.version}",
           f"one row per week, score at the end of the week; '|' is PREPARE at {rb.prepare_threshold}", ""]
    for i, d in enumerate(starts):
        sc = score(sig_inputs, _eod(d), rb)
        filled = round(sc.score * width)
        bar = "".join("#" if c < filled else ("|" if c == line_col else " ") for c in range(width + 1))
        if filled > line_col:
            bar = bar[:line_col] + "|" + bar[line_col + 1:]
        prev = starts[i - 1] if i else d - timedelta(days=7)
        new = [s for s in sigs if prev < s["published_at"].date() <= d]
        out.append(f"{d}  {sc.score:.2f} {bar} {sc.verdict}")
        for s in new:
            tag = " closed, evidence only" if s.get("completed") else ""
            out.append(f"{'':>17}+ {s['published_at'].date()} {s['signal_type']}{tag} "
                       f"\"{s['headline'][:60]}\" ({s['outlet'] or 'unknown outlet'})")
    older = [s for s in sigs if s["published_at"].date() <= starts[0] - timedelta(days=7)]
    if older:
        out.append(f"\n{len(older)} earlier signal(s) before this range; use --weeks to see more.")
    if not sigs:
        out.append("No signal on record for this pension.")
    return "\n".join(out)


def record_decision(con, pension_id: str, action: str, *, owner: str | None = None, reason: str | None = None,
                    as_of: datetime | None = None) -> dict:
    """Store a prepare or dismiss decision and send it to Langfuse as a categorical feedback score."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    if action == "dismiss" and not (reason and reason.strip()):
        raise ValueError("a dismissal needs --reason, so the rule review can use it")
    rb = rulebook()
    as_of = as_of or datetime.now(timezone.utc)
    sc = score(inputs(store.load_signals(con, pension_id)), as_of, rb)
    row = con.execute("SELECT trace_id FROM runs WHERE trace_id IS NOT NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    trace_id = row[0] if row else None
    at = datetime.now(timezone.utc)
    by = who()
    did = hashlib.sha1(f"{pension_id}|{action}|{at.isoformat()}".encode()).hexdigest()[:16]
    con.execute("INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [did, pension_id, action, owner, reason, by, at, rb.version, sc.score, sc.verdict, trace_id])
    sent = tracing.score_trace(
        trace_id, "partner_decision", action, data_type="CATEGORICAL",
        comment=reason or (f"owner {owner}" if owner else None),
        metadata={"pension_id": pension_id, "owner": owner, "decided_by": by, "rulebook_version": rb.version,
                  "score": round(sc.score, 3), "verdict": sc.verdict, "decision_id": did})
    tracing.flush()
    return {"decision_id": did, "pension_id": pension_id, "action": action, "owner": owner, "reason": reason,
            "decided_by": by, "decided_at": at, "rulebook_version": rb.version, "score": sc.score,
            "verdict": sc.verdict, "sent_to_langfuse": sent}


def last_decision(con, pension_id: str, as_of: datetime | None = None) -> dict | None:
    """The latest decision made on or before `as_of` (point in time, like the score)."""
    cur = con.execute("SELECT * FROM decisions WHERE pension_id = ? AND decided_at <= ? ORDER BY decided_at DESC LIMIT 1",
                      [pension_id, as_of or datetime.now(timezone.utc)])
    r = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], r)) if r else None


def decision_note(d: dict | None) -> str:
    if not d:
        return ""
    if d["action"] == "prepare":
        return f"[prepare: {d['owner'] or 'no owner'}, {d['decided_at'].date()}]"
    return f"[dismissed {d['decided_at'].date()}: {d['reason'][:40]}]"
