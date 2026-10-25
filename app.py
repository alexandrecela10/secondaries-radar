"""Secondaries Radar: a guided demo, written to you.

Offer Check runs live with no key: the offline parser reads the offer, and matching, metrics and
verdicts are plain code. Seller Watch replays a committed snapshot of one real news run.
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
FOOTER = ("Synthetic buyer and offers, invented names. Seller Watch uses public headlines and short quotes. "
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
md("For a fund that buys stakes in private equity funds from pensions and insurers who want out early. That trade is "
   "an **LP-led secondary**.")
st.success("**Together: be the first call, then bid at the right price.** Seller Watch spots pensions in the news "
           "in ways that often come before a sale, so you are ready before a broker calls. When an offer arrives, "
           "Offer Check tells you within minutes whether it deserves an NDA, and whether the price is right by our "
           "own fund data, which other bidders do not have. Every number and every news item is sourced.")

with st.sidebar:
    st.markdown("### How to use this demo")
    md("Each tab follows one person through one job, step by step. Every control changes the result live.")
    md("- **Offer Check**: you are Layla, an associate, at 09:00.\n- **Seller Watch**: you are Omar, a partner, "
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

tab_ql, tab_fc, tab_hood = st.tabs(["Offer Check", "Seller Watch", "Under the hood"])

# ================================================================== OFFER CHECK

with tab_ql:
    st.info("**Offer Check: decide on a sale offer in minutes, not a day.** It recognises funds the broker does not "
            "name, checks them against our rules, and flags when the asking price is wrong by what our own fund "
            "reports say since the broker's valuation date. You get a sourced verdict and a note ready to send.")
    st.header("You are Layla, a secondaries associate")
    md("A broker emails a sale offer at 09:00. Your head of team wants a sourced \"look closer or pass\" by 10:00, "
       "so they can decide whether to sign the NDA (the non-disclosure agreement that opens the data room). "
       "Before the NDA, the broker hides fund names. Offer Check works out which funds they are from their numbers.")

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
                         "Offer Check never guesses an offer out of an email that is not one."))
    else:
        offer = ql.load_offer(choice)

    okey = "__pasted__" if choice == PASTE else choice

    if offer is not None:
        # ---- the rulebook (read here, so the verdict below reacts)
        with st.expander("Play: change the rulebook and watch the verdict flip", expanded=False):
            md("These are the team's rules, in plain English. Two questions. **Does it fit us?** LOOK CLOSER needs "
               "the weighted share of rules met to reach the threshold, and no veto to fail. **Is the price right?** "
               "PASS if the seller asks well above what our own data says we would pay. Try: on Hornbeam, raise the "
               "price veto to 15 points. Or raise the known-managers bar to 90%.")
            base_name = st.radio("Start from", ["v3", "v2", "v1"], horizontal=True, key="ql_base",
                                 format_func=lambda v: {"v3": "Rulebook v3 (live today)",
                                                        "v2": "v2 (price was one rule among others)",
                                                        "v1": "v1 (the PRD's first version)"}[v])
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
            gap = None
            if base.price is not None:
                gap = st.slider("Price veto: PASS if the seller asks more than this many points above what we would "
                                "pay", 0, 30, int(round(base.price.veto_gap * 100)), 1, key=f"ql_gap_{base_name}") / 100
            rb = ql.custom_rulebook(base, thr, edits, gap)
            if rb.sha == "edited-in-demo":
                st.info(esc(f"You are using your own edit of rulebook {base_name}. Scroll to \"Why rule changes are "
                            "tested\" to see how it does on labelled offers it was not tuned on."))
            with st.popover("Read the rulebook as sentences"):
                for s in ql.rulebook_sentences(rb):
                    md(s)

        picks = ss.ql_picks.setdefault(okey, {})
        r0 = ql.run(offer, rb)            # what code says on its own
        for g in ql.open_lines(r0):       # your pick for an unidentified fund, from the widget below
            choice_key = f"ql_pick_{okey}_{g['line_no']}"
            if ss.get(choice_key) not in (None, "not sure"):
                picks[g["line_no"]] = ss[choice_key]
            elif choice_key in ss:
                picks.pop(g["line_no"], None)
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
        verdict_box(r.verdict)
        if r.picks:
            cap("Includes your pick for " + ", ".join(f"Fund {n}" for n in sorted(r.picks)) + ".")
        pc = ql.price_check(r)
        fits = r.verdict.score is not None and r.verdict.score >= r.verdict.threshold and not (
            r.verdict.veto_rule and r.verdict.veto_rule != ql.rulebook.PRICE_VETO)
        price_words = {"cheap": "Looks right by our own data", "expensive": "Looks high by our own data",
                       "no view": "No view: we own none of these funds"}[pc["view"]]
        c1, c2, c3 = st.columns(3)
        c1.metric("Does it fit us?", "Yes" if fits else "No")
        c2.metric("Is the price right?", "Too high" if pc["vetoed"] else price_words.split(" by")[0].split(":")[0],
                  help=price_words)
        c3.metric("How sure?", r.verdict.band.capitalize(),
                  help="High needs most of the offer identified and a clear margin on fit.")
        with st.expander("How a buyer thinks about this"):
            md("A buyer sees dozens of offers a year and bids on a few. The first read is triage: is it worth "
               "signing an NDA and spending a week on the data room? The biggest edge is **funds you already own**. "
               "The broker prices every fund from its value on one past date. As an investor in the fund, you have "
               "heard what happened since: companies sold, write-downs. Other bidders have not.")

        # ---- 09:05
        st.subheader("09:05. Check why it fits, or not")
        md("Each rule, its number and where the number comes from. Code computes every number; the model never does.")
        icons = {"met": "Met", "not met": "Not met", "not checked": "Not checked"}
        table([{"": icons[x["status"]], "rule": x["rule"] + (" (veto)" if x["veto"] else ""), "weight": x["weight"],
                "number": x["number"], "test": x["test"], "source": x["source"]} for x in ql.reasons(r)])
        for g in ql.open_lines(r0):
            line = next(l for l in offer.lines if l.line_no == g["line_no"])
            share = g["value_usd"] / (r0.values["total_value_usd"] or 1)
            cands = ql.candidates_for(r0, g["line_no"])
            lab = {c["fund_id"]: f"{c['name']} ({'we own it' if c['owned'] else 'we track it'})" for c in cands}
            md(f"**{g['label']} ({share:.0%} of value): not identified.** \"{line.description}, {line.vintage}\" fits "
               f"{len(cands)} funds we know. Know which one it is?")
            opts = ["not sure"] + [c["fund_id"] for c in cands]
            cur = picks.get(g["line_no"], "not sure")
            st.radio(esc(f"{g['label']} is"), opts, index=opts.index(cur) if cur in opts else 0, horizontal=True,
                     format_func=lambda x: lab.get(x, x), key=f"ql_pick_{okey}_{g['line_no']}",
                     label_visibility="collapsed")
        if r.picks:
            cap("Your pick is recorded next to what the code said. A fund we own gets a price; a fund we only "
                "track counts as a manager we know.")
        nc = [x for x in ql.reasons(r) if x["status"] == "not checked"]
        if nc:
            cap("Not checked: " + "; ".join(f"{x['rule']} ({x['note']})" for x in nc) +
                ". A rule with no number is left out of the score and lowers confidence by one level.")
        with st.expander("Every line and how code identified it"):
            table(ql.lines_table(r0))
            cap("Tiers: a fund we own (ratios match our report), a manager we know (other fund), unknown. "
                "Nothing is matched silently: each line carries its method.")

        # ---- 09:08
        st.subheader("09:08. Check the price against what we know")
        if pc["view"] == "no view":
            md("We own none of these funds, so we know nothing the other bidders don't. No price view.")
        else:
            md(f"We own **{pc['n_owned']}** of these funds (**{pc['share_owned']:.0%}** of the value). The broker "
               f"prices them at their value on **{offer.parsed.reference_date:%d %B}**. Since then, their managers "
               "have told us things other bidders have not heard.")
            if pc["adjustments"]:
                table(pc["adjustments"])
                more = pc["change"] >= 0
                md(f"So by our own data, these funds are worth **${pc['worth']:,.1f}M**, against "
                   f"${pc['broker_value']:,.1f}M at the broker's date: **{abs(pc['change']):.1%} "
                   f"{'more' if more else 'less'}**.")
            else:
                md("Nothing material since then, so our view matches the broker's value.")
            ask = f"{pc['ask_text']} (about {pc['ask_mid']:.0%})" if pc["ask_mid"] is not None else "not given"
            md(f"We would pay **{pc['low']:.0%} to {pc['high']:.0%}** of the broker's value. The seller asks **{ask}**.")
            if pc["vetoed"]:
                st.error(esc(f"Too expensive: the ask is {pc['gap'] * 100:.0f} points above the most we would pay. "
                             "Other bidders working from the old value would overpay. We let them."))
            elif pc["view"] == "cheap":
                st.success("The price looks right: the ask is inside what we would pay.")
            else:
                st.warning(esc(f"The price looks high: the ask is {pc['gap'] * 100:.0f} points above the most we "
                               "would pay. Worth a look, but plan to bid below the ask."))
            cap(f"Covers the funds we own only ({pc['share_owned']:.0%} of value). A first view, not a bid: a real bid "
                "projects the cash flows after the NDA. Cash paid in or out since the broker's date is settled at "
                "closing, so it is not counted here.")
        with st.expander("A simplification a real investor would question"):
            md("Other investors in the same funds hear the same news, so the edge is real but not unique. And the "
               "range itself is a rule of thumb by fund type and age; a real desk calibrates it on its own past bids.")
        if offer.code == "Cedar":
            cap("Then try Hornbeam: it looks fine at the June values, but two write-downs since make it too expensive.")

        # ---- 09:12
        st.subheader("09:12. Check how a fund was identified")
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
        md("Ready to paste. Every number traces back to its source.")
        st.code(ql.memo(r, dec), language=None, wrap_lines=True)
        cap("Use the copy icon at the top right of the box.")

        # ---- rule changes
        with st.expander("Why rule changes are tested: three rule versions compared"):
            md("Each version is tested on labelled offers: the **tune** offers shaped the rules, and the **holdout** "
               "offers were kept aside. v2 lowered one bar to fix Willow, and did worse on the holdout. v3 took price "
               "out of the fit score and judged it with what our own fund reports say.")
            rows = []
            books = [("Rulebook v1", ql.load_rulebook("v1")), ("Rulebook v2", ql.load_rulebook("v2")),
                     ("Rulebook v3", ql.load_rulebook("v3"))]
            if rb.sha == "edited-in-demo":
                books.append(("Your rulebook", rb))
            results = {}
            for name, b in books:
                a = ql.agreement(b)
                results[name] = a
                rows.append({"rulebook": name, "tune offers agreed": f"{a['tune']['kept']} of {a['tune']['total']}",
                             "holdout offers agreed": f"{a['holdout']['kept']} of {a['holdout']['total']}",
                             "good offers marked PASS (tune)": a["tune"]["good_passed"],
                             "good offers marked PASS (holdout)": a["holdout"]["good_passed"]})
            table(rows)
            md("v2 fixed Willow and lost its grip on old, tail-end funds: **that is overfitting.** v3 agrees with all 14 "
               "holdout offers, but marks PASS on two tune offers labelled good. **Myrtle**: our reports show its funds "
               "down 6% since June, and at par the seller asks 17 points over what we would pay; the label was written "
               "before the price check existed. **Poplar**: v2 said LOOK CLOSER only because the price rule propped up "
               "its score; we can identify managers behind just 22% of its value. Both are open questions for the team, "
               "not tuned away.")
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
            ("The broker values Hornbeam's funds at June. Our reports show two write-downs since. Who overpays?",
             ["Every bidder equally", "Bidders working from the June value", "Nobody, the auction corrects it"], 1,
             "Bidders who only have the June value price in companies that are already worth less. The winner of "
             "an auction is often the one most wrong about value. Our data tells us when to step back."),
            ("Rulebook v2 agrees with 14 of 14 tune offers and 10 of 14 holdout offers. What do you trust?",
             ["The tune result", "The holdout result", "Their average"], 1,
             "The tune offers shaped the rule. Only the holdout says how it does on offers it has not seen."),
        ])

# ================================================================== SELLER WATCH

with tab_fc:
    st.info("**Seller Watch: know who may sell before the broker calls.** Every Monday, a short list of US public "
            "pensions in the news in ways that often come before a sale, each with the article to read. It is a "
            "monitor, not a forecast: it shows the evidence and you decide.")
    st.header("You are Omar, a partner planning the week")
    md("You look after our relationships with large US public pensions. On Monday at 08:00 you have 10 minutes. "
       "You want to know which pensions are in the news in ways that often come before a sale of fund stakes, so "
       "the team is ready before a broker calls. **\"Worth preparing for\" and \"keep an eye on\" describe what we "
       "do. They never say a pension plans to sell.**")
    cap(fcd.DISCLAIMER)

    with st.expander("Play: change how much each kind of news counts", expanded=False):
        b = fcd.base_rulebook()
        md("Each news item adds to a pension's total. Some kinds count more than others: move the sliders. Older news "
           f"counts less: an item counts half as much after {b.half_life_days:.0f} days, and not at all after a year. "
           "When the total reaches the line, the pension moves to \"worth preparing for\". A sale that already "
           "happened is shown but adds nothing, because there is nothing left to prepare for.")
        thr = st.slider("Where the line sits (higher means fewer pensions to prepare for)", 0.1, 0.9,
                        float(b.prepare_threshold), 0.05, key="fc_thr")
        cols = st.columns(len(b.weights))
        weights = {k: col.slider(fcd.SIGNAL_WORDS[k], 0.0, 2.0, float(v), 0.1, key=f"fc_w_{k}")
                   for col, (k, v) in zip(cols, b.weights.items())}
        frb = fcd.custom_rulebook(weights, thr)
        if not fcd.is_default(frb):
            st.info("You are using your own settings. Everything below, including the two past cases, uses them.")
        cap("Try: move the line down to 0.35. New York City Retirement Systems becomes worth preparing for on one "
            "sale report. Then look at its 2025 case below: does it now warn early, and on what evidence?")
        cap(f"Exact rule: total = 1 - exp(-S), where S adds weight x certainty x 0.5^(age / {b.half_life_days:.0f} "
            f"days) for each item in the last {b.window_days} days. The line is at {b.prepare_threshold:g} by default "
            f"(rules version {b.version}).")

    snap = fcd.snapshot()
    st.subheader("Overnight. Collect")
    md(f"Overnight, a batch job read public news (Google News and GDELT). It matched each headline to one of 61 US "
       f"public pensions, or to none, sorted it by kind and updated each pension's total. What you see is one real "
       f"run, as of **{fcd.nice_date(fcd.as_of().date())}**. We keep only the headline, outlet, date and a short "
       "quote, never the article.")
    cap(f"Headlines were sorted by: {', '.join(snap['classifiers'])}.")

    st.subheader("08:00. Read the list")
    rows = fcd.weekly(frb)
    n_prep = sum(r["verdict"] == "PREPARE" for r in rows)
    notes = {}
    for d in ss.fc_decisions:
        notes[d["pension"]] = (f"prepare: {d['owner']}" if d["action"] == "prepare" else f"dismissed: {d['reason'][:30]}")
    md(f"**{n_prep} worth preparing for, {len(rows) - n_prep} to keep an eye on.**" if n_prep
       else f"**No pension is worth preparing for this week. {len(rows)} to keep an eye on.**")
    if rows:
        md("Top of the list. " + fcd.verdict_sentence(fcd.why(rows[0]["pension"], frb)))
    list_rows = []
    for r in rows:
        src = fcd.source(r["top"])
        row = {"our call": fcd.VERDICT_WORDS[r["verdict"]], "pension": r["pension"],
               "in short": f"{fcd.count_words(r['signals'])}, " + ("" if r["signals"] == 1 else "the strongest ")
               + fcd.ago(r["age_days"]),
               "how close to the line": fcd.distance(r["score"], frb.prepare_threshold).split(" the line")[0]
               .replace(" way to", " way").replace("close to", "close").replace("far from", "far"),
               "strongest news": src["headline"], "outlet": src["outlet"], "date": src["date"]}
        row["link"] = src["link"]
        row["your decision"] = notes.get(r["pension"], "")
        list_rows.append(row)
    table(list_rows, column_config={"link": st.column_config.LinkColumn("link", display_text="open")})
    with st.expander("Exact numbers"):
        table([{"pension": r["pension"], "verdict": r["verdict"], "total": r["score"],
                "line": frb.prepare_threshold, "items counted": r["signals"]} for r in rows])
    closed = fcd.closed_sales()
    if closed:
        md("**Sales already completed in the last 90 days.** Shown for context. They add nothing to the list: once a "
           "sale is done, there is nothing left to prepare for.")
        for s in closed:
            md(f"- {s['pension'] + ': ' if s['pension'] else ''}{fcd.source_md(fcd.source(s))}")

    st.subheader("08:03. Check the evidence")
    pension = st.selectbox("Open a pension", [r["pension"] for r in rows], key="fc_pension")
    w = fcd.why(pension, frb)
    md(fcd.verdict_sentence(w))
    for p in w["parts"]:
        src = fcd.source(p)
        with st.container(border=True):
            md(f"**{p['type']}.** {fcd.source_md(src)}")
            if src["quote"] and src["quote"] != src["headline"]:
                md(f"> {src['quote']}")
            cap(f"From {fcd.ago(p['age_days'])}, so it {fcd.counts_words(p['decay'])}."
                + (f" Seen in {p['copies']} outlets, counted once." if p["copies"] > 1 else ""))
    for s in w["closed"]:
        cap("Sale already completed, shown for context only: " + fcd.source_md(fcd.source(s)))
    if w["outside"]:
        cap(f"{fcd.count_words(w['outside'], 'older news item')} from more than a year ago no longer count.")
    with st.expander("Exact numbers"):
        md(f"Total {w['score']:.2f} (S = {w['S']:.3f}, total = 1 - exp(-S)). Line at {w['threshold']:g}. An item "
           f"counts half after {w['half_life']:.0f} days and is dropped after {w['window']} days.")
        table([{"kind": p["type"], "date": p["date"], "weight": p["weight"], "certainty": p["confidence"],
                "age (days)": p["age_days"], "fade": p["decay"], "adds": p["value"]} for p in w["parts"]])
    with st.expander("How a buyer thinks about this"):
        md("A pension that holds more private equity than its plan allows may sell some to get back in line. One that "
           "needs cash may sell too. A new head of investments often reviews the whole portfolio. None of these means "
           "a sale. Several of them, recent, are a reason to call.\n\n"
           "Worked example: a pension has USD 100 billion, plans 10% in private equity and holds 14% after a fall in "
           "stocks. To get back to 10% it must shed about USD 4 billion of private equity. Most pensions do that by "
           "slowing new commitments. Some sell fund stakes. That is the moment a buyer wants to be the first call.\n\n"
           "A real investor would push back on one thing: a news monitor sees only what is reported. Many sales are "
           "run quietly by an adviser and surface only when they close.")

    with st.expander("08:06. See how it built up"):
        tl = fcd.timeline(pension, frb)
        df = pd.DataFrame(tl["rows"])
        df["week"] = pd.to_datetime(df["week"])
        line = alt.Chart(df).mark_line(point=True).encode(
            x=alt.X("week:T", title=None),
            y=alt.Y("score:Q", scale=alt.Scale(domain=[0, 1]), title="how strong the signs are"),
            tooltip=[alt.Tooltip("week:T", title="week"), alt.Tooltip("score:Q", title="total", format=".2f")])
        rule = alt.Chart(pd.DataFrame({"y": [tl["threshold"]]})).mark_rule(strokeDash=[6, 4], color="#c2410c").encode(y="y:Q")
        label = alt.Chart(pd.DataFrame({"y": [tl["threshold"]], "t": ["start preparing"]})).mark_text(
            align="left", dx=4, dy=-6, color="#c2410c").encode(y="y:Q", text="t:N", x=alt.value(0))
        layers = [line, rule, label]
        if tl["signals"]:
            sdf = pd.DataFrame([{"date": m["date"], "kind": m["type"] + (" (already completed)" if m["closed"] else ""),
                                 "headline": m["headline"], "outlet": m["outlet"]} for m in tl["signals"]])
            sdf["date"] = pd.to_datetime(sdf["date"])
            layers.append(alt.Chart(sdf).mark_rule(color="#64748b", opacity=0.5).encode(
                x="date:T", tooltip=["date:T", "kind:N", "headline:N", "outlet:N"]))
        st.altair_chart(alt.layer(*layers).properties(height=260), width="stretch")
        cap("Each point is the pension's total at the end of a week. The dashed line is where we start preparing. Grey "
            "lines mark news items: hover to read them.")

    st.subheader("08:08. Decide")
    md("**Prepare** means you assign an analyst to get ready. **Dismiss** means you think it is noise: say why, so we "
       "can improve the rules. Each decision is saved with who, when, the rules version and the total at that moment.")
    c1, c2 = st.columns(2)
    _rules = f"v{frb.version}" + ("" if fcd.is_default(frb) else " (your settings)")
    with c1:
        owner = st.selectbox("Analyst", TEAM, key="fc_owner")
        if st.button(f"Prepare for {pension}", type="primary"):
            ss.fc_decisions.append({"pension": pension, "action": "prepare", "owner": owner, "reason": "",
                                    "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "score": round(w["score"], 2),
                                    "verdict": w["verdict"], "rulebook": _rules})
            st.rerun()
    with c2:
        reason = st.text_input("Dismiss, because...", key="fc_reason",
                               placeholder="The cash talk is about paying pensions, not a sale")
        if st.button(f"Dismiss {pension}"):
            if len(reason.strip()) < 5:
                st.warning("Please give a reason. It is how the rules get better.")
            else:
                ss.fc_decisions.append({"pension": pension, "action": "dismiss", "owner": "", "reason": reason.strip(),
                                        "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                        "score": round(w["score"], 2), "verdict": w["verdict"], "rulebook": _rules})
                st.rerun()
    if ss.fc_decisions:
        table([{"pension": d["pension"], "decision": d["action"], "analyst": d["owner"], "reason": d["reason"],
                "when": d["at"], "by": "omar", "our call then": fcd.VERDICT_WORDS[d["verdict"]], "total then": d["score"],
                "rules": d["rulebook"]} for d in reversed(ss.fc_decisions)])
        cap("In this demo, decisions stay in your browser session.")

    with st.expander("Did it warn early? Two real past sales"):
        md("**No. In both cases the tool flagged the pension only once the sale was reported.** So treat Seller Watch as a "
           "news monitor that shows its evidence, not a forecast. Two cases are an example, not proof. "
           "Each replay uses only the news published by that day.")
        clf = st.radio("Who sorted the stored headlines", ["keyword", "claude"], horizontal=True,
                       format_func=lambda c: {"keyword": "Keyword rules (offline)", "claude": "Claude (saved run)"}[c],
                       key="fc_case_clf")
        for case in fcd.cases(clf):
            rep = fcd.case_replay(case, frb)
            with st.container(border=True):
                gt = case["ground_truth"]
                md(f"#### {case['name']}, {case['case'][-4:]}")
                md(f"The sale was first reported on {fcd.nice_date(gt['date'])}: "
                   f"[{gt['headline']}]({fcd.CASE_LINKS[case['case']]}), {gt['outlet']}.")
                m1, m2, m3 = st.columns(3)
                m1.metric("First flagged as worth preparing for",
                          fcd.nice_date(rep["first_prepare"]) if rep["first_prepare"] else "never")
                m2.metric("Sale first reported", fcd.nice_date(rep["sale_day"]))
                m3.metric("Weeks of early warning", f"{rep['weeks_of_warning']:g}")
                cdf = pd.DataFrame(rep["rows"])
                cdf["week"] = pd.to_datetime(cdf["week"])
                ch = alt.Chart(cdf).mark_line().encode(x=alt.X("week:T", title=None),
                                                       y=alt.Y("score:Q", scale=alt.Scale(domain=[0, 1]),
                                                               title="how strong the signs are"),
                                                       tooltip=["week:T", alt.Tooltip("score:Q", format=".2f")])
                thr_rule = alt.Chart(pd.DataFrame({"y": [frb.prepare_threshold]})).mark_rule(
                    strokeDash=[6, 4], color="#c2410c").encode(y="y:Q")
                sale = alt.Chart(pd.DataFrame({"x": [pd.to_datetime(rep["sale_day"])]})).mark_rule(
                    color="#15803d").encode(x="x:T")
                st.altair_chart(alt.layer(ch, thr_rule, sale).properties(height=200), width="stretch")
                cap("Dashed: where we start preparing. Green: the day the sale was first reported.")
                table([{"date": fcd.nice_date(s["date"]), "kind": fcd.SIGNAL_WORDS[s["type"]],
                        "note": "already completed, context only" if s["completed"] else "",
                        "headline": s["headline"], "outlet": s["outlet"], "link": s["link"]} for s in case["signals"]],
                      column_config={"link": st.column_config.LinkColumn("link", display_text="open")})
        md("Why no early warning: one report of a sale, on its own, lands just under the line. In the New York City case, "
           "a correct report 12 weeks before the sale stayed just below it. With Claude's labels, New York City crosses "
           "the line 12 weeks early, but only because two stories about dropping fossil-fuel investments were counted as "
           "private equity reviews. A buyer would not count those. Proposed fix, not applied yet: let one confident sale "
           "report be enough on its own.")
        cap("Exact numbers. Example: one fresh sale report with certainty 0.85 gives S = 1.0 x 0.85 = 0.85 and a "
            "total of 1 - e^-0.85 = 0.57, under the line at 0.6. The proposed rule: a sale report with certainty 0.8 "
            "or more puts the pension past the line on its own.")

    with st.expander("Play: test a headline"):
        md("Paste a headline. The offline rules say what kind of news it is and which pension it names, if any. Then you "
           "see what it alone would mean today, with your settings.")
        hl = st.text_input("Headline", value="CalPERS hires Evercore to run secondary sale of private equity stakes",
                           key="fc_headline")
        use_claude = False
        if live_key():
            use_claude = st.checkbox("Also ask Claude (live, counts against your limit)", key="fc_claude")
        if hl.strip():
            c = fcd.classify(hl, frb)
            md(f"**{c['type']}**" + (" (a sale that already happened: context only)" if c["completed"] else "")
               + f". About: **{c['pension'] or 'no pension we follow'}**. Certainty: {fcd.certainty(c['confidence'])}.")
            if c["signal_type"] == "none" or c["weight"] == 0:
                md("On its own, it adds nothing.")
            elif c["verdict_alone"] == "PREPARE":
                md("On its own, today, it would make that pension **worth preparing for**.")
            else:
                md("On its own, today, it would put that pension on the **keep an eye on** list. More news would be needed "
                   "to reach the line.")
            cap(f"Exact numbers. {c['classifier']}: {c['rationale']} Pension match: {c['link_reason']}.")
            if c["signal_type"] != "none":
                cap(f"Weight {c['weight']:.1f} x certainty {c['confidence']:.2f} x fade 1.00 = {c['value']:.3f}, "
                    f"total {c['score_alone']:.2f}, line {frb.prepare_threshold:g}: {c['verdict_alone']}.")
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
                        md(f"Claude says: **{fcd.SIGNAL_WORDS[res.signal_type]}**"
                           + (" (already completed)" if res.completed else "")
                           + f". Certainty: {fcd.certainty(res.confidence)}. {res.rationale}")
                    except ClassifierError as e:
                        st.error(esc(f"Claude did not return a valid label: {e}"))
        cap("Try: \"PSERS board to review private equity allocation\", \"Blackstone acquires PE portfolio from NYC "
            "pension\", or \"Pension Bros raises $20 million\".")
    quiz("fc", [
        ("A pension's private equity sits at 14% when its plan says 10%. Why might it sell?",
         ["To get back to its plan", "Because private equity lost money", "Because rules forbid 14%"], 0,
         "This is the denominator effect: stocks fall, so private stakes look larger as a share, and the pension "
         "sells some to get back in line."),
        ("A headline says a sale already happened. Why does it add nothing?",
         ["It is old news", "The stakes are gone: there is nothing left for us to prepare for", "It is not public"], 1,
         "It stays on screen as context. Preparing is about a sale still ahead."),
        ("Neither past case gave early warning. What should the product claim?",
         ["It predicts sales", "It monitors public news and shows the evidence", "It is useless"], 1,
         "Two cases cannot prove or disprove a forecast. The honest claim is monitoring."),
    ])

# ================================================================== UNDER THE HOOD

with tab_hood:
    st.header("Under the hood")
    with st.expander("New to this? The choices behind it, in plain English"):
        md("Building a tool like this means making a handful of choices about data and AI. Each one has a cheaper or "
           "simpler option and a more powerful one. Here is what we picked, why, and what we gave up.")
        table([
            {"choice": "Collect news and emails live, or on a schedule?", "we picked": "On a schedule (batch)",
             "why": "The sources only update every 15 minutes, and decisions take a day or weeks",
             "what we gave up": "Up to 15 minutes of freshness"},
            {"choice": "Let the AI decide, or only read?", "we picked": "The AI reads; code decides",
             "why": "Same input, same answer, every time. Every verdict can be checked",
             "what we gave up": "Code misses cases its rules were not written for"},
            {"choice": "Written rules, or a trained model?", "we picked": "Rules the team writes and edits",
             "why": "About 30 offers with known answers is far too few to train a model",
             "what we gave up": "People must keep the rules up to date"},
            {"choice": "Use any data, or only what we knew that day?", "we picked": "Only what we knew that day",
             "why": "No hindsight in verdicts or in tests", "what we gave up": "Two dates to keep on every fact"},
            {"choice": "Trust it, or test it?", "we picked": "Test it on answers we hold back",
             "why": "Shows how it does on cases it has never seen", "what we gave up": "Someone has to write the answers"},
            {"choice": "A database server, or a file?", "we picked": "One database file (DuckDB)",
             "why": "Nothing to install or run for a demo", "what we gave up": "One writer at a time"},
        ])

        md("#### 1. Live stream or batch: when do we collect?")
        md("A **live stream** is like a phone line that stays open: each news item is handled the second it arrives. "
           "A **batch** is like the post: items pile up, and a job collects and handles them on a schedule, then "
           "stops. A stream is fresher. A batch has fewer parts that can break, and costs nothing between runs.")
        st.graphviz_chart(hood.STREAM_VS_BATCH, width="stretch")
        md("We built both, over the same processing code, and measured them. Both gave identical results and "
           "survived a crash with nothing lost or counted twice. **Batch won both times.** The news sources only "
           "publish every 15 minutes, so a stream cannot be fresher than that, and a pension's sale plays out over "
           "weeks. For news, the stream needed 3 moving parts running all month (2,160 hours); batch needs 1, "
           "running about 11 hours a month. A stream earns its cost where minutes matter: trading, fraud, cash "
           "positions.")

        md("#### 2. The AI reads; code decides")
        md("A language model is very good at reading messy text: a broker's email, a headline. It is less good at "
           "giving the same answer twice, or showing exactly why. So the AI has one job, filling in a fixed form "
           "(which funds are in the offer, what kind of news this is). Plain code then does the maths and applies "
           "the team's rules. If the AI fills the form wrongly, a check blocks the row instead of passing it on.")
        md("The gain: every verdict can be rerun and traced, and the tool still works with no AI at all (this demo "
           "runs on offline rules by default). The cost: the rules only know what they were written for, so a new "
           "kind of offer needs a person to add a rule.")

        md("#### 3. Raw, checked, computed: keeping data in layers")
        md("Data moves through three layers, like a kitchen: raw ingredients as delivered, then washed and cut, then "
           "the plated dish. Here that is the email as received, then rows that passed the checks, then the "
           "computed numbers (the diagram just below this box). Because the raw copy is kept, any result can be "
           "rebuilt from scratch after a fix, and every number in a memo can point back to the row it came from.")

        md("#### 4. Point in time: no hindsight")
        md("A verdict may only use what we knew on the day the offer arrived. This matters most for the price check. "
           "Take Hornbeam:")
        st.graphviz_chart(hood.POINT_IN_TIME, width="stretch")
        md("The broker prices from the 30 June value. We were told of the write-down on 10 September, before the "
           "offer arrived, so it counts, and it is why we would pass. Had we learnt of it in November, it would not "
           "count, even in a test run later. Tests that peek at the future look better than the tool will ever be.")

        md("#### 5. Evals: an exam with the answers held back")
        md("An eval is an exam: we run the tool on cases where we already know the right answer and count how often "
           "it agrees. The trick is to split the cases. The **tune** set is used to write the rules. The "
           "**holdout** set is locked away and only used to mark the final result.")
        st.graphviz_chart(hood.EVAL_SPLIT, width="stretch")
        ev = []
        for v in ("v1", "v2", "v3"):
            a = ql.agreement(ql.load_rulebook(v))
            for split, label in (("tune", "tune set (seen)"), ("holdout", "holdout set (unseen)")):
                ev.append({"rules": f"version {v[1]}", "set": label,
                           "agreed": round(100 * a[split]["kept"] / a[split]["total"])})
        bars = alt.Chart(pd.DataFrame(ev)).mark_bar().encode(
            x=alt.X("agreed:Q", title="% of offers where the tool agreed with the answer", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("set:N", title=None), row=alt.Row("rules:N", title=None, header=alt.Header(labelAngle=0, labelAlign="left")),
            color=alt.Color("set:N", legend=None, scale=alt.Scale(range=["#2563eb", "#94a3b8"])),
            tooltip=["rules:N", "set:N", "agreed:Q"]).properties(height=44, width=420)
        st.altair_chart(bars)
        md("Version 2 scored better on the cases it was written from and worse on the locked ones. That is "
           "**overfitting**: the rules learnt the examples, not the job. Without the holdout we would have shipped "
           "the worse version. Two more words you will see: **precision** is how often a flag is right; **recall** "
           "is how many of the real cases get flagged.")

        md("#### 6. Tracing: a flight recorder for every run")
        md("Each run records what the AI was given, what it returned, how long each step took and what it cost. "
           "When a verdict looks wrong, you replay the record instead of guessing. We use Langfuse for this. The "
           "trade-off: records include the text the AI read, so in a real deployment they must stay where the "
           "data is allowed to be. Examples are in the Tracing section below.")

        md("#### What a real deployment would change")
        md("- **Where the AI runs**: offers arrive under a confidentiality agreement, so the model would be hosted in "
           "our region and keep no data.\n"
           "- **Storage**: a shared database server once several people use it at once.\n"
           "- **Answers for the exams**: written by 2 or 3 analysts working separately, not by the builder or an AI.")
    st.subheader("How it works")
    md("The AI turns text into a fixed form. Plain code does the rest: matches funds, computes the numbers, applies "
       "the team's rules and checks that every number has a source. So every verdict can be rerun and checked.")
    st.graphviz_chart(hood.DIAGRAM, width="stretch")

    st.subheader("Tracing: open a real run")
    md("Every run leaves a record in Langfuse: what went in, what came out, how long each step took, what it cost "
       "and the checks run on it. When a verdict looks wrong, you open its record instead of guessing.")
    lf = hood.langfuse_examples()
    runs = {"quicklook": "Offer Check: one offer email (Project Cedar)",
            "firstcall": "Seller Watch: one overnight news run"}
    if lf.get("traces"):
        feature = st.radio("Run", list(runs), format_func=runs.get, horizontal=True, key="hood_run",
                           label_visibility="collapsed")
        t = hood.trace(feature)
        sm = hood.trace_summary(t)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Time", f"{sm['seconds']:.1f} s")
        m2.metric("Cost", f"USD {sm['cost']:.3f}")
        m3.metric("AI calls", sm["model_calls"])
        m4.metric("Tokens", f"{sm['tokens']:,}")
        rest = sm["seconds"] - sm["model_seconds"]
        cap(f"Run on {sm['when']}. The AI ({sm['model']}) took {sm['model_seconds']:.1f} of the {sm['seconds']:.1f} "
            "seconds; the rest is " + (f"mostly {sm['fetches']} news searches." if sm["fetches"] else "plain code.")
            + (" This run used rules version 2; the app now uses version 3." if feature == "quicklook" else ""))
        c_in, c_out = st.columns(2)
        with c_in:
            md("**What went in**")
            st.code(t["input"], language=None, wrap_lines=True)
        with c_out:
            md("**What came out**")
            st.code(t["output"], language=None, wrap_lines=True)
        checks = hood.score_words(t)
        if checks:
            md("**Checks saved on this run**\n" + "\n".join(f"- {esc(c)}" for c in checks))

        md("**Step by step.** One bar per step, in order. Orange bars are AI calls.")
        srows = hood.trace_steps(t)
        gdf = pd.DataFrame(srows)
        gantt = alt.Chart(gdf).mark_bar(cornerRadius=2).encode(
            y=alt.Y("order:O", axis=alt.Axis(labelExpr="''", ticks=False, title=None)),
            x=alt.X("start_s:Q", title="seconds from the start"), x2="end_s:Q",
            color=alt.Color("kind:N", title="kind of step",
                            scale=alt.Scale(domain=["span", "generation", "retriever", "guardrail"],
                                            range=["#64748b", "#c2410c", "#2563eb", "#15803d"])),
            tooltip=["step:N", "what it does:N", "took:N", "tokens:N", "cost (USD):N", "result:N"])
        labels = alt.Chart(gdf).mark_text(align="left", dx=4, fontSize=11).encode(
            y=alt.Y("order:O"), x="end_s:Q", text="name:N")
        st.altair_chart(alt.layer(gantt, labels).properties(height=max(140, 16 * len(srows))), width="stretch")
        gens = [i for i, s in enumerate(t["steps"]) if s["type"] == "GENERATION"]
        i = st.selectbox("Open one step", range(len(t["steps"])), index=gens[0] if gens else 0,
                         format_func=lambda k: hood.step_label(t["steps"][k]), key=f"hood_step_{feature}")
        step = t["steps"][i]
        facts = [f"took {step['duration_ms'] / 1000:.2f} s"]
        if step.get("model"):
            facts.append(f"AI model {step['model']}")
        if step.get("tokens_in"):
            facts.append(f"{step['tokens_in']:,} tokens in, {step['tokens_out']:,} out")
        if step.get("cost_usd"):
            facts.append(f"USD {step['cost_usd']:.4f}")
        cap(esc(f"{hood.STEP_WORDS.get(step['name'], step['name']).capitalize()}: " + ", ".join(facts) + "."))
        s_in, s_out = st.columns(2)
        with s_in:
            md("**In**")
            st.code(step.get("input") or "(nothing)", language=None, wrap_lines=True)
        with s_out:
            md("**Out**")
            st.code(step.get("output") or "(nothing)", language=None, wrap_lines=True)
        cap(f"Exported from the Langfuse API on {lf['exported_at'][:10]}. Long text is cut short. This demo itself "
            "sends nothing to Langfuse.")
    else:
        md("No exported runs in this copy. Run `python demo/export_langfuse.py` with Langfuse keys to add two.")

    st.subheader("Evals: is it right?")
    md("An eval runs the tool on cases where we already know the answer and counts how often it agrees. Each result "
       "below is on cases kept aside, never used to tune anything.")
    md("\n".join(f"- {line}" for line in hood.headline_results()))
    cap("Simplification a real investor would flag: the answers were written by the builder or an AI agent. A real "
        "test needs 2 or 3 analysts labelling on their own.")
    with st.expander("All the eval results"):
        md("**Seller Watch: Claude against keyword rules, on headlines kept aside**")
        for line in hood.classifier_in_words():
            md(f"- {line}")
        ex = hood.experiment_rows()
        if ex["firstcall"]:
            md("Every run of the same comparison in Langfuse. Precision: of the headlines flagged, the share that were "
               "right. Recall: of the real signs, the share that were caught.")
            table(ex["firstcall"])
        md("**Offer Check tests**")
        table(hood.quicklook_evals())
        md("**Offer Check verdicts, by rules version**")
        table(hood.verdict_evals())
        pc = hood.parser_comparison()
        if pc:
            md(f"**Claude against the offline rules, reading 29 offers.** Same result: {pc['match']:.0%} of lines "
               f"matched right and {pc['fields']:.0%} of fields read right. Claude took {pc['claude_s']:.1f} seconds "
               f"per offer against {pc['offline_s']:.2f}. The offline rules were written for these generated emails, "
               "so the next useful test is emails written by hand.")
        if ex["quicklook"]:
            table(ex["quicklook"])

    with st.expander("Stream or batch: the measurements"):
        for title, path in hood.DECISIONS:
            md(f"**{title}**")
            st.markdown(esc(hood.measured_table(path)))
            cap(f"From {path.relative_to(hood.ROOT)}.")

    with st.expander("What a real investor would question"):
        md("- **The price range** starts from a rule of thumb by strategy and fund age, then moves with what our own "
           "fund reports say since the broker's date. A real desk also checks against its own past bids.\n"
           "- **One asking price** for the whole portfolio is compared with a range built only from funds we own.\n"
           "- **Seller Watch** matches a headline to a pension by its name. \"New York pensions\" stays unmatched.\n"
           "- **Live mode**, when on, sends pasted text to Anthropic. A real desk would use a model in its own region "
           "that keeps no data, under its confidentiality and data processing agreements.")
    md("Code and docs: [github.com/alexandrecela10/secondaries-radar](https://github.com/alexandrecela10/secondaries-radar).")

st.divider()
cap(FOOTER)
