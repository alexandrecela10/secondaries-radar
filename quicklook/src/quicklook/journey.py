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
    desc = line["description"] or ""
    vint = "" if not line["vintage"] or str(line["vintage"]) in desc else f", {line['vintage']}"
    head = [f"Project {o['code_name']}, {g['label']}: {desc}{vint}",
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

def day(d) -> str:
    """30 June, the way people write dates in a message."""
    return f"{d.day} {d:%B}"


def memo_parts(d: dict, N) -> list:
    """The message to the head of team, as text pieces. Decision and ask first, then a few plain facts.

    `d` holds plain values (see memo_facts and demo.ql.memo). `N(key, display)` returns the piece to show
    for a number or date: a sourced span in the command line version, plain text in the demo."""
    final = d["verdict"]
    dec = d.get("decision")
    if dec and dec["kind"] == "override":
        final = dec["verdict"]
    code = d["code"]
    if final == "LOOK CLOSER":
        head = f"Project {code}: worth a closer look. I suggest we sign the NDA and ask for the data room."
    elif d.get("veto_rule") == "price_veto" and final == d["verdict"]:
        head = f"Project {code}: too expensive by our own data. I suggest we decline the NDA."
    else:
        head = f"Project {code}: not for us. I suggest we decline the NDA."
    seller = (d.get("seller") or "the seller").strip()
    if not re.match(r"(?i)(a|an|the)\s", seller):
        seller = ("an " if seller[0].lower() in "aeiou" else "a ") + seller
    seller = seller[0].upper() + seller[1:]
    n = d["n_lines"]
    out: list = [head, "\n\n", f"{seller} is selling its stakes in ", N("count_lines", f"{n:.0f}"),
                 f" fund{'s' if n != 1 else ''}, worth ", N("total_value_usd", f"${d['total']:,.1f}M"), " at ",
                 N("reference_date", day(d["ref_date"])), f", through {d['broker']}."]
    if d.get("deadline"):
        out += [" First bids are due ", N("bid_deadline", day(d["deadline"])), "."]
    out.append("\n")
    bullets: list[list] = []
    if d["n_owned"]:
        k = d["n_owned"]
        bullets.append(["We already own ", N("count_same_fund", f"{k:.0f}"),
                        f" of these funds (", N("share_value_same_fund", f"{d['share_owned']:.0%}"),
                        " of the value), so we know them well."])
        ups = [a["company"] for a in d.get("adjustments", []) if a["kind"] == "exit"]
        downs = [a["company"] for a in d.get("adjustments", []) if a["kind"] != "exit"]
        if ups or downs:
            what = []
            if ups:
                what.append(f"{_and(ups)} {'was' if len(ups) == 1 else 'were'} sold for more than "
                            f"{'its' if len(ups) == 1 else 'their'} value then")
            if downs:
                what.append(f"{_and(downs)} {'was' if len(downs) == 1 else 'were'} written down")
            more = d["worth_change"] >= 0
            bullets.append(["Since ", N("reference_date", day(d["ref_date"])), ", our own reports show ",
                            "; ".join(what), ". So these funds are worth about ",
                            N("worth_change_owned", f"{abs(d['worth_change']) * 100:.1f}%"),
                            f" {'more' if more else 'less'} than the seller's figure."])
        if d.get("ask_text") and d.get("top") is not None:
            ok = d["price_view"] == "cheap"
            bullets.append(["The seller wants ", N("asking_price_text", d["ask_text"]),
                            " (% of value). By our own data we would pay up to ",
                            N("price_range_high", f"{d['top']:.0%}"),
                            ", so the price looks right." if ok else ", so the price looks too high."])
    else:
        bullets.append(["We own none of these funds, so we have no price view of our own."])
    if d.get("share_known") is not None:
        bullets.append(["We know the managers behind ", N("share_value_known_manager", f"{d['share_known']:.0%}"),
                        " of the value."])
    for desc in d.get("failed_rules", []):
        bullets.append([f"It fails one of our rules: {desc[0].lower() + desc[1:]}."])
    for p in d.get("picks", []):
        bullets.append([f"{p['label']} was identified by hand as {p['fund']} ({p['user']})."])
    if d.get("unresolved"):
        u = len(d["unresolved"])
        bullets.append(["We can't yet identify ", N("count_unresolved", f"{u}"),
                        f" fund{'s' if u > 1 else ''} (", N("share_value_unresolved", f"{d['share_unresolved']:.0%}"),
                        " of the value)."])
    for b in bullets:
        out += ["\n- "] + b
    why = []
    if d.get("close"):
        why.append("it " + ("passes" if d["verdict"] == "LOOK CLOSER" else "misses") + " our rules, but not by much")
    if d.get("unresolved"):
        why.append("some of the offer is not identified yet")
    why = why if d["band"] != "high" else []
    out += ["\n\n", f"Confidence is {d['band']}" + (": " + " and ".join(why) if why else "") + "."]
    if dec and dec["kind"] == "override":
        out += [f" The system said {d['verdict']}; I disagree: ", N("override_reason", dec["reason"]), "."]
    out.append("\n\nInternal: uses information from our own fund reports.")
    return out


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def memo_facts(con, offer_id: str) -> tuple[dict, dict]:
    """Plain values for memo_parts from gold rows, and where each number comes from."""
    o = store.one(con, "SELECT * FROM silver_offers WHERE offer_id = ?", [offer_id])
    v = store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [offer_id])
    mets = {m["metric"]: m for m in store.rows(con, "SELECT * FROM gold_offer_metrics WHERE offer_id = ?", [offer_id])}
    val = lambda k: mets[k]["value"]  # noqa: E731
    failed = store.rows(con, "SELECT * FROM gold_rule_results WHERE offer_id = ? AND status = 'not met' ORDER BY rank",
                        [offer_id])
    unresolved = store.rows(con, "SELECT label FROM gold_line_matches WHERE offer_id = ? AND NOT resolved ORDER BY line_no",
                            [offer_id])
    adj = store.rows(con, "SELECT * FROM gold_price_adjustments WHERE offer_id = ? ORDER BY line_no, event_date",
                     [offer_id])
    last: dict = {}
    for r in store.rows(con, "SELECT * FROM line_resolutions WHERE offer_id = ? ORDER BY created_at", [offer_id]):
        last[r["line_no"]] = r
    dec = store.team_decision(con, offer_id)
    decision = None
    if dec and dec["kind"] == "override":
        decision = dict(kind="override", verdict=dec["new_verdict"], reason=dec["reason"])
    elif dec:
        decision = dict(kind="keep")
    rb_close = v["margin"] is not None and v["margin"] < 0.2
    d = dict(code=o["code_name"], seller=o["seller_type"], broker=re.sub(r"\s*<.*>$", "", o["sender"] or "").strip()
             or "the broker", n_lines=val("count_lines") or 0, total=val("total_value_usd"),
             ref_date=o["reference_date"], deadline=o["bid_deadline"], verdict=v["verdict"], veto_rule=v["veto_rule"],
             price_view=v["price_view"], band=v["band"], close=rb_close, n_owned=val("count_same_fund") or 0,
             share_owned=val("share_value_same_fund"), worth_change=val("worth_change_owned"),
             adjustments=[dict(company=a["company"], kind=a["kind"]) for a in adj], ask_text=o["asking_price_text"],
             top=val("price_range_high"), share_known=val("share_value_known_manager"),
             failed_rules=[r["description"] for r in failed], unresolved=[u["label"] for u in unresolved],
             share_unresolved=val("share_value_unresolved"),
             picks=[dict(label=r["label"], fund=r["fund_name"], user=r["user_name"]) for r in sorted(last.values(), key=lambda r: r["line_no"])],
             decision=decision)
    pv = o["parser_version"]
    src = {k: ("gold_offer_metrics", m["row_id"], k, m["version"]) for k, m in mets.items()}
    src.update(reference_date=("silver_offers", offer_id, "reference_date", pv),
               bid_deadline=("silver_offers", offer_id, "bid_deadline", pv),
               asking_price_text=("silver_offers", offer_id, "asking_price_text", pv))
    if decision and decision["kind"] == "override":
        src["override_reason"] = ("overrides", dec["override_id"], "reason", "override")
    return d, src


def memo(con, offer: str) -> tuple[str, str, list[str], int]:
    """(plain text, html, validator problems, count of sourced numbers)."""
    o = store.resolve_offer(con, offer)
    if not o:
        raise KeyError(f"No offer called {offer}.")
    d, src = memo_facts(con, o["offer_id"])
    segs = memo_parts(d, lambda key, disp: brief_mod.span(disp, *src[key], absolute=key == "worth_change_owned"))
    html = "<p>" + "".join(str(escape(s)) if isinstance(s, str) and not isinstance(s, Markup) else str(s)
                           for s in segs).replace("\n", "<br>") + "</p>"
    problems = brief_mod.validate(html, lambda t, r, m: store.lookup(con, t, r, m))
    text = "".join(re.sub(r"<[^>]+>", "", str(s)) if isinstance(s, Markup) else s for s in segs)
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&#34;", '"')
    return text, html, problems, sum(1 for s in segs if isinstance(s, Markup))
