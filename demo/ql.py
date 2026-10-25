"""Quick Look data functions for the demo. No Streamlit here, so tests can call every one.

Everything is the production code from `quicklook/`: the offline parser reads the email,
`pipeline.compute` matches lines, computes metrics and applies the rulebook. Nothing is stored:
the demo keeps the analyst's picks and decisions in the browser session only.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from quicklook import journey, match, metrics, pipeline, rulebook
from quicklook.emailio import EmailContent, read_email
from quicklook.models import ParsedLine, Tier
from quicklook.parse import OfflineParser, ParseError
from quicklook.reference import Reference

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "demo" / "data" / "quicklook"
CONFIG = ROOT / "quicklook" / "config"
RULEBOOKS = {"v1": CONFIG / "history" / "rulebook_v1.yaml", "v2": CONFIG / "history" / "rulebook_v2.yaml",
             "v3": CONFIG / "rulebook.yaml"}
LIVE = "v3"
JOURNEY_OFFERS = ["Cedar", "Hornbeam", "Alder", "Birch", "Hazel", "Juniper", "Maple", "Rowan", "Spruce", "Thistle",
                  "Willow"]
RATIO_NAMES = {"pct_called": "% called", "dpi": "DPI", "tvpi": "TVPI", "value_per_commitment": "value / commitment"}
TIER_WORDS = {"same_fund": "a fund we own", "same_manager": "a manager we know, other fund", "unknown": "unknown"}
PASTED_AT = dt.datetime(2026, 10, 23, 9, 0, tzinfo=dt.timezone.utc)

EXAMPLE_PASTE = """Project Fern: LP portfolio sale
Reference date: 30 June 2026.
Total NAV: approx. USD 60M.
Pricing guidance: mid 80s % of NAV.
Indicative bids due: 20 November 2026.
The seller is a family office.
Fund 1: European mid-market buyout, 2016, commitment EUR 20M, 92% called, DPI 0.84x, TVPI 1.71x, NAV EUR 16.1M
Fund 2: US growth, 2019, NAV ~$30M
Fund 3: North American venture capital, 2024, commitment $15M, 58% called, DPI 0.0x, TVPI 1.14x, NAV $10.0M
"""


# ------------------------------------------------------------------ loading

@lru_cache(maxsize=1)
def reference() -> Reference:
    return Reference(SNAP / "reference")


@lru_cache(maxsize=1)
def plan() -> dict:
    return yaml.safe_load((CONFIG / "plan.yaml").read_text())


@lru_cache(maxsize=4)
def load_rulebook(name: str) -> rulebook.Rulebook:
    return rulebook.load(RULEBOOKS[name])


@lru_cache(maxsize=1)
def labels() -> dict[str, dict]:
    return {o["code"]: o for o in json.loads((SNAP / "labels.json").read_text())["offers"]}


def offer_codes() -> list[str]:
    """The ten journey offers first (Cedar leads), then the twenty archetype offers."""
    rest = sorted(c for c in labels() if c not in JOURNEY_OFFERS)
    return [c for c in JOURNEY_OFFERS if c in labels()] + rest


@dataclass
class Offer:
    code: str
    offer_id: str
    content: EmailContent
    parsed: object
    lines: list[ParsedLine]
    rejects: list[dict]
    parser: str = "offline-regex / offline-v1"
    label: dict | None = None


def _build(content: EmailContent, parser=None, label: dict | None = None) -> Offer:
    parser = parser or OfflineParser()
    parsed = parser.parse(content)
    oid = pipeline.offer_id_for(parsed.code_name, content.message_id)
    lines, rejects = pipeline._validate_lines(oid, parsed)
    return Offer(parsed.code_name, oid, content, parsed, lines, rejects, f"{parser.name} / {parser.version}", label)


@lru_cache(maxsize=64)
def load_offer(code: str) -> Offer:
    lab = labels()[code]
    content = read_email((SNAP / "offers" / Path(lab["file"]).name).read_bytes())
    return _build(content, label=lab)


def content_from_text(text: str, sent: dt.datetime = PASTED_AT) -> EmailContent:
    """Pasted text becomes an email: first line is the subject, the rest is the body."""
    text = (text or "").strip()
    first, _, rest = text.partition("\n")
    return EmailContent(message_id=f"pasted-{abs(hash(text)) % 10**8}", subject=first.strip(),
                        sender="You <you@example.com>", date=sent, body=text)


def parse_pasted(text: str, parser=None) -> tuple[Offer | None, str | None]:
    """(offer, None) or (None, plain reason). Offline unless a model parser is passed."""
    if len(text or "") > 20_000:
        return None, "That text is over 20,000 characters. Paste one offer."
    try:
        o = _build(content_from_text(text), parser)
    except ParseError as e:
        return None, f"The offline parser could not read it: {e}."
    except Exception as e:  # pydantic or model errors, shown as one line
        return None, f"Could not read it: {str(e).splitlines()[0]}"
    if not o.lines:
        return None, "No fund line had a value, so there is nothing to weigh."
    return o, None


def email_text(o: Offer) -> str:
    c = o.content
    head = f"From: {c.sender}\nDate: {c.date:%a %d %b %Y %H:%M} UTC\nSubject: {c.subject}\n"
    tables = "".join(f"\n[Attachment {t.filename}]\n{t.as_csv()}" for t in c.tables)
    return head + "\n" + c.body.strip() + ("\n" + tables if tables else "")


# ------------------------------------------------------------------ rulebook

def custom_rulebook(base: rulebook.Rulebook, threshold: float, rules: dict[str, dict],
                    veto_gap: float | None = None) -> rulebook.Rulebook:
    """A copy of `base` with your threshold, your value, weight and veto per rule, and your price veto gap."""
    new_rules = []
    for r in base.quick_look:
        e = rules.get(r.id, {})
        new_rules.append(r.model_copy(update={k: e[k] for k in ("value", "weight", "veto") if k in e}))
    price = base.price
    if base.price is not None and veto_gap is not None:
        price = base.price.model_copy(update={"veto_gap": veto_gap})
    changed = (threshold != base.threshold or any(a != b for a, b in zip(new_rules, base.quick_look))
               or price != base.price)
    return base.model_copy(update={"threshold": threshold, "quick_look": new_rules, "price": price,
                                   "version": f"{base.version}-yours" if changed else base.version,
                                   "sha": "edited-in-demo" if changed else base.sha})


def rulebook_sentences(rb: rulebook.Rulebook) -> list[str]:
    return rulebook.as_sentences(rb)


# ------------------------------------------------------------------ the run

@dataclass
class Run:
    offer: Offer
    match_rows: list[dict]
    metrics: dict
    verdict: object
    exposure: list[dict]
    picks: dict = field(default_factory=dict)

    @property
    def values(self) -> dict[str, float | None]:
        return {k: v.value for k, v in self.metrics.items()}


def run(o: Offer, rb: rulebook.Rulebook, picks: dict[int, str] | None = None, user: str = "layla") -> Run:
    """Match, metrics and verdict, exactly as the pipeline computes them. `picks` maps line_no to fund_id."""
    res = {n: {"fund_id": f, "user_name": user} for n, f in (picks or {}).items()}
    rows, mvals, verdict, exposure = pipeline.compute(o.parsed, o.lines, o.offer_id, o.content.date, reference(),
                                                      rb, plan(), res)
    return Run(o, rows, mvals, verdict, exposure, dict(picks or {}))


def source(r: Run, metric: str) -> str:
    m = r.metrics[metric]
    return f"gold_offer_metrics · row {r.offer.offer_id}:{metric} · {metric} {m.version}"


def fmt_value(metric: str, v: float | None) -> str:
    if v is None:
        return "no number"
    if metric == "guidance_minus_range_top":
        return f"{v:+.0%}"
    if metric in rulebook.PCT_METRICS:
        return f"{v:.0%}"
    if metric.startswith("count_"):
        return f"{v:.0f}"
    return f"{v:.3f}" if metric == "fit_score" else f"{v:g}"


def reasons(r: Run) -> list[dict]:
    out = []
    for x in r.verdict.reasons:
        out.append({"status": x.status, "rule": x.description, "weight": x.weight, "veto": x.veto,
                    "number": fmt_value(x.metric, x.value), "test": f"{rulebook.OP_WORDS[x.op]} "
                    f"{fmt_value(x.metric, x.threshold) if x.metric != 'guidance_minus_range_top' else '0 points'}",
                    "metric": x.metric, "source": source(r, x.metric), "note": r.metrics[x.metric].note})
    return out


def lines_table(r: Run) -> list[dict]:
    out = []
    for m, line in zip(r.match_rows, r.offer.lines):
        cands = json.loads(m["candidates_json"])
        out.append({"line": m["label"], "description": line.description, "vintage": line.vintage,
                    "value (USD M)": round(m["value_usd"], 1), "tier": TIER_WORDS[m["tier"]],
                    "settled": "yes" if m["resolved"] else "open",
                    "confidence": None if m["confidence"] is None else round(m["confidence"], 2),
                    "fund": m["fund_name"] or "", "method": m["method"],
                    "candidates": ", ".join(c["fund_name"] for c in cands[:3])})
    return out


def open_lines(r: Run) -> list[dict]:
    return [m for m in r.match_rows if not m["resolved"]]


def candidates_for(r: Run, line_no: int) -> list[dict]:
    """Funds that fit the line's description: the only ones an analyst may pick (code refuses others)."""
    line = next(l for l in r.offer.lines if l.line_no == line_no)
    ref = reference()
    fits = ref.candidates(line.vintage, line.strategy, line.region)
    return [{"fund_id": f["fund_id"], "name": f["name"], "manager": ref.manager_name(f["manager_id"]),
             "owned": bool(f["owned"])} for f in fits]


def match_view(r: Run, line_no: int) -> dict:
    """The line's ratios next to our own report at the reference date, distance per ratio."""
    ref = reference()
    line = next(l for l in r.offer.lines if l.line_no == line_no)
    g = next(m for m in r.match_rows if m["line_no"] == line_no)
    lr = match.line_ratios(line)
    cands = json.loads(g["candidates_json"])
    order = ([g["fund_id"]] if g["fund_id"] else []) + [c["fund_id"] for c in cands]
    known_at = r.offer.content.date
    ref_date = r.offer.parsed.reference_date
    owned = []
    for fid in dict.fromkeys(order):
        f = ref.funds[fid]
        row = ref.series_at(fid, ref_date, known_at) if f["owned"] else None
        if row is not None and row.called > 0:
            owned.append((f, row))
    out = {"label": g["label"], "description": line.description, "tier": TIER_WORDS[g["tier"]],
           "settled": bool(g["resolved"]), "confidence": g["confidence"], "method": g["method"],
           "flags": json.loads(g["flags_json"]), "rows": [], "fund": None, "report_row": None, "score": None,
           "runner_up": None, "candidates": cands}
    if not lr or not owned:
        return out
    f, row = owned[0]
    fr = match.fund_ratios(row.called, row.distributed, row.nav, f["size_m"])
    score, dist = match.ratio_score(lr, fr)
    outside = match.within_tolerance(lr, fr)
    for k, v in lr.items():
        pt = k == "pct_called"
        out["rows"].append({
            "ratio": RATIO_NAMES[k],
            "offer": f"{v * 100:.1f}%" if pt else (f"{v:.3f}" if k == "value_per_commitment" else f"{v:.3f}x"),
            "ours": f"{fr[k] * 100:.1f}%" if pt else (f"{fr[k]:.3f}" if k == "value_per_commitment" else f"{fr[k]:.3f}x"),
            "gap": f"{(v - fr[k]) * 100:+.1f} pt" if pt else f"{v - fr[k]:+.3f}",
            "tolerance": f"{match.TOL[k] * 100:.1f} pt" if pt else f"{match.TOL[k]:.3f}",
            "distance": round(dist[k], 3), "inside": "yes" if k not in outside else "no"})
    out.update(fund=f"{f['fund_id']} {f['name']}", report_row=f"{row.row_id} (as of {row.as_of_date}, "
               f"known at {known_at:%Y-%m-%d %H:%M})", score=score)
    others = [c for c in cands if c["fund_id"] != f["fund_id"] and c.get("score") is not None]
    if others:
        out["runner_up"] = f"{others[0]['fund_id']} {others[0]['fund_name']}, score {others[0]['score']:.2f}"
    return out


def price_check(r: Run) -> dict:
    """The price check for funds we own: what their managers told us after the broker's date."""
    m, v = r.values, r.verdict
    adj = []
    for row in r.match_rows:
        for a in json.loads(row["adjustments_json"]):
            adj.append({"line": a["label"], "fund": a["fund_name"], "what happened": a["note"],
                        "we were told": a["reported_at"],
                        "fund value": f"{a['fund_change_pct']:+.1%}",
                        "this stake (USD M)": f"{a['line_change_usd']:+.1f}", "source": f"ref_fund_events {a['event_id']}"})
    owned_value = sum(x["value_usd"] for x in r.match_rows if x["tier"] == "same_fund" and x["worth_usd"] is not None)
    return {"view": v.price_view, "vetoed": v.veto_rule == rulebook.PRICE_VETO, "adjustments": adj,
            "n_owned": int(m["count_same_fund"] or 0), "share_owned": m["share_value_same_fund"],
            "broker_value": owned_value, "worth": m["worth_today_owned_usd"], "change": m["worth_change_owned"],
            "low": m["price_range_low"], "high": m["price_range_high"], "ask_text": r.offer.parsed.asking_price_text,
            "ask_mid": m["asking_price_mid"], "gap": m["guidance_minus_range_top"]}


def memo(r: Run, decision: dict | None) -> str:
    """The message to the head of team. Same builder as `radar memo`; here numbers are plain text."""
    o, v, m = r.offer.parsed, r.verdict, r.values
    ref = reference()
    adj = [a for row in r.match_rows for a in json.loads(row["adjustments_json"])]
    d = dict(code=o.code_name, seller=o.seller_type,
             broker=(r.offer.content.sender or "").split("<")[0].strip() or "the broker",
             n_lines=m["count_lines"] or 0, total=m["total_value_usd"], ref_date=o.reference_date,
             deadline=o.bid_deadline, verdict=v.verdict, veto_rule=v.veto_rule, price_view=v.price_view, band=v.band,
             close=v.margin is not None and v.margin < 0.2, n_owned=m["count_same_fund"] or 0,
             share_owned=m["share_value_same_fund"], worth_change=m["worth_change_owned"],
             adjustments=[dict(company=a["company"], kind=a["kind"]) for a in adj], ask_text=o.asking_price_text,
             top=m["price_range_high"], share_known=m["share_value_known_manager"],
             failed_rules=[x.description for x in v.reasons if x.status == "not met"],
             unresolved=[l["label"] for l in open_lines(r)], share_unresolved=m["share_value_unresolved"],
             picks=[dict(label=f"Fund {n}", fund=ref.funds[f]["name"], user="layla") for n, f in sorted(r.picks.items())],
             decision=decision)
    return "".join(journey.memo_parts(d, lambda key, disp: disp))


# ------------------------------------------------------------------ tune and holdout

@lru_cache(maxsize=1)
def labelled_values() -> dict[str, dict]:
    """Metric values for every labelled offer, computed once. A rulebook change re-scores them instantly."""
    out = {}
    for code in labels():
        r = run(load_offer(code), load_rulebook(LIVE))
        out[code] = r.values
    return out


def agreement(rb: rulebook.Rulebook) -> dict:
    """Verdicts against the builder's labels, per split. Same rule as `radar rules test`."""
    vals, labs = labelled_values(), labels()
    out = {}
    for split in ("tune", "holdout"):
        rows = []
        for code, lab in labs.items():
            if not lab["is_test_offer"] or lab.get("split") != split:
                continue
            v = rulebook.evaluate(rb, vals[code])
            agrees = (v.verdict == "LOOK CLOSER") == (lab["pm_label"] == "LOOK")
            rows.append({"offer": code, "archetype": lab.get("archetype", ""), "label": lab["pm_label"],
                         "verdict": v.verdict, "score": None if v.score is None else round(v.score, 2),
                         "agrees": "yes" if agrees else "no", "why the label": lab.get("rationale", "")})
        kept = sum(r["agrees"] == "yes" for r in rows)
        good_passed = sum(r["label"] == "LOOK" and r["verdict"] == "PASS" for r in rows)
        out[split] = {"kept": kept, "total": len(rows), "good_passed": good_passed, "rows": rows}
    return out
