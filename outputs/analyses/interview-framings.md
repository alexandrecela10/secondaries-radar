# Interview framings

Two 90-second framings and answers to the questions a reviewer predicted. Numbers come from the builds and `outputs/decisions/`.

## For a Chief Data & Analytics Officer (about 90 seconds)

A secondaries team decides whether an offer deserves an NDA, and it often does that before the offer even names the funds. I built a prototype that answers that question in minutes, using data the fund already owns.

Offer Check reads a broker's offer and identifies each fund from its numbers. Every investor in a fund shares the same ratios on a given date, so our own quarterly data recognises a fund that the offer doesn't name. On labelled synthetic offers it matched 96% of lines, with no false matches. Then it applies the team's own written rules and gives one sentence: look closer or pass, with the confidence and the reasons. Every number links to its source row. The team can override any verdict, and every override is recorded with its reason. That record becomes the labelled data that improves the rules.

Then price. The broker prices every fund from its value on one past date, often months old. Where we already invest in a fund, its manager has told us what happened since: a company sold above its value, a write-down. Offer Check adds that to the broker's number and says whether the asking price is right by our own data. In the demo, one offer looks cheap and another looks fine until two write-downs make it too expensive. On a $330M offer, each point of price is $3.3M, which is worth more than the hours saved.

Seller Watch reads real public news and flags pensions showing signs they may sell. I tested it on two real past sales, and it gave no early warning. So it ships as monitoring, not as a deal-sourcing claim.

The pattern carries to other teams: the model reads, code decides, every number is sourced, and every human correction is kept.

## For an engineering lead (about 90 seconds)

Two pipelines on one pattern: bronze, silver and gold in DuckDB, with Pydantic contracts between the layers. A failed row is blocked and logged, never passed on.

- **Point in time:** a metric only sees data reported by its as-of date.
- **Model scope:** the model only parses or classifies, with schema-checked output. Matching, metrics and verdicts are pure, versioned, tested functions.
- **Lineage:** every number in a brief carries table, row id, metric and version, and a validator fails any brief with an unsourced number.

For intake, I built both a real-time path and a batch path behind the same processing function, and measured them against seven criteria.

- **Streaming:** for news it ran on Redpanda, with consumer groups, committed offsets, a dead-letter topic and a redrive. Both paths gave identical output and survived kill -9 with no duplicates and nothing missed.
- **Result:** batch won both times. News freshness is capped by the source's 15-minute refresh, and the decision window is weeks. The stream path needed 3 moving parts and 2,160 always-on hours a month. Batch needs 1 moving part and about 11 hours.
- **A bug the comparison caught:** the parity check found a real one, float sums that depended on set order.

Evals run in pytest as the gate, and Langfuse holds traces and dataset experiments to compare the offline parser with the Claude parser.

## Likely questions

**"Drop Seller Watch. What is Offer Check worth after cost, and how would you measure it?"**
Base case: about AED 0.55M net in year 1. That's 300 serious offers, 7 hours saved on each, at AED 500 an hour, less about AED 0.5M to build and run. The range is minus AED 0.35M to plus AED 1.18M. In production I'd measure three things from traces and overrides: the time from email to decision, the override rate, and good offers passed (the target is zero).

**"Where does an NDA offer physically go when the parser reads it?"**
In the demo, nowhere. All offers are synthetic. In production it goes to a model hosted in-region with no data retention, after legal has reviewed the NDA and the data processing agreement. Offer data stays separate from Seller Watch. Only the parse step sees the text, and everything downstream is our own code.

**"Which component would a second team adopt first?"**
Two components. The sourced-number validator works for any AI-written memo. The rulebook-plus-override loop works for any screening job, for example monitoring re-ups with existing managers or screening co-investments. A second team would change the metrics and the rules, not the pattern.

**"Why build rather than buy?"**
Vendors sell market data. Our edge is joining offers with our own fund data, which no vendor has. I'd buy news and fund databases, and build the join and the rules.

## Honest limits to say first

- Labels are mine. A real test needs 2 or 3 analysts and their agreement rate.
- The price range is a rule of thumb and is shown only for funds we own.
- The price check is tested on synthetic data that also made the answers (15 of 17 funds closer to the next value than the broker's number). The real test replays our own past fund reports.
- Seller Watch's evidence is two worked cases, so it illustrates the mechanism and doesn't measure it.
