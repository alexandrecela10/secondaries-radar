"""One function, every intake path: raw email bytes in, brief out.

bronze (as received) -> parse -> silver (validated rows) -> match -> metrics ->
rules -> gold -> brief -> validator. Idempotent on Message-ID: a second copy of
an email that was fully processed changes nothing.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from . import brief as brief_mod
from . import match, metrics, rulebook, store
from .config import PLAN_PATH, RULEBOOK_PATH, Workspace
from .emailio import EmailContent, read_email
from .models import ParsedLine, ParsedOffer, Tier
from .parse import ParseError, Parser, get_parser
from .reference import Reference


@dataclass
class Result:
    status: str  # done | duplicate | parse_failed | brief_invalid
    message_id: str
    offer_id: str | None = None
    code_name: str | None = None
    verdict: str | None = None
    headline: str | None = None
    brief_path: Path | None = None
    seconds: float = 0.0


@lru_cache(maxsize=4)
def _reference(path: str, mtime: float) -> Reference:
    return Reference(Path(path))


def load_reference(ws: Workspace) -> Reference:
    p = ws.reference_dir / "fund_series.parquet"
    return _reference(str(ws.reference_dir), p.stat().st_mtime)


def load_plan(path: Path = PLAN_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def offer_id_for(code: str, message_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", code.lower()).strip("-")
    return f"{slug}-{hashlib.sha1(message_id.encode()).hexdigest()[:6]}"


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def record_bronze(con, ws: Workspace, raw: bytes, content: EmailContent, channel: str) -> dict | None:
    """Store the email as received. Returns the existing row if we have seen this Message-ID."""
    existing = store.one(con, "SELECT * FROM bronze_emails WHERE message_id = ?", [content.message_id])
    if existing:
        return existing
    sha = hashlib.sha256(raw).hexdigest()
    path = ws.bronze_dir / f"{sha}.eml"
    if not path.exists():
        _atomic_write(path, raw)
    store.insert(con, "bronze_emails", dict(message_id=content.message_id, sha256=sha, raw_path=path.name,
                                            channel=channel, received_at=store.now(), status="received",
                                            error=None, processed_at=None, attempts=0))
    return None


def _validate_lines(offer_id: str, parsed: ParsedOffer) -> tuple[list[ParsedLine], list[dict]]:
    good, rejects = [], []
    for raw in parsed.lines:
        raw = dict(raw)
        if raw.get("currency") is None:
            raw["currency"] = parsed.currency
        try:
            line = ParsedLine(**raw)
        except ValidationError as e:
            err = e.errors()[0]
            rejects.append(dict(row_id=f"{offer_id}:R{len(rejects) + 1}", offer_id=offer_id,
                                line_ref=str(raw.get("label") or raw.get("line_no")),
                                reason=f"{'.'.join(map(str, err['loc'])) or 'line'}: {err['msg']}",
                                raw=json.dumps(raw, default=str), logged_at=store.now()))
            continue
        if line.value is None:
            rejects.append(dict(row_id=f"{offer_id}:R{len(rejects) + 1}", offer_id=offer_id, line_ref=line.label,
                                reason="no value given; cannot weigh this line",
                                raw=json.dumps(raw, default=str), logged_at=store.now()))
            continue
        good.append(line)
    good.sort(key=lambda l: l.line_no)
    return good, rejects


def compute(parsed: ParsedOffer, lines: list[ParsedLine], offer_id: str, known_at: dt.datetime,
            ref: Reference, rb: rulebook.Rulebook, plan: dict):
    """Pure-ish core: matching, metrics, verdict. Returns rows ready for gold."""
    ref_date = parsed.reference_date
    match_rows, facts = [], []
    for line in lines:
        line_id = f"{offer_id}:L{line.line_no:02d}"
        m = match.match_line(line, line_id, ref, ref_date, known_at)
        rate, fx_row = ref.fx_rate(line.currency or parsed.currency, ref_date)
        value_usd = line.value * rate
        lo = hi = None
        method = our_row = None
        if m.tier == Tier.SAME_FUND:
            fund = ref.funds[m.fund_id]
            latest = ref.latest(m.fund_id, known_at)
            if latest is not None:
                our_vpc = latest.nav / fund["size_m"]
                # value / commitment is a ratio, so currency cancels out
                implied = line.commitment * our_vpc if line.commitment else line.value
                lo, hi, method = metrics.line_price_range(fund["strategy"], fund["vintage"], ref_date,
                                                          line.value, implied)
                method += f"; our report for {latest.as_of_date.isoformat()}"
                our_row = latest.row_id
        fund_name = ref.funds[m.fund_id]["name"] if m.fund_id else None
        mgr_name = ref.manager_name(m.manager_id) if m.manager_id else None
        match_rows.append(dict(
            line_id=line_id, offer_id=offer_id, line_no=line.line_no, label=line.label, tier=m.tier.value,
            fund_id=m.fund_id, fund_name=fund_name, manager_id=m.manager_id, manager_name=mgr_name,
            confidence=m.confidence, resolved=m.resolved, method=m.method,
            candidates_json=json.dumps([c.model_dump() for c in m.candidates]), flags_json=json.dumps(m.flags),
            value_usd=value_usd, fx_rate=rate, fx_row_id=fx_row, price_low=lo, price_high=hi, price_method=method,
            our_value_row=our_row, compared_series_row=m.compared_series_row, match_version=match.VERSION,
            price_version=metrics.LINE_PRICE_VERSION))
        facts.append(metrics.LineFact(line_id=line_id, value_usd=value_usd, strategy=line.strategy,
                                      region=line.region, vintage=line.vintage, tier=m.tier, resolved=m.resolved,
                                      match_confidence=m.confidence, price_low=lo, price_high=hi))
    mvals = metrics.offer_metrics(facts, parsed.asking_price_text, plan)
    verdict = rulebook.evaluate(rb, {k: v.value for k, v in mvals.items()})

    exposure = []
    mgrs = sorted({r["manager_id"] for r in match_rows if r["manager_id"] and r["tier"] != Tier.UNKNOWN.value})
    for mid in mgrs:
        held = [f for f in ref.funds.values() if f["manager_id"] == mid and f["owned"]]
        commit = value = 0.0
        for f in held:
            h = ref.holdings[f["fund_id"]]
            r_, _ = ref.fx_rate(f["currency"], ref_date)
            commit += h["commitment"] * r_
            latest = ref.latest(f["fund_id"], known_at)
            if latest:
                value += latest.nav * h["commitment"] / f["size_m"] * r_
        exposure.append(dict(row_id=f"{offer_id}:{mid}", offer_id=offer_id, manager_id=mid,
                             manager_name=ref.manager_name(mid), funds_held=len(held), our_commitment_usd=commit,
                             our_value_usd=value, version=metrics.EXPOSURE_VERSION))
    return match_rows, mvals, verdict, exposure


def _write_gold(con, offer_id, content, parsed, parser, lines, rejects, match_rows, mvals, verdict, exposure):
    con.execute("BEGIN TRANSACTION")
    try:
        store.delete_offer(con, offer_id)
        store.insert(con, "silver_offers", dict(
            offer_id=offer_id, message_id=content.message_id, code_name=parsed.code_name,
            seller_type=parsed.seller_type, reference_date=parsed.reference_date,
            email_date=content.date.replace(tzinfo=None), sender=content.sender, currency=parsed.currency,
            total_value=parsed.total_value, asking_price_text=parsed.asking_price_text,
            bid_deadline=parsed.bid_deadline, parser=parser.name, parser_version=parser.version,
            parser_offline=parser.offline))
        for l in lines:
            store.insert(con, "silver_offer_lines", dict(line_id=f"{offer_id}:L{l.line_no:02d}", offer_id=offer_id,
                                                         **l.model_dump()))
        for r in rejects:
            store.insert(con, "silver_rejects", r)
        for r in match_rows:
            store.insert(con, "gold_line_matches", r)
        for m in mvals.values():
            store.insert(con, "gold_offer_metrics", dict(row_id=f"{offer_id}:{m.name}", offer_id=offer_id,
                                                         metric=m.name, version=m.version, value=m.value, note=m.note))
        for e in exposure:
            store.insert(con, "gold_exposure", e)
        for i, rr in enumerate(verdict.reasons):
            store.insert(con, "gold_rule_results", dict(
                row_id=f"{offer_id}:{rr.rule_id}", offer_id=offer_id, rule_id=rr.rule_id, description=rr.description,
                metric=rr.metric, op=rr.op, threshold=rr.threshold, weight=rr.weight, veto=rr.veto,
                status=rr.status, value=rr.value, rank=i, rulebook_version=verdict.rulebook_version))
        store.insert(con, "gold_verdicts", dict(
            offer_id=offer_id, verdict=verdict.verdict, score=verdict.score, threshold=verdict.threshold,
            veto_rule=verdict.veto_rule, data_matched=verdict.data_matched, margin=verdict.margin,
            band=verdict.band, headline=verdict.headline, headline_json=json.dumps(verdict.headline_segments),
            rulebook_version=verdict.rulebook_version,
            rulebook_sha=verdict.rulebook_sha, decided_at=content.date.replace(tzinfo=None)))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def write_brief(con, ws: Workspace, offer_id: str) -> tuple[Path, bool, list[str]]:
    html = brief_mod.render(con, offer_id)
    problems = brief_mod.validate(html, lambda t, r, m: store.lookup(con, t, r, m))
    path = ws.briefs_dir / (f"{offer_id}.html" if not problems else f"{offer_id}.INVALID.html")
    _atomic_write(path, html.encode())
    con.execute("DELETE FROM gold_briefs WHERE offer_id = ?", [offer_id])
    store.insert(con, "gold_briefs", dict(offer_id=offer_id, path=path.name,
                                          sha256=hashlib.sha256(html.encode()).hexdigest(), valid=not problems,
                                          problems=json.dumps(problems), written_at=store.now()))
    return path, not problems, problems


def process_email(raw: bytes, ws: Workspace, parser: Parser | None = None, channel: str = "cli",
                  rulebook_path: Path = RULEBOOK_PATH) -> Result:
    t0 = time.perf_counter()
    content = read_email(raw)
    con = store.connect(ws)
    try:
        existing = record_bronze(con, ws, raw, content, channel)
        if existing and existing["status"] in ("done", "parse_failed", "brief_invalid"):
            return Result("duplicate", content.message_id, seconds=time.perf_counter() - t0)
        con.execute("UPDATE bronze_emails SET attempts = attempts + 1 WHERE message_id = ?", [content.message_id])
        return _run(con, ws, content, parser or get_parser(), rulebook_path, t0)
    finally:
        con.close()


def _run(con, ws, content, parser, rulebook_path, t0) -> Result:
    mid = content.message_id
    try:
        parsed = parser.parse(content)
    except (ParseError, ValidationError) as e:
        reason = str(e).splitlines()[0]
        con.execute("DELETE FROM review_queue WHERE message_id = ?", [mid])
        store.insert(con, "review_queue", dict(message_id=mid, reason=reason, logged_at=store.now()))
        path = ws.briefs_dir / f"unreadable-{hashlib.sha1(mid.encode()).hexdigest()[:6]}.html"
        _atomic_write(path, brief_mod.render_parse_failure(content.subject, reason).encode())
        con.execute("UPDATE bronze_emails SET status = 'parse_failed', error = ?, processed_at = ? WHERE message_id = ?",
                    [reason, store.now(), mid])
        return Result("parse_failed", mid, brief_path=path, headline=f"Could not read this offer: {reason}.",
                      seconds=time.perf_counter() - t0)
    offer_id = offer_id_for(parsed.code_name, mid)
    lines, rejects = _validate_lines(offer_id, parsed)
    rb = rulebook.load(rulebook_path)
    match_rows, mvals, verdict, exposure = compute(parsed, lines, offer_id, content.date, load_reference(ws), rb,
                                                   load_plan())
    _write_gold(con, offer_id, content, parsed, parser, lines, rejects, match_rows, mvals, verdict, exposure)
    slow = float(os.environ.get("QUICKLOOK_SLOW_SECONDS", "0") or 0)
    if slow:
        time.sleep(slow)  # used by the recovery test to kill a run half-way
    path, ok, _ = write_brief(con, ws, offer_id)
    status = "done" if ok else "brief_invalid"
    con.execute("UPDATE bronze_emails SET status = ?, processed_at = ? WHERE message_id = ?",
                [status, store.now(), mid])
    return Result(status, mid, offer_id, parsed.code_name, verdict.verdict, verdict.headline, path,
                  time.perf_counter() - t0)


def record_override(ws: Workspace, offer: str, verdict: str, reason: str, user: str) -> dict:
    """Store a team override with who, when, why, and the rulebook it overrules. Not learned from."""
    import uuid

    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValueError("An override needs a reason (at least a few words).")
    new = {"pass": "PASS", "look": "LOOK CLOSER"}[verdict.lower()]
    con = store.connect(ws)
    try:
        o = store.resolve_offer(con, offer)
        if not o:
            raise KeyError(f"No offer called {offer}.")
        v = store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [o["offer_id"]])
        row = dict(override_id=uuid.uuid4().hex[:12], offer_id=o["offer_id"], user_name=user,
                   created_at=store.now(), rulebook_version=v["rulebook_version"], rulebook_sha=v["rulebook_sha"],
                   original_verdict=v["verdict"], new_verdict=new, reason=reason)
        store.insert(con, "overrides", row)
        path, _, _ = write_brief(con, ws, o["offer_id"])
        return dict(row, code_name=o["code_name"], brief_path=path)
    finally:
        con.close()


def resume_pending(ws: Workspace, parser: Parser | None = None) -> list[Result]:
    """Re-run every email that reached bronze but not the end (crash recovery)."""
    con = store.connect(ws)
    pending = store.rows(con, "SELECT message_id, raw_path FROM bronze_emails WHERE status = 'received' ORDER BY received_at")
    con.close()
    out = []
    for p in pending:
        raw = (ws.bronze_dir / p["raw_path"]).read_bytes()
        out.append(process_email(raw, ws, parser, channel="resume"))
    return out


def replay(ws: Workspace, parser: Parser | None = None) -> list[Result]:
    """Rebuild silver, gold and briefs from bronze only. Overrides are kept."""
    con = store.connect(ws)
    emails = store.rows(con, "SELECT message_id, raw_path FROM bronze_emails ORDER BY received_at, message_id")
    con.execute("UPDATE bronze_emails SET status = 'received'")
    con.close()
    return [process_email((ws.bronze_dir / e["raw_path"]).read_bytes(), ws, parser, channel="replay") for e in emails]
