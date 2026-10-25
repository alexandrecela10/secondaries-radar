"""One-page HTML brief, rendered from gold rows only, plus the number validator (C-2).

Every number on the page is a <span class="n"> that names its source:
table, row id, metric (column) and version. The validator looks each one up in
the database and checks the shown number against the stored value. Any other
digit on the page fails the brief, except years, fund ordinals ("Fund 9") and
numbers repeated from a sourced span. Elements with class "lineage" are the
pointers themselves and are not checked.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from html.parser import HTMLParser
from typing import Callable

from jinja2 import Environment
from markupsafe import Markup, escape

from . import store
from .metrics import REGISTRY

PCT = {"share_value_same_fund", "share_value_same_manager", "share_value_unknown", "share_value_known_manager",
       "share_value_unresolved", "data_matched", "share_value_venture", "top5_concentration",
       "price_range_low", "price_range_high", "asking_price_mid"}
TIER_WORDS = {"same_fund": "same fund", "same_manager": "same manager, other fund", "unknown": "unknown"}


def fmt_metric(metric: str, v: float | None) -> str:
    if v is None:
        return "no value"
    if metric in PCT:
        return f"{v:.0%}"
    if metric == "guidance_minus_range_top":
        return "0%" if round(v * 100) == 0 else f"{v * 100:+.0f}%"
    if metric.startswith("count_"):
        return f"{v:.0f}"
    if metric == "total_value_usd":
        return f"${v:,.1f}M"
    return f"{v:.2f}"


def fmt_money(v: float | None, cur: str | None) -> str:
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur or "USD", "")
    return "" if v is None else f"{sym}{v:,.1f}M"


def span(display: str, table: str, row: str, metric: str, version: str, absolute: bool = False) -> Markup:
    """A sourced number. absolute=True: the text shows the size and the words around it give the
    direction ("5.5% more"), so the validator compares sizes."""
    if display in ("", None):
        return Markup('<span class="na">n/a</span>')
    ab = ' data-abs="1"' if absolute else ""
    return Markup(f'<span class="n" data-table="{escape(table)}" data-row="{escape(row)}" '
                  f'data-metric="{escape(metric)}" data-version="{escape(version)}"{ab}>{escape(display)}</span>')


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quick Look: Project {{ o.code_name }}</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mute:#6b6a65;--line:#e3e0d8;--good:#1f6f43;--bad:#9b2c2c;--warn:#8a5a00;--card:#fff}
@media (prefers-color-scheme: dark){:root{--bg:#161614;--fg:#ecebe6;--mute:#a19f98;--line:#33322e;--good:#6fcf97;--bad:#f28b82;--warn:#f2c46d;--card:#1f1f1c}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:24px 16px}
main{max-width:980px;margin:0 auto}
header{display:flex;flex-wrap:wrap;gap:4px 16px;color:var(--mute);font-size:13px;text-transform:uppercase;letter-spacing:.04em}
header b{color:var(--fg)}
h1{font-size:22px;line-height:1.3;margin:12px 0 16px}
h2{font-size:15px;margin:24px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.banner{background:#fff4d6;color:#5a4300;padding:8px 12px;border-radius:6px;margin:12px 0}
.override{border:2px solid var(--warn);padding:8px 12px;border-radius:6px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mute);font-weight:600}
.met{color:var(--good);font-weight:600}.notmet{color:var(--bad);font-weight:600}.nc{color:var(--mute);font-weight:600}
.veto{font-size:11px;border:1px solid var(--bad);color:var(--bad);border-radius:3px;padding:0 4px;margin-left:6px}
details summary{cursor:pointer}
.lineage{color:var(--mute);font-size:12px;font-family:ui-monospace,Menlo,monospace}
.scroll{overflow-x:auto}
.mute{color:var(--mute)}
.n{font-variant-numeric:tabular-nums}
</style></head><body><main>
<header><b>Quick Look</b><span>Project {{ o.code_name }}</span><span>received {{ received }}</span>
<span>values at {{ refdate }}</span><span>{{ seller }}</span></header>
{% if offline %}<div class="banner">Offline parser in use. Fields were read by fixed rules, not by the model.</div>{% endif %}
{% if override %}<div class="override"><b>Team override: {{ override.new_verdict }}</b> (rulebook said {{ override.original_verdict }}).
Reason: {{ override.reason }}. By {{ override.user_name }}, {{ override.at }}.</div>{% endif %}
{% if kept %}<div class="override"><b>Team kept the verdict: {{ kept.verdict }}</b>. By {{ kept.user_name }}, {{ kept.at }}.</div>{% endif %}
{% if picks %}<p class="mute">Matched by hand: {% for p in picks %}{{ p.label }} is {{ p.fund }} (by {{ p.user }}, {{ p.at }}){% if not loop.last %}; {% endif %}{% endfor %}.</p>{% endif %}
<h1>{{ headline }}</h1>
<p class="mute">Fit score {{ score }} against a threshold of {{ threshold }}. Data matched {{ data_matched }}.{% if margin %} Margin {{ margin }}.{% else %} A veto decides.{% endif %}
Total value {{ total }}.</p>

<h2>Why</h2>
<div class="scroll"><table><tr><th>Result</th><th>Rule</th><th>Number</th><th>Threshold</th></tr>
{% for r in reasons %}<tr><td class="{{ r.cls }}">{{ r.status }}</td>
<td>{{ r.description }}{% if r.veto %}<span class="veto">veto</span>{% endif %}
<details><summary class="mute">source</summary><div class="lineage">{{ r.lineage }}</div></details></td>
<td>{{ r.value }}{% if r.extra %}<div class="mute">{{ r.extra }}</div>{% endif %}</td><td>{{ r.op }} {{ r.threshold }}</td></tr>
{% endfor %}</table></div>

<h2>Price check: what our own reports say</h2>
<p><b>{{ price_head }}</b></p>
<p>Funds we own: {{ n_owned }} lines, {{ share_owned }} of value. The broker prices them at their value on {{ refdate }}.
{% if adjustments %}Since then, their managers told us:</p><ul>
{% for a in adjustments %}<li>{{ a.label }}, {{ a.fund }}: {{ a.note }} Fund value {{ a.pct }}, so this stake {{ a.usd }}. <span class="mute">Told on {{ a.told }}.</span>
<details><summary class="mute">source</summary><div class="lineage">{{ a.lineage }}</div></details></li>{% endfor %}</ul>
<p>Worth today by our data: {{ worth }} ({{ worth_change }} against the broker's value).{% else %}Nothing material since then.{% endif %}
{% if range_low %}Our range: {{ range_low }} to {{ range_high }} of the broker's value. Asking: {{ asking }}{% if asking_mid %} (midpoint {{ asking_mid }}){% endif %}.{% else %}No price: no fund we own in this offer.{% endif %}</p>
{% if priced %}<ul>{% for p in priced %}<li>{{ p.label }}, {{ p.fund }}: {{ p.low }} to {{ p.high }}. <span class="mute">Method: {{ p.method }}</span></li>{% endfor %}</ul>{% endif %}
<p class="mute">A first view, not a bid. Funds we don't own get no price. Share of value with managers we know: {{ share_known }}. Largest five lines: {{ top5 }} of value.</p>

{% if unresolved %}<h2>Unmatched</h2><ul>
{% for u in unresolved %}<li>{{ u.label }}: {{ u.desc }} {{ u.vintage }}, value {{ u.value }}. {{ u.method }}.
Candidates: {{ u.cands }}.</li>{% endfor %}</ul>{% endif %}

<h2>Lines</h2>
<div class="scroll"><table><tr><th>Line</th><th>Description</th><th>Vintage</th><th>Value</th><th>USD</th><th>Called</th><th>DPI</th><th>TVPI</th><th>Tier</th><th>Match</th></tr>
{% for l in lines %}<tr><td>{{ l.label }}</td><td>{{ l.desc }}{% if l.hint %} <span class="mute">"{{ l.hint }}"</span>{% endif %}</td><td>{{ l.vintage }}</td>
<td>{{ l.value }}</td><td>{{ l.usd }}</td><td>{{ l.pc }}</td><td>{{ l.dpi }}</td><td>{{ l.tvpi }}</td>
<td>{{ l.tier }}</td><td>{{ l.match }}<details><summary class="mute">how</summary><div class="lineage">{{ l.lineage }}</div></details></td></tr>
{% endfor %}</table></div>

{% if exposure %}<h2>Our exposure to these managers</h2>
<div class="scroll"><table><tr><th>Manager</th><th>Funds we hold</th><th>Our commitments</th><th>Our value</th></tr>
{% for e in exposure %}<tr><td>{{ e.name }}</td><td>{{ e.funds }}</td><td>{{ e.commit }}</td><td>{{ e.value }}</td></tr>{% endfor %}
</table></div>{% endif %}

{% if rejects %}<h2>Rows blocked at validation</h2><ul>
{% for r in rejects %}<li>{{ r.ref }}: {{ r.reason }}</li>{% endfor %}</ul>{% endif %}

<h2>Sources</h2>
<div class="lineage">parser {{ o.parser }} ({{ o.parser_version }}); rulebook v{{ v.rulebook_version }} sha {{ v.rulebook_sha }};
offer row {{ o.offer_id }}; message {{ o.message_id }}; metric versions: {{ metric_versions }}.
Each number expands to table, row id, metric and version (hover or inspect the number).</div>
</main></body></html>
"""

FAIL_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Quick Look: unreadable offer</title>
<style>body{font:15px/1.45 -apple-system,sans-serif;margin:24px 16px;background:#fbfaf7;color:#1d1d1b}</style></head>
<body><main><h1>Could not read this offer: {{ reason }}. Saved to review queue.</h1>
<p>No verdict. Subject: {{ subject }}</p></main></body></html>"""

_env = Environment(autoescape=True)
_tpl = _env.from_string(TEMPLATE)
_fail = _env.from_string(FAIL_TEMPLATE)


def render_parse_failure(subject: str, reason: str) -> str:
    return _fail.render(subject=subject, reason=reason)


def render(con, offer_id: str) -> str:
    o = store.one(con, "SELECT * FROM silver_offers WHERE offer_id = ?", [offer_id])
    v = store.one(con, "SELECT * FROM gold_verdicts WHERE offer_id = ?", [offer_id])
    mets = {m["metric"]: m for m in store.rows(con, "SELECT * FROM gold_offer_metrics WHERE offer_id = ?", [offer_id])}
    pv = o["parser_version"]

    def M(metric: str, display: str | None = None) -> Markup:
        m = mets[metric]
        if m["value"] is None:
            return Markup('<span class="na">no value</span>')
        return span(display or fmt_metric(metric, m["value"]), "gold_offer_metrics", m["row_id"], metric, m["version"])

    def S(table: str, row: str, col: str, display: str, version: str = pv) -> Markup:
        return span(display, table, row, col, version)

    # Headline: rebuilt from stored segments, each number pointing at its metric row.
    segs = json.loads(v["headline_json"])
    headline = Markup("").join(escape(s) if isinstance(s, str) else M(s[0], s[2]) for s in segs)

    reasons = []
    for r in store.rows(con, "SELECT * FROM gold_rule_results WHERE offer_id = ? ORDER BY rank", [offer_id]):
        thr = (f"{r['threshold']:.0%}" if r["metric"] in PCT else
               (fmt_metric("guidance_minus_range_top", r["threshold"]) if r["metric"] == "guidance_minus_range_top"
                else f"{r['threshold']:g}"))
        extra = None
        if r["metric"] == "guidance_minus_range_top":
            extra = Markup("asking ") + S("silver_offers", offer_id, "asking_price_text", o["asking_price_text"] or "") + \
                Markup(", our range top ") + M("price_range_high")
        reasons.append(dict(
            status=r["status"], cls={"met": "met", "not met": "notmet"}.get(r["status"], "nc"),
            description=r["description"], veto=r["veto"],
            value=M(r["metric"]) if r["value"] is not None else Markup('<span class="nc">not checked</span>'),
            op=r["op"].replace("<=", "≤").replace(">=", "≥"),
            threshold=S("gold_rule_results", r["row_id"], "threshold", thr, f"rulebook v{r['rulebook_version']}"),
            extra=extra,
            lineage=f"{r['metric']} {REGISTRY[r['metric']][0]} from gold_offer_metrics row {offer_id}:{r['metric']}; "
                    f"threshold from gold_rule_results row {r['row_id']} (rulebook v{r['rulebook_version']}). "
                    f"{REGISTRY[r['metric']][1]}." + (f" {mets[r['metric']]['note']}." if mets[r['metric']]['note'] else "")))

    lines, priced, unresolved = [], [], []
    q = """SELECT s.*, g.tier, g.fund_name, g.manager_name, g.confidence, g.resolved, g.method, g.candidates_json,
                  g.flags_json, g.value_usd, g.fx_rate, g.fx_row_id, g.price_low, g.price_high, g.price_method,
                  g.our_value_row, g.compared_series_row, g.match_version
           FROM silver_offer_lines s JOIN gold_line_matches g USING (line_id) WHERE s.offer_id = ? ORDER BY s.line_no"""
    for l in store.rows(con, q, [offer_id]):
        lid, mv = l["line_id"], l["match_version"]
        desc = S("silver_offer_lines", lid, "description", l["description"] or "")
        vint = S("silver_offer_lines", lid, "vintage", str(l["vintage"])) if l["vintage"] else ""
        value = S("silver_offer_lines", lid, "value", fmt_money(l["value"], l["currency"]))
        cands = json.loads(l["candidates_json"])
        if l["tier"] == "same_fund" and l["confidence"] is None:
            match_cell = Markup(f"{escape(l['fund_name'])}, analyst pick")
        elif l["tier"] == "same_fund":
            match_cell = Markup(f"{escape(l['fund_name'])}, ") + S("gold_line_matches", lid, "confidence",
                                                                   f"{l['confidence']:.2f}", mv)
        elif l["tier"] == "same_manager":
            match_cell = escape(l["manager_name"] or "")
        else:
            match_cell = escape(", ".join(c["fund_name"] for c in cands) or "none")
        lines.append(dict(
            label=l["label"], desc=desc, hint=l["name_fragment"] and S("silver_offer_lines", lid, "name_fragment", l["name_fragment"]),
            vintage=vint, value=value,
            usd=S("gold_line_matches", lid, "value_usd", fmt_money(l["value_usd"], "USD"), mv),
            pc=S("silver_offer_lines", lid, "pct_called", f"{l['pct_called']:.0%}") if l["pct_called"] is not None else "",
            dpi=S("silver_offer_lines", lid, "dpi", f"{l['dpi']:.2f}x") if l["dpi"] is not None else "",
            tvpi=S("silver_offer_lines", lid, "tvpi", f"{l['tvpi']:.2f}x") if l["tvpi"] is not None else "",
            tier=TIER_WORDS[l["tier"]] + ("" if l["resolved"] else " (open)"),
            match=match_cell,
            lineage=(f"{l['method']}; {mv}; line row {lid}; fx {l['fx_row_id']}"
                     + (f"; compared with ref_fund_series row {l['compared_series_row']}" if l["compared_series_row"] else "")
                     + (f"; candidates: " + ", ".join(f"{c['fund_id']} {c['fund_name']} score {c['score']}" for c in cands) if cands else "")
                     + ("; " + "; ".join(json.loads(l["flags_json"])) if json.loads(l["flags_json"]) else ""))))
        if l["price_low"] is not None:
            priced.append(dict(label=l["label"], fund=l["fund_name"],
                               low=S("gold_line_matches", lid, "price_low", f"{l['price_low']:.0%}", "price v2"),
                               high=S("gold_line_matches", lid, "price_high", f"{l['price_high']:.0%}", "price v2"),
                               method=S("gold_line_matches", lid, "price_method", l["price_method"], "price v2")))
        if not l["resolved"]:
            unresolved.append(dict(label=l["label"], desc=desc, vintage=vint, value=value,
                                   method=l["method"].capitalize(),
                                   cands=", ".join(f"{c['fund_name']} ({c['manager_name']})" for c in cands) or "none"))

    adjustments = []
    for a in store.rows(con, "SELECT * FROM gold_price_adjustments WHERE offer_id = ? ORDER BY line_no, event_date",
                        [offer_id]):
        A = lambda col, disp: span(disp, "gold_price_adjustments", a["row_id"], col, a["version"])  # noqa: E731
        up = a["line_change_usd"] >= 0
        adjustments.append(dict(
            label=a["label"], fund=a["fund_name"], note=A("note", a["note"]),
            pct=Markup("up " if up else "down ") + span(f"{abs(a['fund_change_pct']):.1%}", "gold_price_adjustments",
                                                         a["row_id"], "fund_change_pct", a["version"], absolute=True),
            usd=Markup("gains " if up else "loses ") + span(fmt_money(abs(a["line_change_usd"]), "USD"),
                                                            "gold_price_adjustments", a["row_id"], "line_change_usd",
                                                            a["version"], absolute=True),
            told=A("reported_at", a["reported_at"].isoformat()),
            lineage=f"ref_fund_events row {a['event_id']}; gold_price_adjustments row {a['row_id']}; price {a['version']}"))
    view = v["price_view"]
    if v["veto_rule"] == "price_veto":
        price_head = "Too expensive by our own data: the asking price is well above the top of our range."
    else:
        price_head = {"cheap": "The price looks right by our own data: the asking price is within our range.",
                      "expensive": "The price looks high by our own data: the asking price is above the top of our range.",
                      "no view": "No price view: we own none of these funds."}[view]
    exposure = [dict(name=e["manager_name"],
                     funds=span(str(e["funds_held"]), "gold_exposure", e["row_id"], "funds_held", e["version"]),
                     commit=span(fmt_money(e["our_commitment_usd"], "USD"), "gold_exposure", e["row_id"], "our_commitment_usd", e["version"]),
                     value=span(fmt_money(e["our_value_usd"], "USD"), "gold_exposure", e["row_id"], "our_value_usd", e["version"]))
                for e in store.rows(con, "SELECT * FROM gold_exposure WHERE offer_id = ? ORDER BY our_value_usd DESC", [offer_id])]
    rejects = [dict(ref=r["line_ref"], reason=S("silver_rejects", r["row_id"], "reason", r["reason"]))
               for r in store.rows(con, "SELECT * FROM silver_rejects WHERE offer_id = ? ORDER BY row_id", [offer_id])]
    dec = store.team_decision(con, offer_id)
    ov = dec if dec and dec["kind"] == "override" else None
    override = kept = None
    if dec and dec["kind"] == "keep":
        kept = dict(verdict=dec["verdict"],
                    user_name=span(dec["user_name"], "decisions", dec["decision_id"], "user_name", "decision"),
                    at=span(dec["created_at"].strftime("%Y-%m-%d %H:%M"), "decisions", dec["decision_id"], "created_at", "decision"))
    picks = []
    last: dict = {}
    for r in store.rows(con, "SELECT * FROM line_resolutions WHERE offer_id = ? ORDER BY created_at", [offer_id]):
        last[r["line_no"]] = r
    for r in sorted(last.values(), key=lambda r: r["line_no"]):
        rid = r["resolution_id"]
        picks.append(dict(label=r["label"], fund=span(r["fund_name"], "line_resolutions", rid, "fund_name", "resolution"),
                          user=span(r["user_name"], "line_resolutions", rid, "user_name", "resolution"),
                          at=span(r["created_at"].strftime("%Y-%m-%d %H:%M"), "line_resolutions", rid, "created_at", "resolution")))
    if ov:
        override = dict(new_verdict=ov["new_verdict"], original_verdict=ov["original_verdict"],
                        reason=span(ov["reason"], "overrides", ov["override_id"], "reason", "override"),
                        user_name=span(ov["user_name"], "overrides", ov["override_id"], "user_name", "override"),
                        at=span(ov["created_at"].strftime("%Y-%m-%d %H:%M"), "overrides", ov["override_id"], "created_at", "override"))
    V = lambda col, disp: span(disp, "gold_verdicts", offer_id, col, f"rulebook v{v['rulebook_version']}")  # noqa: E731
    return _tpl.render(
        o=o, v=v, offline=o["parser_offline"], override=override, kept=kept, picks=picks, headline=headline,
        received=S("silver_offers", offer_id, "email_date", o["email_date"].strftime("%Y-%m-%d %H:%M")),
        refdate=S("silver_offers", offer_id, "reference_date", o["reference_date"].isoformat()),
        seller=o["seller_type"] or "seller type not given",
        score=V("score", f"{v['score']:.2f}") if v["score"] is not None else "n/a",
        threshold=V("threshold", f"{v['threshold']:g}"),
        data_matched=V("data_matched", f"{v['data_matched']:.0%}"),
        margin=V("margin", f"{v['margin']:.2f}") if v["margin"] is not None else None,
        total=M("total_value_usd"),
        reasons=reasons, n_owned=M("count_same_fund"), share_owned=M("share_value_same_fund"),
        range_low=M("price_range_low") if mets["price_range_low"]["value"] is not None else None,
        range_high=M("price_range_high"),
        asking=S("silver_offers", offer_id, "asking_price_text", o["asking_price_text"] or "") if o["asking_price_text"] else "not given",
        asking_mid=M("asking_price_mid") if mets["asking_price_mid"]["value"] is not None else None,
        priced=priced, adjustments=adjustments, price_head=price_head,
        worth=M("worth_today_owned_usd", fmt_money(mets["worth_today_owned_usd"]["value"], "USD"))
        if mets["worth_today_owned_usd"]["value"] is not None else None,
        worth_change=M("worth_change_owned", f"{mets['worth_change_owned']['value'] * 100:+.1f}%")
        if mets["worth_change_owned"]["value"] is not None else None,
        share_known=M("share_value_known_manager"), top5=M("top5_concentration"),
        unresolved=unresolved, lines=lines, exposure=exposure, rejects=rejects,
        metric_versions=", ".join(f"{k} {m['version']}" for k, m in sorted(mets.items())),
    )


# ---------------------------------------------------------------- validator (C-2)

NUM_RE = re.compile(r"[-+]?[$€£]?\d[\d,]*(?:\.\d+)?\s*(?:%|x|M|bn)?")
ORDINAL_RE = re.compile(r"\b(?:Fund|Line|Interest)\s+#?$")


def normalise(text: str) -> float | None:
    """'68%' -> 0.68, '1.71x' -> 1.71, '€16.1M' -> 16.1, '+3%' -> 0.03."""
    t = text.strip().replace(",", "")
    m = re.match(r"^([-+]?)[$€£]?(\d+(?:\.\d+)?)\s*(%|x|M|bn)?$", t)
    if not m:
        return None
    v = float(m[2]) * (-1 if m[1] == "-" else 1)
    if m[3] == "%":
        v /= 100
    elif m[3] == "bn":
        v *= 1000
    return v


def _tolerance(text: str) -> float:
    t = text.strip().replace(",", "")
    m = re.search(r"\d+(?:\.(\d+))?", t)
    dec = len(m[1]) if m and m[1] else 0
    tol = 0.5 * 10 ** -dec
    return tol / 100 if t.endswith("%") else tol


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict]] = []
        self.spans: list[dict] = []
        self.free_text: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        skipping = tag in ("style", "script", "title") or "lineage" in cls
        if tag in ("meta", "br", "img", "input"):
            return
        self.stack.append((tag, a, skipping))
        if skipping:
            self.skip += 1
        if tag == "span" and "n" in cls:
            self.spans.append(dict(attrs=a, text=""))

    def handle_endtag(self, tag):
        while self.stack:
            t, a, skipping = self.stack.pop()
            if skipping:
                self.skip -= 1
            if t == tag:
                break

    def _in_span(self):
        return any(t == "span" and "n" in (a.get("class") or "").split() for t, a, _ in self.stack)

    def handle_data(self, data):
        if self.skip:
            return
        if self._in_span():
            self.spans[-1]["text"] += data
        else:
            self.free_text.append(data)


def _matches(display: str, value, absolute: bool = False) -> bool:
    if isinstance(value, dt.datetime):
        return display == value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, dt.date):
        return display in (value.isoformat(), f"{value.day} {value:%B}", f"{value.day} {value:%B %Y}")
    if isinstance(value, str):
        return display == value
    if isinstance(value, bool):
        return display == str(value)
    if value is None:
        return False
    n = normalise(display)
    if n is None:
        return False
    v = abs(float(value)) if absolute else float(value)
    return abs(n - v) <= _tolerance(display) + 1e-9


def validate(html: str, lookup: Callable[[str, str, str], object]) -> list[str]:
    """Return problems. An empty list means every number on the page is sourced."""
    c = _Collector()
    c.feed(html)
    problems, sourced = [], []
    for s in c.spans:
        a, text = s["attrs"], s["text"].strip()
        missing = [k for k in ("data-table", "data-row", "data-metric", "data-version") if not a.get(k)]
        if missing:
            problems.append(f"number '{text}' is missing {', '.join(missing)}")
            continue
        try:
            value = lookup(a["data-table"], a["data-row"], a["data-metric"])
        except KeyError:
            problems.append(f"number '{text}' cites {a['data-table']}/{a['data-row']}/{a['data-metric']}, which does not exist")
            continue
        if not _matches(text, value, a.get("data-abs") == "1"):
            problems.append(f"number '{text}' does not match its source value {value!r} "
                            f"({a['data-table']}/{a['data-row']}/{a['data-metric']})")
            continue
        n = normalise(text)
        if n is not None:
            sourced.append(n)
    for chunk in c.free_text:
        for m in NUM_RE.finditer(chunk):
            tok = m.group(0).strip()
            digits = re.sub(r"[^\d]", "", tok)
            before = chunk[: m.start()]
            if re.fullmatch(r"(19|20)\d\d", tok):
                continue  # a year
            if ORDINAL_RE.search(before) and digits.isdigit() and len(digits) <= 3:
                continue  # Fund 9
            n = normalise(tok)
            if n is not None and any(abs(n - s) <= _tolerance(tok) + 1e-9 for s in sourced):
                continue  # repeats a sourced number
            ctx = chunk[max(0, m.start() - 30): m.end() + 30].strip()
            problems.append(f"unsourced number '{tok}' in: ...{ctx}...")
    return problems
