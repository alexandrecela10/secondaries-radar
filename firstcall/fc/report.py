"""Weekly list (PRD Annex A screen 2) and the `why` view. Wording is our action, never a claim
about the pension: PREPARE means we prepare; it does not mean the pension is selling."""
from __future__ import annotations

import html
from datetime import datetime, timedelta

from . import pensions, store
from .config import rulebook
from .scoring import SignalInput, score

DISCLAIMER = ("PREPARE and WATCH describe our own preparation. They are not statements that a pension "
              "is selling. Evidence is public headlines with links.")


def pension_scores(con, as_of: datetime) -> list[dict]:
    out = []
    pids = [r[0] for r in con.execute("SELECT DISTINCT pension_id FROM gold_signals ORDER BY 1").fetchall()]
    for pid in pids:
        sigs = store.load_signals(con, pid)
        sc = score([SignalInput(s["signal_id"], s["signal_type"], s["confidence"], s["published_at"]) for s in sigs], as_of)
        if not sc.contributions:
            continue
        by_id = {s["signal_id"]: s for s in sigs}
        top = by_id[sc.contributions[0].signal_id]
        out.append({"pension_id": pid, "name": pensions.ALL[pid].name, "score": sc.score, "S": sc.S,
                    "verdict": sc.verdict, "n": len(sc.contributions), "top": top, "contributions": sc.contributions,
                    "signals": by_id})
    out.sort(key=lambda r: -r["score"])
    return out


def classifier_banner(con) -> str:
    names = {r[0] for r in con.execute("SELECT DISTINCT classifier FROM classified_items").fetchall()}
    if any(n.startswith("claude") for n in names):
        return f"Classifier: {', '.join(sorted(names))}"
    return "Offline classifier in use (keyword rules). Set ANTHROPIC_API_KEY to use Claude."


def weekly_text(con, as_of: datetime, watch_limit: int = 8) -> str:
    rows = pension_scores(con, as_of)
    week = (as_of - timedelta(days=as_of.weekday())).date()
    lines = [f"FIRST CALL   week of {week}   as of {as_of.date()}   rulebook v{rulebook().version}",
             classifier_banner(con), "-" * 78]
    prepare = [r for r in rows if r["verdict"] == "PREPARE"]
    watch = [r for r in rows if r["verdict"] == "WATCH"]
    for r in prepare + watch[:watch_limit]:
        t = r["top"]
        more = f"   {r['n'] - 1} more signal{'s' if r['n'] != 2 else ''}" if r["n"] > 1 else ""
        lines.append(f"{r['verdict']:<8} {r['pension_id']:<14} score {r['score']:.2f}  \"{t['quote'][:70]}\"")
        lines.append(f"{'':<23}{t['outlet'] or 'unknown outlet'}, {t['published_at'].date()}{more}")
        lines.append(f"{'':<23}{t['link'][:90]}")
    if not prepare:
        lines.append(f"No pension crossed PREPARE. {len(watch)} on WATCH.")
    else:
        lines.append(f"{len(prepare)} on PREPARE, {len(watch)} on WATCH"
                     + (f" (top {watch_limit} shown)." if len(watch) > watch_limit else "."))
    lines += ["-" * 78, DISCLAIMER]
    return "\n".join(lines)


def why_text(con, pension_id: str, as_of: datetime) -> str:
    rb = rulebook()
    if pension_id not in pensions.ALL:
        matches = [p for p in pensions.ALL if pension_id.lower() in p]
        if len(matches) != 1:
            return f"Unknown pension '{pension_id}'. Try one of: {', '.join(sorted(pensions.ALL)[:12])} ..."
        pension_id = matches[0]
    p = pensions.ALL[pension_id]
    sigs = store.load_signals(con, pension_id)
    sc = score([SignalInput(s["signal_id"], s["signal_type"], s["confidence"], s["published_at"]) for s in sigs], as_of)
    by_id = {s["signal_id"]: s for s in sigs}
    lines = [f"{sc.verdict}: {p.name} ({pension_id}), score {sc.score:.2f} as of {as_of.date()}",
             f"score = 1 - exp(-S), S = {sc.S:.3f}; PREPARE at {rb.prepare_threshold} or more; "
             f"half-life {rb.half_life_days:.0f} days; window {rb.window_days} days.", ""]
    if not sc.contributions:
        lines.append("No signal inside the window.")
    for c in sc.contributions:
        s = by_id[c.signal_id]
        lines += [
            f"- {c.signal_type}  weight {c.weight:.1f} x confidence {c.confidence:.2f} x decay {c.decay:.2f} "
            f"(age {c.age_days:.0f} d) = {c.value:.3f}",
            f"  \"{s['quote']}\"",
            f"  {s['outlet'] or 'unknown outlet'}, {s['published_at'].date()}, copies {s['n_copies']}",
            f"  {s['link']}",
        ]
    outside = [s for s in sigs if s["signal_id"] not in {c.signal_id for c in sc.contributions}]
    if outside:
        lines.append(f"\n{len(outside)} older or later signal(s) outside the window are not counted.")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def weekly_html(con, as_of: datetime) -> str:
    rows = pension_scores(con, as_of)
    esc = html.escape
    items = []
    for r in rows[:25]:
        evid = []
        for c in r["contributions"]:
            s = r["signals"][c.signal_id]
            evid.append(f"<li><b>{esc(c.signal_type)}</b> {c.value:.3f} = {c.weight:.1f} x {c.confidence:.2f} x {c.decay:.2f}"
                        f"<br>&ldquo;{esc(s['quote'])}&rdquo;<br><span class=m>{esc(s['outlet'] or '')}, "
                        f"{s['published_at'].date()} &middot; <a href=\"{esc(s['link'])}\">link</a></span></li>")
        items.append(f"<details class={r['verdict'].lower()}><summary><span class=v>{r['verdict']}</span> "
                     f"<b>{esc(r['name'])}</b> <span class=s>{r['score']:.2f}</span> "
                     f"&ldquo;{esc(r['top']['quote'][:90])}&rdquo;</summary><ul>{''.join(evid)}</ul></details>")
    n_prep = sum(r["verdict"] == "PREPARE" for r in rows)
    summary = (f"{n_prep} pension(s) on PREPARE, {len(rows) - n_prep} on WATCH." if n_prep
               else f"No pension crossed PREPARE. {len(rows)} on WATCH.")
    return f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>First Call weekly list</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6b6b70;--line:#e3e3e6;--prep:#9a3412;--watch:#475569}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ececef;--mut:#9a9aa1;--line:#2c2c30;--prep:#fb923c;--watch:#94a3b8}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px}}
details{{border-top:1px solid var(--line);padding:8px 0}} summary{{cursor:pointer}}
.v{{font-weight:700;font-size:12px;letter-spacing:.04em;margin-right:6px}} .prepare .v{{color:var(--prep)}} .watch .v{{color:var(--watch)}}
.s{{font-variant-numeric:tabular-nums;color:var(--mut);margin:0 6px}} .m{{color:var(--mut);font-size:13px}} li{{margin:6px 0}}
a{{color:inherit}} footer{{color:var(--mut);font-size:13px;border-top:1px solid var(--line);padding-top:8px;margin-top:16px}}
</style>
<h1>First Call, week of {(as_of - timedelta(days=as_of.weekday())).date()}</h1>
<p>As of {as_of.date()}. Rulebook v{rulebook().version}. {esc(classifier_banner(con))}</p>
<p><b>{summary}</b></p>
{''.join(items)}
<footer>{esc(DISCLAIMER)} News index: Google News RSS and the GDELT Project (gdeltproject.org).</footer></html>"""
