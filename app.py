"""Secondaries Radar: a guided demo, written to you.

Quick Look runs live with no key: the offline parser reads the offer, and matching, metrics and
verdicts are plain code. First Call replays a committed, anonymised snapshot of one real news run.
Optional live mode: with ANTHROPIC_API_KEY in Streamlit secrets, Claude can read pasted text,
capped at 3 calls per visitor and 30 per day.

Run: streamlit run app.py
"""
from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

import demo  # noqa: F401  (puts the repo packages first on the path, switches tracing off)
from demo import fcd, hood, ql

SESSION_CAP = 3
DAILY_CAP = 30
TEAM = ["layla", "sam", "priya", "jonah"]
FOOTER = ("Synthetic buyer and offers, invented names. First Call uses public headlines and short quotes. "
          "Not investment advice. Not a statement of any pension's intent.")

st.set_page_config(page_title="Secondaries Radar", layout="wide")
ss = st.session_state
ss.setdefault("live_used", 0)
ss.setdefault("ql_decisions", {})     # offer code -> decision
ss.setdefault("ql_picks", {})         # offer code -> {line_no: fund_id}
ss.setdefault("fc_decisions", [])
ss.setdefault("pasted_offer", None)


# ------------------------------------------------------------------ helpers

def esc(text) -> str:
    """Streamlit reads $...$ as maths. Money must show as text."""
    return str(text).replace("$", "\\$")


def md(text: str, **kw):
    st.markdown(esc(text), **kw)


def cap(text: str, **kw):
    st.caption(esc(text), **kw)


def table(rows, **kw):
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", **kw)


@st.cache_resource
def daily_counter() -> dict:
    return {}


def live_key() -> str | None:
    try:
        return st.secrets.get("ANTHROPIC_API_KEY") or None
    except Exception:  # no secrets file: live mode stays hidden
        return None


def live_available() -> tuple[bool, str]:
    if not live_key():
        return False, ""
    if daily_counter().get(str(dt.date.today()), 0) >= DAILY_CAP:
        return False, f"Today's {DAILY_CAP} live calls are used up. The offline path still works."
    if ss.live_used >= SESSION_CAP:
        return False, f"You have used your {SESSION_CAP} live calls. The offline path still works."
    return True, ""


def count_live_call() -> None:
    ss.live_used += 1
    c = daily_counter()
    c[str(dt.date.today())] = c.get(str(dt.date.today()), 0) + 1


def anthropic_client():
    import anthropic

    return anthropic.Anthropic(api_key=live_key())


def verdict_box(v, label: str = ""):
    text = esc(f"{label}{v.headline}")
    (st.success if v.verdict == "LOOK CLOSER" else st.error)(text)


def quiz(key: str, questions: list[tuple[str, list[str], int, str]]):
    with st.expander("Check yourself"):
        for i, (q, options, right, why) in enumerate(questions):
            a = st.radio(esc(q), options, index=None, key=f"{key}-{i}")
            if a is not None:
                (st.success if options.index(a) == right else st.warning)(
                    esc(("Right. " if options.index(a) == right else "Not quite. ") + why))


# ------------------------------------------------------------------ header and sidebar

st.title("Secondaries Radar")
md("You work at a fund that buys private equity stakes from investors who want out early. This is an "
   "**LP-led secondary**: a pension or an insurer sells its share of funds, and you buy it, usually below the "
   "funds' reported value. Two tools help your team. **Quick Look** reads a broker's sale offer and says look "
   "closer or pass, with every number sourced. **First Call** reads public news and lists pensions that show "
   "signs they may sell, so you can prepare before an offer arrives. **The model reads; code decides.**")

with st.sidebar:
    st.markdown("### How to use this demo")
    md("Each tab follows one person through one job, step by step. Every control changes the result live.")
    md("- **Quick Look**: you are Layla, an associate, at 09:00.\n- **First Call**: you are Omar, a partner, "
       "on Monday at 08:00.\n- **Under the hood**: how it is built and measured.")
    st.divider()
    ok, why_not = live_available()
    if live_key():
        md(f"**Live mode:** on. Claude can read text you paste. Calls left for you: "
           f"**{max(0, SESSION_CAP - ss.live_used)} of {SESSION_CAP}**.")
        if why_not:
            cap(why_not)
    else:
        md("**Live mode:** off. Everything here runs on offline code, with no key and no cost.")
    st.divider()
    if st.button("Start over"):
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()
    cap(FOOTER)

tab_ql, tab_fc, tab_hood = st.tabs(["Quick Look: you are Layla", "First Call: you are Omar", "Under the hood"])

# ================================================================== QUICK LOOK

with tab_ql:
    st.header("You are Layla, a secondaries associate")
    md("A broker emails a sale offer at 09:00. Your head of team wants a sourced \"look closer or pass\" by 10:00, "
       "so they can decide whether to sign the NDA (the non-disclosure agreement that opens the data room). "
       "Before the NDA, the broker hides fund names. Quick Look works out which funds they are from their numbers.")

    # ---- the offer
    codes = ql.offer_codes()
    PASTE = "Paste your own offer text"
    NOT_OFFER = "Thistle (an email that is not an offer)"
    names = {c: (f"{c} (the journey offer)" if c == "Cedar" else
                 f"{c} ({ql.labels()[c].get('archetype', '')}, {ql.labels()[c].get('split', '')} split)"
                 if c not in ql.JOURNEY_OFFERS else c) for c in codes}
    choice = st.selectbox("Which offer arrives?", codes + [NOT_OFFER, PASTE], format_func=lambda c: names.get(c, c),
                          key="ql_choice")

    offer = None
    if choice == PASTE:
        md("Paste a broker email. The **offline parser** reads it with plain rules: it needs \"Project <Name>\", a "
           "reference date and lines like \"Fund 1: ... 2016, commitment $20M, 92% called, DPI 0.84x, TVPI 1.71x, "
           "NAV $16M\". Your fund lines are matched against the same synthetic universe.")
        text = st.text_area("Offer text", value=ql.EXAMPLE_PASTE, height=220, key="paste_text")
        live_ok, live_why = live_available()
        modes = ["Offline parser (rules, free)"] + (["Claude parser (live, counts against your cap)"] if live_key() else [])
        mode = st.radio("Who reads it?", modes, horizontal=True, key="paste_mode")
        if mode != modes[0]:
            cap("Your text is sent to Anthropic's API. Do not paste anything confidential.")
            if live_why:
                st.warning(live_why)
        if st.button("Read this offer", type="primary"):
            parser = None
            if mode != modes[0]:
                if not live_ok:
                    st.stop()
                from quicklook.parse.claude import ClaudeParser

                parser = ClaudeParser(client=anthropic_client())
                count_live_call()
            with st.spinner("Reading..."):
                o, err = ql.parse_pasted(text, parser)
            ss.pasted_offer, ss.pasted_err = o, err
            ss.ql_picks.pop("__pasted__", None)
            ss.ql_decisions.pop("__pasted__", None)
        if ss.get("pasted_err"):
            st.error(esc(ss.pasted_err))
        offer = ss.pasted_offer
        if offer is None:
            st.info("Press \"Read this offer\" to run it.")
    elif choice == NOT_OFFER:
        from quicklook.emailio import read_email

        content = read_email((ql.SNAP / "offers" / "thistle.eml").read_bytes())
        st.code(f"Subject: {content.subject}\n\n{content.body.strip()}", language=None)
        try:
            ql._build(content)
            st.warning("The parser read it as an offer.")
        except ql.ParseError as e:
            st.error(esc(f"Could not read this offer: {e}. It goes to the review queue, with no verdict. "
                         "Quick Look never guesses an offer out of an email that is not one."))
    else:
        offer = ql.load_offer(choice)

    okey = "__pasted__" if choice == PASTE else choice

    if offer is not None:
        # ---- the rulebook (read here, so the verdict below reacts)
        with st.expander("Play: change the rulebook and watch the verdict flip", expanded=False):
            md("These are the team's rules, in plain English. A verdict is LOOK CLOSER when the **weighted share of "
               "rules met** reaches the threshold and no veto rule fails. Try: on Cedar, set the price rule's weight "
               "to 5. Or lower the known-managers bar to 90%.")
            base_name = st.radio("Start from", ["v2", "v1"], horizontal=True, key="ql_base",
                                 format_func=lambda v: {"v2": "Rulebook v2 (live today)",
                                                        "v1": "Rulebook v1 (the PRD's first version)"}[v])
            base = ql.load_rulebook(base_name)
            thr = st.slider("Threshold: share of weighted rules that must be met", 0.0, 1.0, float(base.threshold),
                            0.05, key=f"ql_thr_{base_name}")
            edits = {}
            for r in base.quick_look:
                c1, c2, c3 = st.columns([3, 2, 1])
                pct = r.metric != "fit_score"
                if r.metric == "guidance_minus_range_top":
                    v = c1.slider(f"{r.description}: asking price minus top of our range, at most (points)", -10, 10,
                                  int(round(r.value * 100)), 1, key=f"ql_v_{base_name}_{r.id}") / 100
                elif pct:
                    v = c1.slider(f"{r.description}: {ql.rulebook.OP_WORDS[r.op]} (%)", 0, 100,
                                  int(round(r.value * 100)), 5, key=f"ql_v_{base_name}_{r.id}") / 100
                else:
                    v = c1.slider(f"{r.description}: {ql.rulebook.OP_WORDS[r.op]} (fit score, 0 to 1)", 0.0, 1.0,
                                  float(r.value), 0.05, key=f"ql_v_{base_name}_{r.id}")
                w = c2.slider("Weight", 0.5, 5.0, float(r.weight), 0.5, key=f"ql_w_{base_name}_{r.id}")
                veto = c3.checkbox("Veto", value=r.veto, key=f"ql_x_{base_name}_{r.id}")
                edits[r.id] = {"value": v, "weight": w, "veto": veto}
            rb = ql.custom_rulebook(base, thr, edits)
            if rb.sha == "edited-in-demo":
                st.info(esc(f"You are using your own edit of rulebook {base_name}. Scroll to \"Why rule changes are "
                            "tested\" to see how it does on labelled offers it was not tuned on."))
            with st.popover("Read the rulebook as sentences"):
                for s in ql.rulebook_sentences(rb):
                    md(s)

        picks = ss.ql_picks.setdefault(okey, {})
        r0 = ql.run(offer, rb)            # what code says on its own
        r = ql.run(offer, rb, picks)      # with your picks

        # ---- 09:00
        st.subheader("09:00. The offer arrives")
        md(f"Project **{offer.code}** lands in the deals mailbox. A batch job checks the mailbox every minute, "
           "reads the email and runs the rules. You do nothing yet.")
        st.code(ql.email_text(offer), language=None)
        cap(f"Read by: {offer.parser}. Values at the reference date {offer.parsed.reference_date}; received "
            f"{offer.content.date:%Y-%m-%d %H:%M} UTC. Code only uses our own reports received before that moment.")
        if offer.rejects:
            st.warning(esc(f"{len(offer.rejects)} line(s) blocked at validation and left out: "
                           + "; ".join(f"{x['line_ref']}: {x['reason']}" for x in offer.rejects)))

        # ---- 09:02
        st.subheader("09:02. Read the verdict")
        verdict_box(r0.verdict)
        md(f"Confidence has two visible parts. **Data matched** is the share of value whose fund is settled: "
           f"**{r0.verdict.data_matched:.0%}**. **Margin** is the gap between the score and the threshold: "
           + (f"score **{r0.verdict.score:.2f}** against **{r0.verdict.threshold:g}**." if r0.verdict.score is not None
              else "no score.") + " High needs both to be strong.")
        with st.expander("How a buyer thinks about this"):
            md("A buyer sees dozens of offers a year and bids on a few. The first read is triage: is it worth "
               "signing an NDA and spending a week on the data room? The biggest edge is **funds you already own**. "
               "You hold the manager's own quarterly report, so you can price that line from your numbers, not "
               "the broker's, while other bidders discount for what they do not know.")

        # ---- 09:05
        st.subheader("09:05. Check why")
        md("Each rule, its number, the test, and where the number comes from. Code computes every number from "
           "the matched lines; the model never does.")
        icons = {"met": "Met", "not met": "Not met", "not checked": "Not checked"}
        table([{"": icons[x["status"]], "rule": x["rule"] + (" (veto)" if x["veto"] else ""), "weight": x["weight"],
                "number": x["number"], "test": x["test"], "source": x["source"]} for x in ql.reasons(r0)])
        nc = [x for x in ql.reasons(r0) if x["status"] == "not checked"]
        if nc:
            cap("Not checked: " + "; ".join(f"{x['rule']} ({x['note']})" for x in nc) +
                ". A rule with no number is left out of the score and lowers confidence by one level.")
        with st.expander("Every line and how code identified it"):
            table(ql.lines_table(r0))
            cap("Tiers: a fund we own (ratios match our report), a manager we know (other fund), unknown. "
                "Nothing is matched silently: each line carries its method.")

        # ---- 09:10
        st.subheader("09:10. Check a match")
        md("Pick a line. Inside one fund, every investor has the same **% called** (share of the commitment paid "
           "in), **DPI** (cash returned per dollar paid in) and **TVPI** (cash returned plus remaining value, per "
           "dollar paid in) at a given date. So if you own the fund, your own report shows the same ratios.")
        line_opts = [l.line_no for l in offer.lines]
        default_line = 4 if 4 in line_opts and offer.code == "Cedar" else line_opts[0]
        ln = st.selectbox("Line", line_opts, index=line_opts.index(default_line), format_func=lambda n: f"Fund {n}",
                          key=f"ql_line_{okey}")
        mv = ql.match_view(r0, ln)
        md(f"**{mv['label']}: {mv['description']}.** Tier: {mv['tier']}"
           + ("" if mv["settled"] else " (open)") + (f", confidence {mv['confidence']:.2f}" if mv["confidence"] else "")
           + f". Method: {mv['method']}.")
        if mv["rows"]:
            md(f"Compared with our report on **{mv['fund']}**, row {mv['report_row']}.")
            table(mv["rows"])
            md(f"Distance = gap / (5 x tolerance), capped at 1. Score = 1 - mean distance = **{mv['score']:.2f}**. "
               "Same fund needs 0.80 or more, every ratio inside tolerance plus the broker's rounding, and a clear "
               "gap over the next fund we own." + (f" Runner-up: {mv['runner_up']}." if mv["runner_up"] else ""))
        else:
            md("The broker gives no ratios here, or no fund we own fits, so there is nothing to compare.")
            if mv["candidates"]:
                md("Candidates: " + "; ".join(f"{c['fund_id']} {c['fund_name']} "
                                              f"({'we own it' if c['owned'] else 'we track it'})" for c in mv["candidates"]))
        for f in mv["flags"]:
            cap(f)
        with st.expander("A simplification a real investor would question"):
            md("Ratios are fund-level, but not exactly the seller's. Late closers pay equalisation, fee offsets "
               "differ, and some investors report recallable distributions differently. The tolerance bands absorb "
               "some of this, not all.")

        # ---- 09:15
        st.subheader("09:15. Fix an open line")
        still = ql.open_lines(r0)
        if not still:
            md("Every line is settled. Nothing to fix on this offer.")
        else:
            md("Code leaves a line open when it cannot be sure. You may know better, from a past deal. Pick the fund. "
               "Code still sets the tier: a fund we own becomes \"same fund\", a fund we only track becomes \"same "
               "manager\". Only funds that fit the line's description are offered.")
            for g in still:
                cands = ql.candidates_for(r0, g["line_no"])
                opts = ["leave it open"] + [c["fund_id"] for c in cands]
                cur = picks.get(g["line_no"], "leave it open")
                lab = {c["fund_id"]: f"{c['fund_id']} {c['name']} ({'we own it' if c['owned'] else 'we track it'})"
                       for c in cands}
                line = next(l for l in offer.lines if l.line_no == g["line_no"])
                pick = st.radio(esc(f"{g['label']}: {line.description}, {line.vintage}. Code says: {g['method']}."),
                                opts, index=opts.index(cur) if cur in opts else 0, horizontal=True,
                                format_func=lambda x: lab.get(x, x), key=f"ql_pick_{okey}_{g['line_no']}")
                if pick == "leave it open":
                    picks.pop(g["line_no"], None)
                else:
                    picks[g["line_no"]] = pick
            r = ql.run(offer, rb, picks)
            c1, c2 = st.columns(2)
            with c1:
                md("**Before your pick**")
                verdict_box(r0.verdict)
                cap(f"Data matched {r0.verdict.data_matched:.0%}.")
            with c2:
                md("**After your pick**")
                verdict_box(r.verdict)
                cap(f"Data matched {r.verdict.data_matched:.0%}. Recorded as layla's pick; the code's original "
                    "answer is kept beside it.")
            if offer.code == "Cedar":
                cap("Try F012 Ostrova for Fund 9. Confidence stays medium: data matched is now 100%, but the score is "
                    "only 0.15 above the threshold, under the 0.2 margin needed for high.")

        # ---- 09:20
        st.subheader("09:20. Decide")
        md("Keep the verdict, or override it with a reason. Both are stored with who, when and the rulebook "
           "version. Overrides are recorded, not learned from: over time they become the labelled set that tests "
           "the next rule change.")
        dec = ss.ql_decisions.get(okey)
        c1, c2 = st.columns([1, 2])
        with c1:
            if st.button(esc(f"Keep {r.verdict.verdict}"), key=f"keep_{okey}"):
                ss.ql_decisions[okey] = {"kind": "keep", "verdict": r.verdict.verdict,
                                         "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                         "rulebook": r.verdict.rulebook_version}
                st.rerun()
        with c2:
            other = "PASS" if r.verdict.verdict == "LOOK CLOSER" else "LOOK CLOSER"
            reason = st.text_input(f"Or override to {other}, because...", key=f"why_{okey}",
                                   placeholder="Seller will not move on price")
            if st.button(f"Override to {other}", key=f"ovr_{okey}"):
                if len(reason.strip()) < 5:
                    st.warning("An override needs a reason, at least a few words.")
                else:
                    ss.ql_decisions[okey] = {"kind": "override", "verdict": other, "reason": reason.strip(),
                                             "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                             "rulebook": r.verdict.rulebook_version}
                    st.rerun()
        dec = ss.ql_decisions.get(okey)
        if dec:
            if dec["kind"] == "keep":
                md(f"Recorded: {offer.code} keeps **{dec['verdict']}** by layla at {dec['at']}, rulebook v{dec['rulebook']}.")
            else:
                md(f"Recorded: {offer.code} **{r.verdict.verdict} -> {dec['verdict']}** by layla at {dec['at']}, "
                   f"rulebook v{dec['rulebook']}. Reason: {dec['reason']}. Recorded, not learned from.")
            cap("In this demo, decisions live in your browser session only.")

        # ---- 09:25
        st.subheader("09:25. Tell your head of team")
        md("One paragraph to paste. Every number comes from the run above. In the command line version, a "
           "validator checks each number against its database row before the memo is shown.")
        st.code(ql.memo(r, dec), language=None, wrap_lines=True)
        cap("Use the copy icon at the top right of the box.")

        # ---- rule changes
        st.subheader("Why rule changes are tested")
        md("The team changed one rule. Rulebook v1 required a plan fit of 0.70. It passed Willow, a good offer, "
           "because the fit score punishes any offer concentrated in one region. v2 lowered the bar to 0.55, and "
           "agreed with all 14 tune offers. Then it met 14 offers it had never seen (the **holdout**):")
        rows = []
        books = [("Rulebook v1", ql.load_rulebook("v1")), ("Rulebook v2", ql.load_rulebook("v2"))]
        if rb.sha == "edited-in-demo":
            books.append(("Your rulebook", rb))
        results = {}
        for name, b in books:
            a = ql.agreement(b)
            results[name] = a
            rows.append({"rulebook": name, "tune offers agreed": f"{a['tune']['kept']} of {a['tune']['total']}",
                         "holdout offers agreed": f"{a['holdout']['kept']} of {a['holdout']['total']}",
                         "good offers marked PASS (holdout)": a["holdout"]["good_passed"]})
        table(rows)
        md("v2 fixed Willow and lost its grip on old, tail-end funds: all three tail-end holdout offers get LOOK "
           "CLOSER against a PASS label. **That is overfitting.** The fix was tuned on 9 offers labelled by one "
           "person, and it did not generalise. A target of 80% on the holdout is reported, not gated.")
        which = st.radio("Show the holdout offers for", list(results), horizontal=True, key="ql_hold_which")
        table(results[which]["holdout"]["rows"])
        st.info("Here you can look at the holdout as often as you like. That is the trap. If you tune the rules "
                "until the holdout agrees, it stops being a holdout. The command line logs every look "
                "(`radar rules test --holdout`) for that reason.")
        quiz("ql", [
            ("Fund 4 shows DPI 0.84x and TVPI 1.71x. What does TVPI minus DPI tell you?",
             ["Cash already returned", "Remaining value still in the fund, per dollar paid in", "The fees paid"], 1,
             "TVPI counts cash returned plus remaining value. Take away DPI (cash returned) and you get the "
             "remaining value per dollar paid in: 0.87x here. That is what you would be buying."),
            ("Why does a fund under 4 years old have a weak fingerprint?",
             ["Its DPI is near 0 and TVPI near 1, like most young funds", "Brokers hide young funds",
              "Young funds have no NAV"], 0,
             "Young funds have returned little cash and are held near cost, so many funds look alike on the ratios."),
            ("Rulebook v2 agrees with 14 of 14 tune offers and 10 of 14 holdout offers. What do you trust?",
             ["The tune result", "The holdout result", "Their average"], 1,
             "The tune offers shaped the rule. Only the holdout says how it does on offers it has not seen."),
        ])

# ================================================================== FIRST CALL

with tab_fc:
    st.header("You are Omar, a partner planning the week")
    md("You own relationships with large US public pensions. On Monday at 08:00 you have 10 minutes to see which "
       "pensions show signs they may sell fund stakes, with the evidence, so the team is ready before a broker "
       "calls. **PREPARE and WATCH describe your own preparation. They never say a pension is selling.**")

    with st.expander("Play: change the signal weights and the PREPARE line", expanded=False):
        b = fcd.base_rulebook()
        md("Score = 1 - exp(-S), where S adds weight x confidence x decay for every signal in the last "
           f"{b.window_days} days. A signal loses half its weight every {b.half_life_days:.0f} days. PREPARE at the "
           "threshold or more. A sale that already closed is shown as evidence and adds nothing (rulebook v2).")
        thr = st.slider("PREPARE threshold", 0.1, 0.9, float(b.prepare_threshold), 0.05, key="fc_thr")
        cols = st.columns(len(b.weights))
        weights = {k: col.slider(fcd.SIGNAL_WORDS[k], 0.0, 2.0, float(v), 0.1, key=f"fc_w_{k}")
                   for col, (k, v) in zip(cols, b.weights.items())}
        frb = fcd.custom_rulebook(weights, thr)
        if not fcd.is_default(frb):
            st.info("You are using your own weights. Every score below, and both worked cases, use them.")
        cap("Try: set the threshold to 0.35. Pension A crosses PREPARE on one open sale report. Then look at the "
            "NYC case below: does it now give early warning, and on what evidence?")

    snap = fcd.snapshot()
    st.subheader("Overnight. Collect")
    md(f"A batch job fetched public news (Google News RSS and GDELT), linked each headline to at most one of 61 "
       f"public pensions, classified it, and updated the scores. This is a snapshot of one real run, as of "
       f"**{fcd.as_of().date()}**, classified by `{', '.join(snap['classifiers'])}`.")
    cap("Pension names are replaced by letters, and by [Pension] inside quotes. People's names and amounts are "
        "masked too. Links are withheld here because an article's address often names the pension. "
        "The local run keeps them. We store headline, outlet, date and a quote of 300 characters or less, never "
        "article text.")

    st.subheader("08:00. Read the list")
    rows = fcd.weekly(frb)
    n_prep = sum(r["verdict"] == "PREPARE" for r in rows)
    notes = {}
    for d in ss.fc_decisions:
        notes[d["pension"]] = (f"prepare: {d['owner']}" if d["action"] == "prepare" else f"dismissed: {d['reason'][:30]}")
    md(f"**{n_prep} on PREPARE, {len(rows) - n_prep} on WATCH.**" if n_prep
       else f"**No pension crossed PREPARE. {len(rows)} on WATCH.**")
    table([{"verdict": r["verdict"], "pension": r["pension"], "score": r["score"], "signals": r["signals"],
            "strongest evidence": r["top_quote"], "type": r["top_type"], "outlet": r["outlet"], "date": r["date"],
            "your decision": notes.get(r["pension"], "")} for r in rows],
          column_config={"score": st.column_config.ProgressColumn("score", min_value=0.0, max_value=1.0, format="%.2f")})
    closed = fcd.closed_sales()
    if closed:
        md("**Closed sales in the last 90 days** (evidence only, not scored):")
        for s in closed:
            md(f"- {s['published'].date()}: \"{s['quote']}\" ({s['outlet']})")

    st.subheader("08:03. Check the evidence")
    pension = st.selectbox("Open a pension", [r["pension"] for r in rows], key="fc_pension")
    w = fcd.why(pension, frb)
    md(f"**{w['verdict']}: {pension}, score {w['score']:.2f}.** S = {w['S']:.3f}; score = 1 - exp(-S); PREPARE at "
       f"{w['threshold']:g}; half-life {w['half_life']:.0f} days.")
    for p in w["parts"]:
        with st.container(border=True):
            md(f"> {p['quote']}")
            md(f"{p['outlet']}, {p['date']}, copies {p['copies']}. Link withheld in this public demo.")
            cap(f"{p['type']}: weight {p['weight']:.1f} x confidence {p['confidence']:.2f} x decay {p['decay']:.2f} "
                f"(age {p['age_days']} days) = {p['value']:.3f}")
    for s in w["closed"]:
        cap(f"Closed sale, evidence only: \"{s['quote']}\" ({s['outlet']}, {s['published'].date()})")
    if w["outside"]:
        cap(f"{w['outside']} older signal(s) outside the {w['window']}-day window are not counted.")
    with st.expander("How a buyer thinks about this"):
        md("A pension that cuts its private equity target, or sits above it, may sell stakes to get back in line. "
           "One that needs cash may sell too. A new CIO often reviews the portfolio. None of these means a sale. "
           "Together, and recent, they are a reason to call. The weights say how much each counts. The confidence "
           "is the classifier's own estimate. Decay means old news fades.")

    st.subheader("08:06. See the history")
    tl = fcd.timeline(pension, frb)
    df = pd.DataFrame(tl["rows"])
    df["week"] = pd.to_datetime(df["week"])
    line = alt.Chart(df).mark_line(point=True).encode(x=alt.X("week:T", title=None),
                                                      y=alt.Y("score:Q", scale=alt.Scale(domain=[0, 1]), title="score"),
                                                      tooltip=["week:T", "score:Q"])
    rule = alt.Chart(pd.DataFrame({"y": [tl["threshold"]]})).mark_rule(strokeDash=[6, 4], color="#c2410c").encode(y="y:Q")
    label = alt.Chart(pd.DataFrame({"y": [tl["threshold"]], "t": ["PREPARE"]})).mark_text(
        align="left", dx=4, dy=-6, color="#c2410c").encode(y="y:Q", text="t:N", x=alt.value(0))
    layers = [line, rule, label]
    if tl["signals"]:
        sdf = pd.DataFrame(tl["signals"])
        sdf["date"] = pd.to_datetime(sdf["date"])
        layers.append(alt.Chart(sdf).mark_rule(color="#64748b", opacity=0.5).encode(
            x="date:T", tooltip=["date:T", "type:N", "quote:N", "outlet:N"]))
    st.altair_chart(alt.layer(*layers).properties(height=260), width="stretch")
    cap("One point per week, the score at the end of the week. Grey lines mark the week a signal was published; "
        "hover for the quote. The dashed line is PREPARE.")

    st.subheader("08:08. Decide")
    md("Prepare (assign an analyst) or dismiss with a reason. Each decision is stored with who, when, the "
       "rulebook version and the score at that moment. Dismissals feed the rule review.")
    c1, c2 = st.columns(2)
    with c1:
        owner = st.selectbox("Owner", TEAM, key="fc_owner")
        if st.button(f"Prepare {pension}", type="primary"):
            ss.fc_decisions.append({"pension": pension, "action": "prepare", "owner": owner, "reason": "",
                                    "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "score": round(w["score"], 2),
                                    "verdict": w["verdict"], "rulebook": f"v{frb.version}"
                                    + ("" if fcd.is_default(frb) else " (your weights)")})
            st.rerun()
    with c2:
        reason = st.text_input("Dismiss, because...", key="fc_reason",
                               placeholder="Liquidity talk is about benefit payments, not a PE sale")
        if st.button(f"Dismiss {pension}"):
            if len(reason.strip()) < 5:
                st.warning("A dismissal needs a reason, so the rule review can use it.")
            else:
                ss.fc_decisions.append({"pension": pension, "action": "dismiss", "owner": "", "reason": reason.strip(),
                                        "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                        "score": round(w["score"], 2), "verdict": w["verdict"],
                                        "rulebook": f"v{frb.version}" + ("" if fcd.is_default(frb) else " (your weights)")})
                st.rerun()
    if ss.fc_decisions:
        table([{**d, "by": "omar"} for d in reversed(ss.fc_decisions)])
        cap("In this demo, decisions live in your browser session only.")

    st.subheader("Did it give early warning? Two real cases")
    md("Real names are used here because both sales were publicly reported. Each case replays the news point in "
       "time: the score on a day uses only what was published by that day. **Neither case gave early warning "
       "under the live rulebook.** So First Call is presented as monitoring, not prediction. n = 2 is an "
       "illustration, not a statistic.")
    clf = st.radio("Classifier that labelled the stored headlines", ["keyword", "claude"], horizontal=True,
                   format_func=lambda c: {"keyword": "Keyword rules (offline)", "claude": "Claude (stored run)"}[c],
                   key="fc_case_clf")
    for case in fcd.cases(clf):
        rep = fcd.case_replay(case, frb)
        with st.container(border=True):
            gt = case["ground_truth"]
            md(f"#### {case['name']}, {case['case'][-4:]}")
            md(f"Ground truth, set by hand: \"{gt['headline']}\" ({gt['outlet']}, {gt['date']}). "
               f"[Source]({fcd.CASE_LINKS[case['case']]})")
            m1, m2, m3 = st.columns(3)
            m1.metric("First PREPARE", rep["first_prepare"] or "never")
            m2.metric("Sale reported", rep["sale_day"])
            m3.metric("Weeks of warning", f"{rep['weeks_of_warning']:g}")
            cdf = pd.DataFrame(rep["rows"])
            cdf["week"] = pd.to_datetime(cdf["week"])
            ch = alt.Chart(cdf).mark_line().encode(x=alt.X("week:T", title=None),
                                                   y=alt.Y("score:Q", scale=alt.Scale(domain=[0, 1])),
                                                   tooltip=["week:T", "score:Q"])
            thr_rule = alt.Chart(pd.DataFrame({"y": [frb.prepare_threshold]})).mark_rule(
                strokeDash=[6, 4], color="#c2410c").encode(y="y:Q")
            sale = alt.Chart(pd.DataFrame({"x": [pd.to_datetime(rep["sale_day"])]})).mark_rule(
                color="#15803d").encode(x="x:T")
            st.altair_chart(alt.layer(ch, thr_rule, sale).properties(height=200), width="stretch")
            cap("Dashed: PREPARE. Green: the day the sale was first reported.")
            table([{"date": s["date"], "type": fcd.SIGNAL_WORDS[s["type"]],
                    "status": "closed, evidence only" if s["completed"] else "", "confidence": s["confidence"],
                    "headline": s["headline"], "outlet": s["outlet"], "link": s["link"]} for s in case["signals"]],
                  column_config={"link": st.column_config.LinkColumn("link", display_text="open")})
    md("Why no warning: one open sale report alone scores 1 - e^-0.85 = 0.57, just under 0.6. In the NYC case that "
       "kept a correct report 12 weeks before the close below the line. With Claude's labels, NYC crosses 12 weeks "
       "early, but only with help from two stories about fossil-fuel exclusions labelled as policy reviews. A buyer "
       "would not count those. A proposal, not applied: make an open sale report with confidence 0.8 or more "
       "PREPARE on its own, as an explicit rule.")

    st.subheader("Play: classify a headline")
    md("Paste a headline. The offline keyword classifier labels it, and the linker finds which pension it names, "
       "if any. Then you see what it alone would add to a score today, under your weights.")
    hl = st.text_input("Headline", value="CalPERS hires Evercore to run secondary sale of private equity stakes",
                       key="fc_headline")
    use_claude = False
    if live_key():
        use_claude = st.checkbox("Also ask Claude (live, counts against your cap)", key="fc_claude")
    if hl.strip():
        c = fcd.classify(hl, frb)
        md(f"**{c['type']}**" + (" (closed sale, evidence only)" if c["completed"] else "")
           + f", confidence {c['confidence']:.2f}. Linked pension: **{c['pension'] or 'none'}** ({c['link_reason']}).")
        cap(f"{c['classifier']}: {c['rationale']}")
        if c["signal_type"] != "none":
            md(f"Alone, today: weight {c['weight']:.1f} x confidence {c['confidence']:.2f} x decay 1.00 = "
               f"{c['value']:.3f}, score {c['score_alone']:.2f}: **{c['verdict_alone']}**.")
        if use_claude:
            ok, why_not = live_available()
            if not ok:
                st.warning(why_not)
            elif st.button("Ask Claude"):
                from fc.classify.base import ClassifierError
                from fc.classify.claude import ClaudeClassifier

                count_live_call()
                try:
                    with st.spinner("Asking Claude..."):
                        res = ClaudeClassifier(client=anthropic_client()).classify(hl.strip()[:400], c["pension"])
                    md(f"Claude: **{fcd.SIGNAL_WORDS[res.signal_type]}**"
                       + (" (closed)" if res.completed else "") + f", confidence {res.confidence:.2f}. {res.rationale}")
                except ClassifierError as e:
                    st.error(esc(f"Claude did not return a valid label: {e}"))
    cap("Try: \"PSERS board to review private equity allocation\", \"Blackstone acquires PE portfolio from NYC "
        "pension\", or \"Pension Bros raises $20 million\".")
    quiz("fc", [
        ("A pension's private equity sits at 14% against a 10% target. Why might it sell?",
         ["To get back to its target allocation", "Because private equity lost money", "Because rules forbid 14%"], 0,
         "Over target is the classic denominator effect: public markets fall, private stakes look larger, and the "
         "pension sells some to rebalance."),
        ("A headline says a sale already closed. Why does it add nothing to the score?",
         ["It is old news", "The stakes are gone: there is nothing left for us to prepare for", "It is not public"], 1,
         "Rulebook v2 shows it as evidence only. Preparing is about a sale still ahead."),
        ("Neither worked case gave early warning. What should the product claim?",
         ["It predicts sales", "It monitors public signals, with evidence", "It is useless"], 1,
         "Two cases cannot prove or disprove prediction. The honest claim is monitoring."),
    ])

# ================================================================== UNDER THE HOOD

with tab_hood:
    st.header("Under the hood")
    st.subheader("The model reads; code decides")
    md("The language model only turns text into fixed fields: offer lines for Quick Look, a signal type for "
       "First Call. Matching, metrics, scores and verdicts are plain, tested functions. That makes every verdict "
       "reproducible and auditable, and it means this demo runs without a model at all.")
    st.graphviz_chart(hood.DIAGRAM, width="stretch")
    md("1. **Source**: an email or a news headline, stored as received (bronze).\n"
       "2. **Read**: the model, or the offline rules, fill a fixed schema. Pydantic checks each row; a bad row is "
       "blocked and logged (silver).\n"
       "3. **Compute**: code matches funds by ratios, computes versioned metrics, point in time (gold).\n"
       "4. **Decide**: the team's rulebook, in plain English YAML, gives the verdict.\n"
       "5. **Show**: every number carries its table, row, metric and version. A validator fails any brief with an "
       "unsourced number.")

    st.subheader("Collection: stream or batch, measured")
    md("Both features built a real-time path and a batch path over the same processing code, then applied one "
       "rule: the simplest path that meets freshness, parity and recovery wins. Batch won both times.")
    for title, path in hood.DECISIONS:
        md(f"**{title}**")
        st.markdown(esc(hood.measured_table(path)))
        cap(f"From {path.relative_to(hood.ROOT)}.")

    st.subheader("Evals")
    md("**Quick Look**")
    table(hood.quicklook_evals())
    md("**Quick Look verdicts against labels**, recomputed now from the snapshot:")
    table(hood.verdict_evals())
    md("**First Call classifier**, 130 labelled real headlines, dev and holdout:")
    table(fcd.classifier_evals())
    cap("The keyword rules were tuned on dev, so holdout is the fair comparison. With 23 positives per split, one "
        "item moves recall by 4 points: treat these as close.")
    pc = hood.parser_comparison()
    if pc:
        md(f"**Claude parser vs offline parser** on 29 offers: the same match accuracy ({pc['match']:.0%}), field "
           f"accuracy ({pc['fields']:.0%}) and verdicts. Claude took {pc['claude_s']:.1f} s per offer against "
           f"{pc['offline_s']:.2f} s, and {pc['tokens']:,} tokens. A real investor would discount the tie: the offline "
           "parser was written against this generator's formats. The next useful eval is hand-written emails in "
           "formats it has never seen.")

    st.subheader("Tracing")
    md("Both features send traces to Langfuse when keys are set: one trace per offer or batch run, model calls "
       "as generations with tokens and cost, and team decisions (keep, override, prepare, dismiss) as feedback "
       "scores. Labelled datasets run as experiments, so a model or prompt change is compared on the same items. "
       "**This demo holds no Langfuse keys and sends nothing.**")

    st.subheader("What a real investor would question")
    md("- **Price range is a heuristic**: a band by strategy and age, times our value over the broker's. A real "
       "desk rolls the NAV forward, adds cash flows since the reference date, and calibrates on its own past bids.\n"
       "- **One asking price** for the whole portfolio is compared with a range built only from funds we own.\n"
       "- **Labels** were written by the builder. A real test needs 2 or 3 analysts labelling on their own.\n"
       "- **First Call** links a headline to a pension by name rules; \"New York pensions\" stays unlinked.\n"
       "- **Live mode**, when on, sends pasted text to Anthropic. A real desk would use an in-region model with no "
       "data retention, under its NDA and data processing agreement.")
    md("Code and docs: [github.com/alexandrecela10/secondaries-radar](https://github.com/alexandrecela10/secondaries-radar). "
       "Rebuild the snapshot with `python demo/build_snapshot.py`.")

st.divider()
cap(FOOTER)
