---
prd: secondaries-radar-solution-review.md
review_date: 2026-10-24
stage: Solution Review (demo build)
agents: [engineer, designer, executive, legal, uxr, skeptic, customer]
---

# PRD Review Synthesis: Secondaries Radar

## TL;DR

**Overall:** requires significant work on scope, the value case and Seller Watch's framing. The governance design (sourced numbers, rulebook, overrides, no send capability) was praised by every reviewer.

**Critical blockers:** 6. **Important gaps:** 5. **Conflicts:** 2.

**Recommended next step:** PM decides the two conflicts below, then one revision pass on the PRD and opportunity analysis.

---

## Critical blockers

### 1. The value case rests on its weakest input
**Flagged by:** Executive, Skeptic, UXR.
**Issue:** about 91% of the $4.36M is one direct deal at a 2% better price. It's unattributed, gross not net, and analyst hours aren't cash unless redeployed.
**Fix:** show low / base / high ranges net of build and run cost. Offer Check is the base value. Seller Watch is an option, shown as break-even: a $200M deal priced 0.1% better is worth $0.20M.

### 2. Seller Watch's backtest and premise don't hold as written
**Flagged by:** Skeptic, Executive, UXR.
**Issue:** 5 to 7 hand-picked public sales is too few, with selection and look-ahead bias. The top signal ("engage secondary advisor") means an auction is starting, so of course it precedes the press. Public pensions usually must run a competitive process, so "direct deal" may rarely happen.
**Fix:** reframe the outcome as "prepare early, bid first and best", which includes earlier, better-informed bids inside an auction. Measure lead time from the first signal that is not a sale announcement. Present 2 sales and 2 controls as worked timelines, not a statistic.

### 3. The confidence number reads as decoration
**Flagged by:** Skeptic, Design, Engineering, Customer.
**Issue:** the 0.2 constant is arbitrary, nothing ties 0.68 to the chance of being right, and users say "0.68 means nothing to me".
**Fix:** lead with plain words: "LOOK CLOSER, medium confidence: 1 fund unmatched (9% of value), close to the line." Show two parts, data matched and margin to threshold, with bands (high / medium / low). Calibration becomes a v2 item once overrides exist.

### 4. Formulas are underspecified
**Flagged by:** Engineering.
**Fix:**
- **F-2:** normalise the seller score to 0 to 1 (1 − e^(−sum)), decay 0.5^(age/90), and state a CALL NOW threshold.
- **R-2:** a metric that can't be computed excludes its rule and lowers confidence.
- **R-3:** use the absolute gap. A veto verdict gets high margin. A line confidently tiered "unknown" counts as covered.
- **Q-1:** add asking price and reference date at offer level.
- **C-2:** normalise numbers (68% = 0.68) and allow years and fund ordinals.
- **Q-3:** FX comes from a synthetic rate table.

### 5. Data path for confidential offers is unstated
**Flagged by:** Legal, Executive.
**Issue:** in real life, offers arrive under NDA. The PRD doesn't say where they go, what reaches an external model, or how offer data is kept apart from seller scoring.
**Fix:** add a short deployment note:
- **Demo:** all offers synthetic.
- **Real deployment:** in-region model hosting, NDA and data processing agreement review, and an information barrier so offer data never feeds Seller Watch.

### 6. Scope is 6 to 8 days, not 3
**Flagged by:** Engineering (complexity XL), Skeptic, Executive.
**Fix:** see Conflict 1.

---

## Important gaps

- **Rough price on a first call** (Customer, Skeptic). A partner won't quote a rule of thumb on stale data. Fix: give a price only for funds we own, from our own latest reported value, with the method shown. Every other fund shows "no price".
- **Non-happy states and the override surface** (Design). An HTML page can't write back. Fix: override through a CLI or chat command. Add a states table: parse failure, coverage 0, veto fails, no sellers this week, rulebook error.
- **Circular labels** (Executive, Skeptic). The PM writes the rules and labels the test offers. Fix: say so in the demo. Real version: 2 or 3 analysts label, and we report how often they agree with each other.
- **Unvalidated triage assumptions** (UXR). Many passes take 15 to 30 minutes. Full fund names often arrive only after an NDA. The "1 day" (Today column) contradicts "2 days" (Cedar). Fix: use ranges, add an assumption / source / how-to-validate column, and do 1 or 2 practitioner calls.
- **Publishing hygiene** (Legal).
  - Keep the bronze layer and the frozen quote corpus out of git.
  - Strip combinations that could re-identify a pseudonymised manager.
  - Add GDELT attribution and a disclaimer.
  - The public version shows only historical, already-reported cases, with current rankings as "Pension A/B".

---

## Enhancements (v2 unless cheap)

- **Customer, cheap:** bid deadline, our ticket size versus the deal, a next step and owner, and a paragraph the analyst can paste into an IC memo.
- **Customer, v2:** inbox intake, Excel export, a relationship history and CRM log for the partner.
- **Executive:** name the reusable assets: fund and manager matching, the sourced-number validator, the rulebook pattern. Name a second user: re-up monitoring of existing managers, or co-investment screening.
- **Executive:** per-component evals (classifier precision and recall), a non-LLM baseline, cost per offer, and a buy-versus-build check against data vendors.
- **Design:** veto rules on top, reasons sorted by weight, a plain-English rulebook view (partner reads, analyst edits), and the R-6 diff written as sentences ("Cedar flips to PASS").
- **Engineering:** match with rapidfuzz plus vintage and strategy checks, with the LLM only for parsing. Build order: public ingest, then pseudonymised IDs, then the synthetic generator.

---

## Conflicting perspectives

### Conflict 1: scope
- **Narrow** (Skeptic, Engineering, Executive):
  - Build Offer Check end to end.
  - Seller Watch becomes one timeline slide.
  - Cut streaming, PDF parsing and chat.
- **PM's stated goals:** both gates, plus streaming as a learning exercise.
- **Recommendation:**
  - **Offer Check:** complete (M1, M2).
  - **Seller Watch:** thin but real. Manual ingest, 2 sales and 2 controls, a call briefing, and prices only for funds we own (M3, one day).
  - **Chat:** keep it thin. It is the override and "why" surface.
  - **PDF offers:** a stretch goal.
  - **Streaming spike:** timeboxed after M3, outside the demo's critical path.

### Conflict 2: what Seller Watch promises
- **Original:** "find sellers before anyone else, agree a direct deal."
- **Reviewers:** large buyers already see every broker process, and public pensions must run auctions.
- **Recommendation:** promise preparation, not exclusivity. "We know who will sell and what we'd pay, weeks before the offer arrives, so our first bid is our best one." This survives both objections and is still worth money.

---

## Detailed ratings

| Reviewer | Headline | Rating |
|---|---|---|
| Engineering | Formulas need edge cases; real-data collection is manual research | XL as written, L after cuts |
| Design | Right hierarchy; numbers need words; override surface undefined | Usability risk Medium |
| Executive | Strong governance; value case gross and First-Call-heavy | Impact Medium, fit Medium-High as a platform pattern |
| Legal | Fine privately; fix before publishing | Low private, Medium if published unchanged |
| UXR | Public premise grounded; triage assumptions unvalidated | Validation Weak |
| Skeptic | Backtest and confidence don't support the claims | n/a |
| Customer | Associate: "I'd use it, but check its work." Partner: "Skeptical until the backtest" | Would use it |

**Questions the executive reviewer would ask:**
1. Drop Seller Watch. What is Offer Check's net annual value, and how would you measure it in production?
2. Where does an NDA offer physically go when the parser reads it, and who signs off?
3. Which component would a second team adopt first, and what would change for them?

---

## Action items

- [ ] Decide Conflict 1 (scope) and Conflict 2 (Seller Watch promise). Owner: PM
- [ ] Approve D1 (pseudonymise managers). Owner: PM
- [ ] Revise PRD: blockers 1 to 6 and the gaps above. Owner: Claude
- [ ] Revise opportunity analysis: value ranges, Seller Watch framing, Today wording. Owner: Claude
- [ ] 1 or 2 practitioner calls on triage time and teaser contents. Owner: PM
