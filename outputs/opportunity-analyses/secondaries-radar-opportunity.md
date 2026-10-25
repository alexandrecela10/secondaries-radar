# Secondaries Radar: Opportunity Analysis

**Date:** 2026-10-24 (price check added 2026-10-25)
**Owner:** Alexandre Cela
**Status:** Draft (recommendation: Go)

## Vision

**See every sale first. Price it best. Prove every number.**

The fund buys private equity stakes from investors who want out early. The end: buy more of the right stakes, at lower prices, without additional headcount.

This opportunity delivers the first two steps:
- **First Call (Gate 0):** spot investors likely to sell, before a sale starts.
- **Quick Look (Gate 1):** decide in under an hour whether a sale offer deserves a closer look, and whether its price is right by what we already know.

## Problem Statement

The fund hears about most sales late, when a broker sends a short sale summary to many buyers at once. Each summary then takes about a day of analyst work before anyone can say "look closer" or "pass". And we price it from the same out-of-date numbers as every other buyer, even when our own records say more.

## Current Situation

- **Late:** signs that an investor wants to sell often appear first in public, in board papers and the press. Nobody reads these sources systematically.
- **Slow:** sale summaries hide fund names behind abbreviations. An analyst matches them by hand, then checks the fund's own records on each one.
- **Same numbers as everyone:** brokers price every fund from its value on a past date, often 3 to 6 months old. Where we already invest in the same fund, we know what happened since: companies sold above or below their value, write-downs. That knowledge rarely makes it into the first view of the price.

**Two concrete cases.**
- **Late (public reporting).** Washington State's pension board stopped a private equity sale in 2022, then restarted one with an advisor in 2023. A buyer reading its board papers knew the seller and the motive months before the second sale began.
- **Slow (labelled hypothetical).** A broker sends "Project Cedar": stakes in 12 funds worth $340M, with names like "KPF IV". An analyst spends most of a day working out which funds these are. The fund already knows 9 of the 12 fund managers, but that only becomes clear on day two. It also invests in 3 of the funds, and one of them sold its largest company in August for 55% more than its value in June, the date the broker prices from. Nobody connects the two before the bid.

## Proposed Solution

- **First Call:** a language model, an AI that reads text, flags news and board papers that suggest an investor may sell. Each flag quotes its source with a link. It gives a verdict (prepare, or watch), so the team can get ready weeks before an offer arrives. A partner decides what to do. The system never contacts a seller.
- **Quick Look:** the language model reads each sale summary and matches every line to a known fund, with a confidence score. Sale offers hide fund names before an NDA, so the system identifies each fund from its numbers (share of commitment paid in, cash returned, total value), which are the same for every investor in a fund. Tested calculations then give a verdict: look closer, or pass.
- **Price check:** for funds we already invest in, plain code starts from the broker's value, adds what we have learned since from our own fund reports, and shows what the stake is worth today by our data, next to the asking price. Each adjustment shows its source. The verdict then answers two questions: does it fit us, and is the price right.
- **Every verdict opens up:** each verdict carries a confidence level and the reasons behind it, each reason tied to a rule and a number.
- **The team's rulebook:** the team writes down, in plain text, what a good deal looks like for them: fund types, maximum price, minimum share of funds we already know. The AI applies these rules, not its own. The team can edit them at any time.
- **Human override:** an analyst can overrule any verdict with a one-line reason. Every override is kept, and shows where the rules or the AI need to change.
- **Checks on the AI:** the model can only use tested calculations, never invent a number. Every number shows its source. A test set of questions with known answers checks this.

## Problems, hypotheses and success

| # | Problem | Today | Hypothesis | Success metric | Expected impact |
|---|---|---|---|---|---|
| 1 | Each sale offer takes from 15 minutes to a day to assess | 15 minutes for an obvious pass, up to a day for a serious look (assumed) | If the AI matches each fund in the offer to our own records before the analyst opens it, then the analyst decides in under an hour, because the lookup work is already done | Analyst time per offer: under 1 hour | About 7 analyst hours saved per offer. Where we already own the same fund, we price it from our own reports, which other buyers can't see |
| 2 | We start preparing only when the offer arrives | We learn of a sale from brokers, at the same time as every other buyer (assumed) | If the AI reads news as it is published and flags pensions showing sale signals, then we start preparing weeks earlier, because pensions discuss allocations and liquidity in public before they hire a broker | Weeks from the first signal that is not a sale announcement to the first sale report, on 2 worked historical cases: 8 or more (illustration) | Better-prepared first bids, inside or outside an auction; size unobserved |
| 4 | We price from the same out-of-date numbers as every other buyer | Brokers price from a value 3 to 6 months old; what we learned since as an investor in the fund rarely reaches the first price view (assumed) | If code updates the broker's value with what we learned since from our own fund reports, then we see which offers are cheap or expensive before other buyers do, because they only have the old value | Our estimate lands closer to the fund's next reported value than the broker's number does, for 70% or more of funds we own (synthetic in the demo; real test on our own past fund reports) | We bid with confidence where it is cheap and walk away where it is expensive. On a $330M offer, each 1% of price is $3.3M |
| 3 | The AI's idea of a good deal may not match the team's | Rules exist in policy documents, but each analyst applies them in their head (assumed) | If the team writes its own rules and the AI explains each verdict against them, then the team keeps most verdicts, because the AI judges by the team's rules, not its own | Verdicts the team keeps without overriding, on the test set: 80% or more | Same offer, same verdict, whoever reads it. Every override is recorded with its reason |

**Must not get worse:** good offers marked "pass". Target: 0 on the test set.

**Not tackled here:** a full bid price from projected cash flows (after the NDA, Gate 2), a price view for funds we don't invest in, a call briefing with each pension's holdings and a price (needs real holdings joined with our data; v2), detailed pricing after a closer look (Gate 2), fund manager consent to a sale, investors that publish no fund-level holdings.

## Value Assessment (rough)

All inputs are assumptions, to replace with the team's real numbers.

| Quick Look | Low | Base | High |
|---|---|---|---|
| Serious offers per year × hours saved × AED per hour | 150 × 2 × 500 | 300 × 7 × 500 | 400 × 7 × 600 |
| Gross value per year | AED 0.15M | AED 1.05M | AED 1.68M |
| Minus run cost and upkeep (0.5 engineer) | AED 0.5M | AED 0.5M | AED 0.5M |
| **Net, year 1** | **AED -0.35M** | **AED 0.55M** | **AED 1.18M** |

- **First Call is an option, not base value.** It breaks even if a better-prepared bid improves the price by 0.1% on one $200M deal (AED 0.73M).
- **Price check is an option, not base value,** until it is tested on our own past fund reports. Rough size: 2 deals a year × $200M × 40% in funds we own × 1% better price = $1.6M (AED 5.9M). Low case, 1 deal and 0.25%: $0.2M (AED 0.6M). It works both ways: better prices on cheap offers, and fewer overpaid deals on expensive ones.
- **Hours are capacity, not cash,** unless they go to more deals or deeper work on serious ones.

## How It Could Be Delivered

A demo-grade working prototype. First Call uses public pension reports (CalPERS and Washington State publish fund-level data), board papers and news, collected on a schedule. Quick Look and our own portfolio use synthetic data. A streaming collection path is built next to the scheduled one as a learning exercise, measured, then retired.

```
FIRST CALL   2026-10-24
1. Pension A   score 0.81   signal: "allocation review" (board papers, 2026-09-12) [link]
   46 funds, $3.1bn reported value; 58% with managers we know
   Rough price 86-90% of reported value [rule of thumb, v1]
   Call pack ready. Draft note cites 6 numbers.

QUICK LOOK   Project Cedar   received 09:00
Fits us:   YES   9 of 12 managers known (78% of value), fund types match
Price:     LOOKS CHEAP on the 3 funds we own (41% of value)
           Broker value (June)   $135M
           + Fund 4 sold a company 55% above its June value  +$5M  [our report, Aug]
           Worth today, our data $140M (+4%)
           Seller asks high 80s; by our data we'd pay up to 88%  (illustrative)
Fund 9 (9% of value): not identified. Ostrova or Brennick? [pick]
[keep verdict]   [override: pass, reason ____]
```

## Value Equation (Hormozi)

> Adapted from Alex Hormozi's Value Equation (*$100M Offers*): Value = (Dream Outcome × Perceived Likelihood of Achievement) / (Time Delay × Effort & Sacrifice). For an internal tool, read "would they actually use it" for "would they buy it."

| Factor | This solution |
|---|---|
| **Dream outcome** | "We call the seller before anyone else, with a price they take seriously, we know when an offer is cheap or expensive before other buyers do, and we spend our time only on offers worth it." For the data leader: one checkable AI pattern, proven on one high-value team, that other teams can reuse. |
| **Perceived likelihood** | Medium. A test on real past pension sales is evidence investors accept. Every number showing its source raises belief. Verdicts follow the team's own rules and can be overruled, so the team stays in control. |
| **Time delay** | Quick Look pays off on the first offer. First Call pays off months later, when a direct deal closes. |
| **Effort & sacrifice** | Write the rulebook once, then read a one-page verdict and a ranked seller list. The partner keeps the relationship and the call. |

**Verdict.** Strong ratio. Quick Look gives fast proof, First Call gives the large gain. The cheapest lever is belief: open every demo with one real past sale, signal date then sale date, and one number traced to its source.

## Recommendation

**Go.** Signals are public, the fund owns the data that gives it an edge on price, and both gates can be shown working on public and synthetic data. Proceed to `/prd-draft`, scoped as a demo.

---
Sources: context-library/strategy/secondaries-radar-brief.md; calpers.ca.gov PEP fund performance page; sib.wa.gov reports; secondariesinvestor.com (Washington State 2022/2023); Bloomberg (NYSTRS, June 2023); dakota.com (PSERS, 2024); ai-cio.com (NYC pensions). "Today" values and value inputs are assumptions. No user interviews yet.
