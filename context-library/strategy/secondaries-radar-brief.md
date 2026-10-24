# Project brief: Secondaries Radar (Gate 0/1 for an LP secondaries buyer)

## Who I am and how I work
I'm Alexandre, a VP-level AI/data product person with a data science and data engineering background. I build things myself. Work with me as a senior pair, not as a contractor: propose a plan with milestones, let me approve, then help me build step by step. Prefer surgical edits over rewrites. Explain your reasoning briefly; don't pad.

Two interviews are driving this: a 30-minute conversation with a Chief Data & AI Officer at a large endowment-style sovereign wealth fund (the LP), and a 1-hour technical interview with a hands-on Director of Investment Engineering at a family office. I may not demo this. I want to walk in with a working prototype, a clear point of view, and real understanding.

## Two goals, equally important

### Goal A: build a prototype
A system that helps an LP secondaries investor decide, fast and proactively, whether a potential deal deserves attention (Gate 0: before a teaser exists; Gate 1: teaser → sign NDA). Information should reach the investor two ways:
- **Push:** alerts and a daily brief ("a teaser arrived, we know 9 of 12 GPs, fit is good, guidance is above our range; recommend NDA, low priority"; "pension fund X shows seller signals; it holds 14 funds we track").
- **Pull:** chat over a governed semantic layer, where the LLM can only call tested metric functions, never free-form SQL on raw data. Every number traceable to a source row and a metric version.

### Goal B: educate me on LP-led secondaries
As we build, teach me the investor side so I can speak as a subject-matter expert. Specifically:
- Each time we touch a domain concept (NAV, reference date, unfunded, true-up, ROFR, GP consent, pricing as % of NAV, LP-led vs GP-led, mosaic bids, deferred payment, tail-end funds, J-curve, PME, Takahashi-Alexander cash-flow model), explain it in plain terms with a worked example, and tell me how an experienced buyer actually thinks about it.
- Maintain `docs/GLOSSARY.md` and `docs/INVESTOR_NOTES.md` as we go.
- Occasionally quiz me (3–5 questions) on what we've covered. Correct me directly.
- Flag where my modelling simplifications would make a real investor wince, and what the real-world version looks like.

## Scope

**In scope**
- Gate 0: detect likely sellers and likely-to-trade funds from news and fundraising signals.
- Gate 1: ingest a teaser (anonymised portfolio description), match to candidate funds, score fit and knowledge, estimate a price range, produce a one-page brief.
- Semantic layer with governed metrics.
- Three ingestion patterns, chosen on purpose: batch (quarterly fund data), event-driven (teaser file lands → pipeline runs), streaming or near-real-time only for news signals.
- Chat interface with tool calls on the semantic layer, plus an eval set.

**Out of scope (describe as phase 2, do not build)**
- Gate 2 deal pricing on data-room documents, PDF extraction of quarterly reports, real benchmark vendor integration, GP consent workflow.

**Hard constraint:** synthetic data only. Nothing from any employer. Fund, GP and LP names are invented.

## Semantic layer

### Entities
- **GP**: id, name, strategy focus, HQ region, founded year, current fundraising status (raising / not raising, target fund number).
- **Fund**: id, gp_id, name, vintage, strategy (buyout, growth, venture, credit, infra, real estate), geography, size, currency, status (investing / harvesting / tail-end).
- **FundSeries** (quarterly): fund_id, as_of_date, nav, cumulative_called, cumulative_distributed, unfunded. Point-in-time: NAVs can restate; keep `reported_at` separate from `as_of_date`.
- **Company**: id, fund_id, name, sector, entry_date, exit_date (nullable), holding value share of fund NAV.
- **LP** (potential seller): id, name, type (pension, insurer, endowment, SWF, family office), region, PE allocation vs target, liquidity pressure indicator.
- **Holding**: lp_id, fund_id, commitment, is_ours (true for our own positions).
- **Deal**: id, received_at, source (intermediary / direct / GP-led), type (LP-led / GP-led), teaser text, total_nav, total_unfunded, reference_date, price_guidance, timeline, status (teaser / NDA / bid / won / lost / passed).
- **DealLine**: deal_id, fund_id (nullable until matched), described_strategy, described_vintage, nav, unfunded, match_confidence.
- **Bid**: deal_id, round, price_pct_nav, assumptions (json), outcome, realised_irr (nullable, filled over time).
- **Signal**: id, observed_at, source, text, signal_type (liquidity pressure / overallocation / policy change / GP fundraising / leadership change), linked_lp_id, linked_gp_id, confidence.

### Relationships
GP manages Fund. Fund has FundSeries and Companies. LP holds Fund via Holding. Deal contains DealLines that reference Funds. Deal has Bids. Signal mentions LP and/or GP.

### Governed metrics (each a pure, tested, versioned function)
1. `tvpi(fund_id, as_of)` = (distributed + nav) / called
2. `dpi(fund_id, as_of)` = distributed / called
3. `irr(fund_id, as_of)` from the cash flow series plus terminal NAV
4. `pme(fund_id, as_of, index)` Kaplan-Schoar against a synthetic public index
5. `fund_age(fund_id, as_of)` in years from vintage
6. `pct_funded(fund_id, as_of)` = called / commitment
7. `concentration(deal_id)` share of deal NAV in top 5 lines
8. `our_exposure(gp_id)` our commitments and NAV across the GP's funds
9. `knowledge_score(deal_id)` share of deal NAV in GPs we hold or have bid on before
10. `fit_score(deal_id)` weighted fit to an allocation plan (strategy, geography, vintage band, % funded, size)
11. `price_range_estimate(deal_id)` simple: from vintage/strategy base discounts and our past clearing levels; clearly labelled as a heuristic
12. `quartile_rank(fund_id, as_of)` vs an immutable peer snapshot for the same vintage and strategy

Point-in-time rule: any metric at `as_of` may use only data with `reported_at <= as_of`.

## Data (synthetic)
- ~40 GPs, ~120 funds, vintages 2008–2024, quarterly series to 2026, with realistic J-curves, dispersion by strategy, and some NAV restatements.
- ~400 companies with exits.
- ~25 LPs; we are one of them with ~60 holdings and ~15 historical bids with outcomes.
- 3 teasers in different formats (plain email text, a tabular schedule, a short PDF), one of them messy (abbreviated fund names, mixed currencies).
- ~60 news items, some genuine seller signals, some noise.
Make the generator reproducible (seeded) and parameterised.

## Architecture and engineering practices to demonstrate
- Python 3.11+, DuckDB for storage and SQL, pandas/polars, Pydantic for schemas, pytest, a simple orchestrator (plain Python or Prefect-lite; no heavy infra).
- Medallion layers: `bronze` (raw as received), `silver` (typed, validated, deduplicated), `gold` (metric marts). Schema contracts between layers; validation failures block and are logged.
- Entity resolution for teaser lines → funds, with confidence scores, never silent matches.
- Immutable benchmark snapshots per vintage/strategy.
- Idempotent, incremental loads.
- Event-driven intake: watch a folder; a new teaser file emits an event that triggers parse → match → score → brief. Explain in `docs/ARCHITECTURE.md` where true streaming (Kafka/Flink) would and would not earn its place.
- LLM layer: tool-calling only over the governed metrics; narrative constrained to computed values with cell-level citations; prompts and tool schemas versioned.
- Evals: ~25 questions with golden answers, run in pytest. Criteria: 100% metric correctness on fixtures; zero fabricated numbers; every number traceable.
- Lineage: each number in a brief carries (source table, row id, metric name, metric version).
- A `CALIBRATION.md` sketch: how bids and outcomes would feed back into price_range_estimate.

## Interfaces
- CLI first. Then a minimal chat loop (terminal is fine) and a generated HTML one-page Gate 1 brief per deal plus a daily brief.
- No front-end polish.

## Milestones (I have roughly three days; propose a plan against these and ask me to confirm)
1. **L0 (day 1):** synthetic data generator; semantic layer tables; metrics 1–6 and 12 with tests; first quiz on fund metrics and the J-curve.
2. **L1 (day 2):** teaser intake for 3 formats; entity resolution; metrics 7–11; one-page Gate 1 brief; event-driven trigger; investor notes on the NDA decision.
3. **L2 (day 3):** chat with tools and eval set; news signals → Gate 0 alerts; daily brief; architecture doc; final quiz covering LP-led secondaries end to end.
4. **Describe only:** Gate 2 pricing (NAV roll-forward, cash-flow projection, discounting), PDF extraction, GP consent tracking.

## Definition of done for the interviews
- I can explain the end-to-end LP-led secondaries workflow and the information needed at each gate, without notes.
- I can run one command and get a Gate 1 brief for a new teaser, with traceable numbers.
- I can show the eval results and explain each architectural choice and its trade-off.
- Two 90-second framings written: one for a CDAIO (proactive information, governance, adoption), one for an engineering lead (layers, contracts, point-in-time, evals, ingestion patterns).

## Start
Begin by proposing the day-1 plan and the repo structure. Then give me a 10-minute primer on LP-led secondaries before we write code.
