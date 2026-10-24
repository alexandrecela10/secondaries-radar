"""Read-side views for the associate journey: brief headline, one match side by side, the memo.

Each view reads gold rows only. The memo is built from sourced spans, checked by the same
validator as the brief, and printed as plain text.
"""
from __future__ import annotations

import json
import re

from markupsafe import Markup, escape

from . import brief as brief_mod
from . import match, store
from .config import Workspace
from .reference import Reference

RATIO_NAMES = {"pct_called": "% called", "dpi": "DPI", "tvpi": "TVPI", "value_per_commitment": "value / commitment"}


def _fmt_ratio(key: str, v: float) -> str:
    return f"{v * 100:.1f}%" if key == "pct_called" else (f"{v:.3f}" if key == "value_per_commitment" else f"{v:.3f}x")


def _fmt_gap(key: str, v: float) -> str:
    return f"{v * 100:+.1f} pt" if key == "pct_called" else f"{v:+.3f}"


def brief_lines(con, ws: Workspace, offer: str) -> list[str]:
    o = store.resolve_offer(con, offer)
    if not o:
        return [f"No offer called {offer}."]
    v = store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [o["offer_id"]])
    b = store.one(con, "SELECT * FROM gold_briefs WHERE offer_id = ?", [o["offer_id"]])
    out = [f"Project {o['code_name']}: received {o['email_date']:%Y-%m-%d %H:%M}, values at {o['reference_date']}",
           v["headline"]]
    dec = store.team_decision(con, o["offer_id"])
    if dec and dec["kind"] == "override":
        out.append(f"Team override: {dec['new_verdict']} by {dec['user_name']} ({dec['reason']})")
    elif dec:
        out.append(f"Team kept the verdict, by {dec['user_name']} at {dec['created_at']:%Y-%m-%d %H:%M}")
    if b:
        out.append(f"Brief: {ws.briefs_dir / b['path']} (validator: {'passed' if b['valid'] else 'FAILED'})")
    return out


def match_lines(con, ref: Reference, offer: str, line_no: int) -> list[str]:
    """Offer ratios next to our fund's ratios at the reference date, with the distance per ratio."""
    o = store.resolve_offer(con, offer)
    if not o:
        return [f"No offer called {offer}."]
    oid = o["offer_id"]
    line = store.one(con, "SELECT * FROM silver_offer_lines WHERE offer_id = ? AND line_no = ?", [oid, line_no])
    g = store.one(con, "SELECT * FROM gold_line_matches WHERE offer_id = ? AND line_no = ?", [oid, line_no])
    if not line or not g:
        return [f"Project {o['code_name']} has no Fund {line_no}."]
    from .models import ParsedLine

    pl = ParsedLine(**{c: line[c] for c in ParsedLine.model_fields})
    lr = match.line_ratios(pl)
    cands = json.loads(g["candidates_json"])
    tier = brief_mod.TIER_WORDS[g["tier"]] + ("" if g["resolved"] else " (open)")
    conf = "" if g["confidence"] is None else f", confidence {g['confidence']:.2f}"
    head = [f"Project {o['code_name']}, {g['label']}: {line['description']}, {line['vintage']}",
            f"Tier: {tier}{conf}. Method: {g['method']}."]
    known_at = o["email_date"]
    # The fund to show: the match, else the best owned candidate with a report at the reference date.
    order = ([g["fund_id"]] if g["fund_id"] else []) + [c["fund_id"] for c in cands]
    shown = []
    for fid in dict.fromkeys(order):
        f = ref.funds[fid]
        row = ref.series_at(fid, o["reference_date"], known_at) if f["owned"] else None
        shown.append((f, row))
    owned = [(f, r) for f, r in shown if r is not None and r.called > 0]
    if not lr:
        names = "; ".join(f"{f['fund_id']} {f['name']} ({'we own it' if f['owned'] else 'we track it'})"
                          for f, _ in shown) or "none"
        return head + ["The broker gives no ratios for this line, so there is nothing to compare.",
                       f"Candidates: {names}.", f"To settle it: radar resolve {o['code_name'].lower()} {line_no} <fund_id>"]
    if not owned:
        return head + ["No fund we own fits, so we have no report to compare with."]
    f, row = owned[0]
    fr = match.fund_ratios(row.called, row.distributed, row.nav, f["size_m"])
    score, dist = match.ratio_score(lr, fr)
    outside = match.within_tolerance(lr, fr)
    out = head + [f"Compared with our report on {f['fund_id']} {f['name']} for {row.as_of_date} "
                  f"(row {row.row_id}, as known at {known_at:%Y-%m-%d %H:%M}).", ""]
    w = (20, 10, 10, 10, 11, 9)
    out.append(f"{'ratio':<{w[0]}}{'offer':>{w[1]}}{'ours':>{w[2]}}{'gap':>{w[3]}}{'tolerance':>{w[4]}}{'distance':>{w[5]}}")
    for k, v in lr.items():
        tol = _fmt_gap(k, match.TOL[k]).lstrip("+")
        flag = "  outside" if k in outside else ""
        out.append(f"{RATIO_NAMES[k]:<{w[0]}}{_fmt_ratio(k, v):>{w[1]}}{_fmt_ratio(k, fr[k]):>{w[2]}}"
                   f"{_fmt_gap(k, v - fr[k]):>{w[3]}}{tol:>{w[4]}}{dist[k]:>{w[5]}.3f}{flag}")
    out += ["", f"Distance = gap / (5 x tolerance), capped at 1. Score = 1 - mean distance = {score:.2f}"
                + (" (stored score includes the name-fragment adjustment)." if line["name_fragment"] else "."),
            f"Same fund needs {match.SAME_FUND_MIN:.2f} or more, every ratio inside tolerance plus the broker's "
            f"rounding, and a gap of {match.CLEAR_GAP:.2f} over the next fund we own."]
    others = [c for c in cands if c["fund_id"] != f["fund_id"] and c.get("score") is not None]
    if others:
        out.append(f"Runner-up: {others[0]['fund_id']} {others[0]['fund_name']}, score {others[0]['score']:.2f}.")
    return out


# ---------------------------------------------------------------- memo

def memo_segments(con, offer_id: str) -> list:
    """The memo as text pieces and sourced spans (Markup)."""
    o = store.one(con, "SELECT * FROM silver_offers WHERE offer_id = ?", [offer_id])
    v = store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [offer_id])
    mets = {m["metric"]: m for m in store.rows(con, "SELECT * FROM gold_offer_metrics WHERE offer_id = ?", [offer_id])}

    def M(metric: str) -> Markup:
        m = mets[metric]
        return brief_mod.span(brief_mod.fmt_metric(metric, m["value"]), "gold_offer_metrics", m["row_id"], metric,
                              m["version"])

    def S(table: str, row: str, col: str, disp: str, version: str) -> Markup:
        return brief_mod.span(disp, table, row, col, version)

    rbv = f"rulebook v{v['rulebook_version']}"
    broker = re.sub(r"\s*<.*>$", "", o["sender"] or "").strip() or "the broker"
    seller = o["seller_type"] or "a seller"
    n = mets["count_lines"]["value"] or 0
    segs: list = [f"Project {o['code_name']} ({broker}): {seller} is selling ", M("count_lines"),
                  f" fund interest{'s' if n != 1 else ''}, total value ", M("total_value_usd"), " at ",
                  S("silver_offers", offer_id, "reference_date", o["reference_date"].isoformat(), o["parser_version"])]
    if o["bid_deadline"]:
        segs += [", indicative bids due ",
                 S("silver_offers", offer_id, "bid_deadline", o["bid_deadline"].isoformat(), o["parser_version"])]
    segs += [". Our rulebook (version ", S("gold_verdicts", offer_id, "rulebook_version", v["rulebook_version"], rbv),
             f") says {v['verdict']}, {v['band']} confidence: score ",
             S("gold_verdicts", offer_id, "score", f"{v['score']:.2f}", rbv) if v["score"] is not None else "n/a",
             " against a threshold of ", S("gold_verdicts", offer_id, "threshold", f"{v['threshold']:g}", rbv),
             ", data matched ", S("gold_verdicts", offer_id, "data_matched", f"{v['data_matched']:.0%}", rbv), ". "]
    if mets["count_same_fund"]["value"]:
        segs += ["We own ", M("count_same_fund"), " of the funds (", M("share_value_same_fund"), " of value)"]
        if mets["price_range_low"]["value"] is not None:
            segs += ["; our price range for them is ", M("price_range_low"), " to ", M("price_range_high"),
                     " of value"]
        if o["asking_price_text"]:
            segs += [", against guidance of ",
                     S("silver_offers", offer_id, "asking_price_text", o["asking_price_text"], o["parser_version"])]
        segs.append(". ")
    else:
        segs.append("We own none of the funds, so we have no price of our own. ")
    segs += ["Managers we know hold ", M("share_value_known_manager"), " of value; venture is ",
             M("share_value_venture"), ". "]
    failed = store.rows(con, "SELECT * FROM gold_rule_results WHERE offer_id = ? AND status != 'met' ORDER BY rank",
                        [offer_id])
    for r in failed:
        thr = (f"{r['threshold']:.0%}" if r["metric"] in brief_mod.PCT else
               brief_mod.fmt_metric(r["metric"], r["threshold"]) if r["metric"] == "guidance_minus_range_top"
               else f"{r['threshold']:g}")
        if r["status"] == "not checked":
            segs += [f"Not checked: {r['description'].lower()} (no number). "]
        else:
            segs += [f"Not met: {r['description'].lower()} (", M(r["metric"]), f" against {r['op']} ",
                     S("gold_rule_results", r["row_id"], "threshold", thr, rbv), "). "]
    open_lines = store.rows(con, "SELECT label FROM gold_line_matches WHERE offer_id = ? AND NOT resolved ORDER BY line_no",
                            [offer_id])
    if open_lines:
        segs += [M("count_unresolved"), f" line{'s' if len(open_lines) > 1 else ''} still unmatched (",
                 M("share_value_unresolved"), " of value): ", ", ".join(l["label"] for l in open_lines), ". "]
    last: dict = {}
    for r in store.rows(con, "SELECT * FROM line_resolutions WHERE offer_id = ? ORDER BY created_at", [offer_id]):
        last[r["line_no"]] = r
    for r in sorted(last.values(), key=lambda r: r["line_no"]):
        segs += [f"{r['label']} was matched by hand to ",
                 S("line_resolutions", r["resolution_id"], "fund_name", r["fund_name"], "resolution"), " (",
                 S("line_resolutions", r["resolution_id"], "user_name", r["user_name"], "resolution"), "). "]
    dec = store.team_decision(con, offer_id)
    final = v["verdict"]
    if dec and dec["kind"] == "override":
        final = dec["new_verdict"]
        segs += [f"I override this to {final}: ",
                 S("overrides", dec["override_id"], "reason", dec["reason"], "override"), ". "]
    elif dec:
        segs += ["I keep this verdict. "]
    else:
        segs += ["No decision recorded yet. "]
    segs.append("Proposal: sign the NDA and ask for the data room." if final == "LOOK CLOSER"
                else "Proposal: decline the NDA and tell the broker.")
    return segs


def memo(con, offer: str) -> tuple[str, str, list[str], int]:
    """(plain text, html, validator problems, count of sourced numbers)."""
    o = store.resolve_offer(con, offer)
    if not o:
        raise KeyError(f"No offer called {offer}.")
    segs = memo_segments(con, o["offer_id"])
    html = "<p>" + "".join(str(escape(s)) if isinstance(s, str) and not isinstance(s, Markup) else str(s)
                           for s in segs) + "</p>"
    problems = brief_mod.validate(html, lambda t, r, m: store.lookup(con, t, r, m))
    text = "".join(re.sub(r"<[^>]+>", "", str(s)) if isinstance(s, Markup) else s for s in segs)
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&#34;", '"')
    return text, html, problems, sum(1 for s in segs if isinstance(s, Markup))
