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
from .tracing import Tracer, get_tracer
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


def _match_all(lines, offer_id, known_at, ref, ref_date, resolutions, currency):
    match_rows, facts = [], []
    for line in lines:
        line_id = f"{offer_id}:L{line.line_no:02d}"
        m = match.match_line(line, line_id, ref, ref_date, known_at)
        if line.line_no in resolutions:
            pick = resolutions[line.line_no]
            m = match.analyst_pick(m, ref.funds[pick["fund_id"]], ref, pick["user_name"])
        rate, fx_row = ref.fx_rate(line.currency or currency, ref_date)
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
    return match_rows, facts


def compute(parsed: ParsedOffer, lines: list[ParsedLine], offer_id: str, known_at: dt.datetime,
            ref: Reference, rb: rulebook.Rulebook, plan: dict, resolutions: dict[int, dict] | None = None,
            tr: Tracer | None = None):
    """Pure-ish core: matching, metrics, verdict. Returns rows ready for gold.

    `resolutions` maps line_no to an analyst's pick ({fund_id, user_name}); code still applies the tier rules."""
    tr = tr or Tracer(None)
    resolutions = resolutions or {}
    ref_date = parsed.reference_date
    with tr.step("match", as_type="retriever",
                 input={"reference_date": ref_date.isoformat(), "known_at": str(known_at), "lines": len(lines),
                        "analyst_picks": {str(k): v["fund_id"] for k, v in resolutions.items()}}) as obs:
        match_rows, facts = _match_all(lines, offer_id, known_at, ref, ref_date, resolutions, parsed.currency)
        obs.update(output=[{"line": r["label"], "tier": r["tier"], "fund_id": r["fund_id"], "manager_id": r["manager_id"],
                            "confidence": r["confidence"], "resolved": r["resolved"], "method": r["method"]}
                           for r in match_rows])
    with tr.step("metrics", input={"lines": len(facts), "asking_price_text": parsed.asking_price_text}) as o:
        mvals = metrics.offer_metrics(facts, parsed.asking_price_text, plan)
        o.update(output={k: v.value for k, v in mvals.items()}, metadata={"versions": {k: v.version for k, v in mvals.items()}})
    with tr.step("verdict", input={"rulebook_version": rb.version, "rulebook_sha": rb.sha}) as o:
        verdict = rulebook.evaluate(rb, {k: v.value for k, v in mvals.items()})
        o.update(output={"verdict": verdict.verdict, "band": verdict.band, "score": verdict.score,
                         "threshold": verdict.threshold, "veto_rule": verdict.veto_rule, "headline": verdict.headline,
                         "rules": [{"rule": r.rule_id, "status": r.status, "value": r.value} for r in verdict.reasons]})

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


def write_brief(con, ws: Workspace, offer_id: str, tr: Tracer | None = None) -> tuple[Path, bool, list[str]]:
    tr = tr or Tracer(None)
    with tr.step("brief", input={"offer_id": offer_id}) as o:
        html = brief_mod.render(con, offer_id)
        o.update(output={"bytes": len(html)})
    with tr.step("validator", as_type="guardrail", input={"offer_id": offer_id}) as o:
        problems = brief_mod.validate(html, lambda t, r, m: store.lookup(con, t, r, m))
        o.update(output={"passed": not problems, "problems": problems[:20]},
                 level="ERROR" if problems else "DEFAULT")
    path = ws.briefs_dir / (f"{offer_id}.html" if not problems else f"{offer_id}.INVALID.html")
    _atomic_write(path, html.encode())
    con.execute("DELETE FROM gold_briefs WHERE offer_id = ?", [offer_id])
    store.insert(con, "gold_briefs", dict(offer_id=offer_id, path=path.name,
                                          sha256=hashlib.sha256(html.encode()).hexdigest(), valid=not problems,
                                          problems=json.dumps(problems), written_at=store.now()))
    return path, not problems, problems


def process_email(raw: bytes, ws: Workspace, parser: Parser | None = None, channel: str = "cli",
                  rulebook_path: Path = RULEBOOK_PATH, tracer: Tracer | None = None) -> Result:
    t0 = time.perf_counter()
    tr = tracer or get_tracer()
    content = read_email(raw)
    with tr.run(TRACE_NAME, input={"subject": content.subject, "sender": content.sender,
                                   "received": content.date.isoformat(), "message_id": content.message_id},
                session_id=content.message_id, metadata={"channel": channel}, tags=[f"channel:{channel}"]) as root:
        con = store.connect(ws)
        try:
            with tr.step("intake", input={"channel": channel, "message_id": content.message_id,
                                          "bytes": len(raw)}) as o:
                existing = record_bronze(con, ws, raw, content, channel)
                dup = bool(existing and existing["status"] in ("done", "parse_failed", "brief_invalid"))
                o.update(output={"status": "duplicate" if dup else ("retry" if existing else "new"),
                                 "attachments": [t.filename for t in content.tables]})
            if dup:
                root.update(output={"status": "duplicate"})
                return Result("duplicate", content.message_id, seconds=time.perf_counter() - t0)
            con.execute("UPDATE bronze_emails SET attempts = attempts + 1 WHERE message_id = ?", [content.message_id])
            r = _run(con, ws, content, parser or get_parser(), rulebook_path, t0, tr, root, channel)
            root.update(output={"status": r.status, "offer_id": r.offer_id, "verdict": r.verdict,
                                "headline": r.headline})
            return r
        finally:
            con.close()


TRACE_NAME = "quick-look-offer"


def _parse_traced(parser, content, tr: Tracer):
    """Run the parser inside a 'parse' step: a generation for the model parser, a span offline."""
    if parser.offline:
        kw = dict(as_type="span", input={"parser": parser.name, "version": parser.version})
    else:
        msgs = parser.request_messages(content) if hasattr(parser, "request_messages") else None
        kw = dict(as_type="generation", input=msgs, model=getattr(parser, "model", None) or parser.name.split(":")[-1],
                  metadata={"prompt_version": parser.version, "parser": parser.name})
    with tr.step("parse", **kw) as o:
        try:
            parsed, err = parser.parse(content), None
        except (ParseError, ValidationError) as e:
            parsed, err = None, e
        call = getattr(parser, "last_call", None) or {}
        upd: dict = {}
        if call.get("usage"):
            upd["usage_details"] = call["usage"]
        if call.get("model"):
            upd["model"] = call["model"]
        if call.get("fell_back_offline"):
            upd.update(level="WARNING", status_message="model unreachable; offline parser used")
        if err is not None:
            upd.update(level="ERROR", status_message=str(err).splitlines()[0])
        else:
            upd["output"] = {"code_name": parsed.code_name, "reference_date": parsed.reference_date.isoformat(),
                             "currency": parsed.currency, "asking_price_text": parsed.asking_price_text,
                             "lines": parsed.lines}
        o.update(**upd)
    if err is not None:
        raise err
    return parsed


def _run(con, ws, content, parser, rulebook_path, t0, tr: Tracer | None = None, root=None,
         channel: str = "cli") -> Result:
    tr = tr or Tracer(None)
    mid = content.message_id
    try:
        parsed = _parse_traced(parser, content, tr)
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
                                                   load_plan(), _resolutions(con, offer_id), tr)
    _write_gold(con, offer_id, content, parsed, parser, lines, rejects, match_rows, mvals, verdict, exposure)
    slow = float(os.environ.get("QUICKLOOK_SLOW_SECONDS", "0") or 0)
    if slow:
        time.sleep(slow)  # used by the recovery test to kill a run half-way
    path, ok, _ = write_brief(con, ws, offer_id, tr)
    _finish_trace(con, offer_id, root, ok, verdict, channel)
    status = "done" if ok else "brief_invalid"
    con.execute("UPDATE bronze_emails SET status = ?, processed_at = ? WHERE message_id = ?",
                [status, store.now(), mid])
    return Result(status, mid, offer_id, parsed.code_name, verdict.verdict, verdict.headline, path,
                  time.perf_counter() - t0)


def _resolutions(con, offer_id: str) -> dict[int, dict]:
    """Latest analyst pick per line for this offer."""
    out: dict[int, dict] = {}
    for r in store.rows(con, "SELECT * FROM line_resolutions WHERE offer_id = ? ORDER BY created_at", [offer_id]):
        out[r["line_no"]] = r
    return out


def _finish_trace(con, offer_id: str, root, ok: bool, verdict, kind: str) -> None:
    """Scores on the run's trace, and remember the trace so later team feedback lands on it."""
    if root is None or root.trace_id is None:
        return
    root.score_trace(name="validator_pass", value=1 if ok else 0, data_type="BOOLEAN")
    root.score_trace(name="data_matched", value=float(verdict.data_matched), data_type="NUMERIC")
    root.score_trace(name="verdict_band", value=verdict.band, data_type="CATEGORICAL",
                     comment=f"{verdict.verdict}: {verdict.headline}")
    con.execute("DELETE FROM offer_traces WHERE trace_id = ?", [root.trace_id])
    store.insert(con, "offer_traces", dict(trace_id=root.trace_id, offer_id=offer_id, kind=kind,
                                           created_at=store.now()))


def _offer_and_verdict(con, offer: str) -> tuple[dict, dict]:
    o = store.resolve_offer(con, offer)
    if not o:
        raise KeyError(f"No offer called {offer}.")
    return o, store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [o["offer_id"]])


def record_override(ws: Workspace, offer: str, verdict: str, reason: str, user: str,
                    tracer: Tracer | None = None) -> dict:
    """Store a team override with who, when, why, and the rulebook it overrules. Not learned from.
    It is also sent as feedback on the offer's trace: an override is the team's label."""
    import uuid

    tr = tracer or get_tracer()
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValueError("An override needs a reason (at least a few words).")
    new = {"pass": "PASS", "look": "LOOK CLOSER"}[verdict.lower()]
    con = store.connect(ws)
    try:
        o, v = _offer_and_verdict(con, offer)
        row = dict(override_id=uuid.uuid4().hex[:12], offer_id=o["offer_id"], user_name=user,
                   created_at=store.now(), rulebook_version=v["rulebook_version"], rulebook_sha=v["rulebook_sha"],
                   original_verdict=v["verdict"], new_verdict=new, reason=reason)
        store.insert(con, "overrides", row)
        path, _, _ = write_brief(con, ws, o["offer_id"])
        tid = store.latest_trace_id(con, o["offer_id"])
        meta = {"user": user, "rulebook_version": v["rulebook_version"], "original_verdict": v["verdict"]}
        tr.feedback(tid, "team_decision", "override", "CATEGORICAL", reason, f"{row['override_id']}-d", meta)
        tr.feedback(tid, "verdict_agrees", 1 if new == v["verdict"] else 0, "BOOLEAN", reason,
                    f"{row['override_id']}-a", meta)
        tr.feedback(tid, "team_label", new, "CATEGORICAL", reason, f"{row['override_id']}-l", meta)
        return dict(row, code_name=o["code_name"], brief_path=path, trace_id=tid)
    finally:
        con.close()


def record_keep(ws: Workspace, offer: str, user: str, tracer: Tracer | None = None) -> dict:
    """The analyst agrees with the verdict. Stored with who, when and the rulebook version."""
    import uuid

    tr = tracer or get_tracer()
    con = store.connect(ws)
    try:
        o, v = _offer_and_verdict(con, offer)
        row = dict(decision_id=uuid.uuid4().hex[:12], offer_id=o["offer_id"], user_name=user,
                   created_at=store.now(), verdict=v["verdict"], band=v["band"],
                   rulebook_version=v["rulebook_version"], rulebook_sha=v["rulebook_sha"])
        store.insert(con, "decisions", row)
        path, _, _ = write_brief(con, ws, o["offer_id"])
        tid = store.latest_trace_id(con, o["offer_id"])
        meta = {"user": user, "rulebook_version": v["rulebook_version"]}
        tr.feedback(tid, "team_decision", "keep", "CATEGORICAL", None, f"{row['decision_id']}-d", meta)
        tr.feedback(tid, "verdict_agrees", 1, "BOOLEAN", None, f"{row['decision_id']}-a", meta)
        tr.feedback(tid, "team_label", v["verdict"], "CATEGORICAL", None, f"{row['decision_id']}-l", meta)
        return dict(row, code_name=o["code_name"], brief_path=path, trace_id=tid)
    finally:
        con.close()


def record_resolve(ws: Workspace, offer: str, line_no: int, fund_id: str, user: str,
                   tracer: Tracer | None = None) -> dict:
    """The analyst says which fund a line is. Verdict and confidence are recomputed from silver."""
    import uuid

    tr = tracer or get_tracer()
    ref = load_reference(ws)
    con = store.connect(ws)
    try:
        o, before = _offer_and_verdict(con, offer)
        oid = o["offer_id"]
        line = store.one(con, "SELECT * FROM silver_offer_lines WHERE offer_id = ? AND line_no = ?", [oid, line_no])
        gm = store.one(con, "SELECT * FROM gold_line_matches WHERE offer_id = ? AND line_no = ?", [oid, line_no])
        if not line or not gm:
            raise KeyError(f"Project {o['code_name']} has no Fund {line_no}.")
        fits = ref.candidates(line["vintage"], line["strategy"], line["region"])
        if fund_id not in {f["fund_id"] for f in fits}:
            listed = json.loads(gm["candidates_json"]) or [dict(fund_id=f["fund_id"], fund_name=f["name"]) for f in fits]
            names = ", ".join(f"{c['fund_id']} {c['fund_name']}" for c in listed[:5]) or "none"
            raise ValueError(f"{fund_id} does not fit {gm['label']}'s description. Candidates: {names}.")
        fund = ref.funds[fund_id]
        row = dict(resolution_id=uuid.uuid4().hex[:12], offer_id=oid, line_no=line_no, label=gm["label"],
                   fund_id=fund_id, fund_name=fund["name"], user_name=user, created_at=store.now(),
                   previous_tier=gm["tier"], previous_method=gm["method"])
        store.insert(con, "line_resolutions", row)
        tid = store.latest_trace_id(con, oid)
    finally:
        con.close()
    tr.feedback(tid, "line_resolved", 1, "BOOLEAN",
                f"{gm['label']}: code said {gm['tier']} ({gm['method']}); analyst picked {fund_id}",
                f"{row['resolution_id']}-r", {"user": user, "line_no": line_no, "fund_id": fund_id,
                                              "previous_tier": gm["tier"]})
    after = recompute(ws, oid, tr, kind="resolve")
    return dict(row, code_name=o["code_name"], before=before, after=after["verdict"], brief_path=after["brief_path"])


def recompute(ws: Workspace, offer: str, tracer: Tracer | None = None, kind: str = "recompute") -> dict:
    """Re-run match, metrics, verdict and brief from silver (no new parse), keeping team rows."""
    from types import SimpleNamespace

    tr = tracer or get_tracer()
    con = store.connect(ws)
    try:
        o = store.resolve_offer(con, offer)
        oid = o["offer_id"]
        cols = [c for c in ParsedLine.model_fields]
        lines = [ParsedLine(**{c: r[c] for c in cols})
                 for r in store.rows(con, "SELECT * FROM silver_offer_lines WHERE offer_id = ? ORDER BY line_no", [oid])]
        rejects = store.rows(con, "SELECT * FROM silver_rejects WHERE offer_id = ? ORDER BY row_id", [oid])
        parsed = ParsedOffer(code_name=o["code_name"], seller_type=o["seller_type"], reference_date=o["reference_date"],
                             currency=o["currency"], total_value=o["total_value"],
                             asking_price_text=o["asking_price_text"], bid_deadline=o["bid_deadline"])
        content = SimpleNamespace(message_id=o["message_id"], date=o["email_date"], sender=o["sender"])
        parser = SimpleNamespace(name=o["parser"], version=o["parser_version"], offline=o["parser_offline"])
        res = _resolutions(con, oid)
        with tr.run(f"{TRACE_NAME}-{kind}", input={"offer_id": oid, "analyst_picks": {str(k): v["fund_id"] for k, v in res.items()}},
                    session_id=o["message_id"], metadata={"channel": kind}, tags=[f"channel:{kind}"]) as root:
            match_rows, mvals, verdict, exposure = compute(parsed, lines, oid, o["email_date"], load_reference(ws),
                                                           rulebook.load(RULEBOOK_PATH), load_plan(), res, tr)
            _write_gold(con, oid, content, parsed, parser, lines, rejects, match_rows, mvals, verdict, exposure)
            path, ok, _ = write_brief(con, ws, oid, tr)
            _finish_trace(con, oid, root, ok, verdict, kind)
            root.update(output={"verdict": verdict.verdict, "band": verdict.band, "headline": verdict.headline})
        return dict(verdict=store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [oid]), brief_path=path)
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
