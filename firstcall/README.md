# First Call (thin build)

First Call reads public news about US public pensions and flags pensions that show signs they may sell private equity stakes.
Each flag quotes its evidence: headline, outlet, date and link.
The verdict is about our preparation (PREPARE or WATCH), never a claim that a pension is selling.

**Synthetic buyer. Not investment advice. Not a statement of any pension's intent.**

## Journey: the partner's Monday review

One persona, one job. Omar, a partner, plans the week on Monday at 08:00 and has 10 minutes
(`../outputs/journey-maps/first-call-partner-journey.md`). Every command below is one step of that journey.

```bash
make setup && source .venv/bin/activate     # once; puts `firstcall` on the path
make collect-batch                          # overnight: fetch, classify, score (one Langfuse trace per run)
```

**08:00, reads the list.** `firstcall weekly`. PREPARE first, then the top of WATCH, then closed sales as evidence only.
Below is real output from 24 October 2026, anonymised for this public page (`firstcall weekly --anon`).
The full list with names and links is in your local `reports/` and `data/`, which git ignores.

```
FIRST CALL   week of 2026-10-19   as of 2026-10-24   rulebook v2
Classifier: claude:claude-sonnet-5-5/fc-classify-v3
------------------------------------------------------------------------------
WATCH    Pension A      score 0.36  "[Pension] disclose details of multi-billion sale"
                       PEI Secondaries Investor, 2026-08-27   2 more signals
WATCH    Pension B      score 0.28  "[Pension] lowers PE target due to lack of 'options' in current market"
                       PEI Private Equity, 2026-10-07
WATCH    Pension C      score 0.25  "continues liquidity priority amid ongoing cash flow pressures"
                       Pensions & Investments, 2026-07-02   2 more signals
...
No pension crossed PREPARE. 16 on WATCH.
Closed sales in the last 90 days (evidence only, not scored): 1
  2026-09-08  [Pension]    "Blackstone was sole buyer of [Pension]' secondaries sale" (PEI Secondaries Investor)
```

**08:03, checks evidence.** `firstcall why <pension>` accepts an id or a name (`nyc_funds`, `WSIB`, `Washington State`).
Real names appear below only for a sale that is already public: New York City, May 2025.

```
$ firstcall why nyc_funds --as-of 2025-06-08
WATCH: New York City Retirement Systems (nyc_funds), score 0.55 as of 2025-06-08
score = 1 - exp(-S), S = 0.800; PREPARE at 0.6 or more; half-life 90 days; window 365 days.

- sale_or_advisor  weight 1.0 x confidence 0.80 x decay 0.91 (age 12 d) = 0.731
  "NYC pension's $5 billion sale of private equity stakes might be largest ever of its kind"
  Axios, 2025-05-28, copies 1
- leadership_change  weight 0.2 x confidence 0.80 x decay 0.43 (age 109 d) = 0.069
  "NYC Pension Systems Name Valerie Red-Horse Mohl as Deputy CIO"
  Chief Investment Officer, 2025-02-20, copies 1
- closed sale, evidence only (weight 0 under rulebook v2)
  "Blackstone acquires $5bn PE portfolio from NYC pension"
  Private Equity Wire, 2025-05-28
```

**08:06, sees the history.** `firstcall timeline <pension>`: one row per week, the score as a bar, `|` at the PREPARE line, each signal on the week it was published.

```
$ firstcall timeline nyc_funds --as-of 2025-06-08 --weeks 16
TIMELINE New York City Retirement Systems (nyc_funds)   16 weeks to 2025-06-08   rulebook v2
one row per week, score at the end of the week; '|' is PREPARE at 0.6

2025-02-23  0.14 ######                  |                 WATCH
                 + 2025-02-20 leadership_change "NYC Pension Systems Name Valerie Red-Horse Mohl as Deputy CI" (Chief Investment Officer)
2025-03-02  0.14 #####                   |                 WATCH
...
2025-05-25  0.07 ###                     |                 WATCH
2025-06-01  0.57 ####################### |                 WATCH
                 + 2025-05-28 sale_or_advisor closed, evidence only "Blackstone acquires $5bn PE portfolio from NYC pension" (Private Equity Wire)
                 + 2025-05-28 sale_or_advisor "NYC pension's $5 billion sale of private equity stakes might" (Axios)
2025-06-08  0.55 ######################  |                 WATCH
```

**08:08, decides.** Prepare (assign an analyst) or dismiss with a reason. Each decision is stored with who, when, the rulebook version
and the score at that moment, and sent to Langfuse as a `partner_decision` score on the latest batch-run trace. Dismissals feed the rule review.

```
$ firstcall act <pension> prepare --owner layla          # Pension A
Recorded prepare, owner layla for [Pension A] by omar at 2026-10-24 23:06 UTC, rulebook v2, score then 0.36 (WATCH). Sent to Langfuse as score partner_decision.
$ firstcall dismiss <pension> --reason "Liquidity talk is about benefit payments, not a PE sale"   # Pension C
Recorded dismiss: Liquidity talk is about benefit payments, not a PE sale for [Pension C] by omar at 2026-10-24 23:06 UTC, rulebook v2, score then 0.25 (WATCH). Sent to Langfuse as score partner_decision.
```

`who` is `$FC_USER`, else your login name. The weekly list then shows `[prepare: layla, 2026-10-24]` next to that pension.

## Other commands

| Command | What it does |
|---|---|
| `make test` | All unit tests and evals, offline. No key and no network needed, even when `.env` has keys |
| `make eval` | Offline classifier precision and recall on 130 labelled real headlines, with each error |
| `make eval-langfuse CLASSIFIER=keyword` | Same headlines as a Langfuse dataset and experiment, dev and holdout. Local run without keys |
| `make eval-llm` | The same experiment with the Claude classifier (about 125k tokens, under USD 0.50) |
| `make cases` | Replay the two worked historical cases point in time |
| `make rebuild` | Re-label every landed fetch after a rule or prompt change (backfill) |
| `make collect-stream`, `make up`, `make down`, `make measure` | Stream path and the batch vs stream experiment. A learning artefact, not on the journey: see `LEARNING.md` |
| `firstcall rulebook` / `firstcall pensions` | The rulebook as sentences; the 61 pensions we link to |

## How it works

```
Google News RSS + GDELT  ->  bronze (as received)  ->  silver (typed, deduplicated, linked)  ->  classify  ->  gold (signals, scores)
```

- **Batch is the production path.** One job every 15 minutes or overnight. Why: `../outputs/decisions/first-call-collection-architecture.md`.
- **Linking:** each headline links to at most one of 61 public pensions, or to none. Short forms such as "Washington State", "NYC" or "New York teachers" link only when a pension or finance word sits in the same headline, so sports, weather or city news never links. Decoys such as "Pension Bros" never link. A name right after "former" does not win.
- **Classify:** six labels from PRD F-2, plus a `completed` flag on sale stories. Claude (`claude-sonnet-5-5`, prompt `fc-classify-v3`, structured output, schema-checked) runs when `ANTHROPIC_API_KEY` is set. Otherwise deterministic keyword rules (`kw-3`) run and the list says so. A stored label from another classifier version is redone on the next run.
- **Score:** `score = 1 - exp(-S)`, `S = sum(weight x confidence x 0.5^(age_days / 90))`. PREPARE at 0.6 or more. Weights live in `config/rulebook.yaml`.
- **Rulebook v2:** a story about a sale that already closed is shown as evidence and adds nothing to the score. Weights and threshold are unchanged from v1.
- **Point in time:** a score or a decision at date T uses only what existed on or before T.

## Tracing and evals (Langfuse)

Keys are read from `firstcall/.env`, then the repo `.env`: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`. Without them every tracing call is a silent no-op. Key values are never printed.

- **One trace per batch run** (`run-batch`): a `fetch-source` retriever per source and query, `dedupe-items`, one `classify-headline` per classified item (a generation with model, parameters, prompt version, tokens and cost when Claude runs; a span for keyword rules), and `score-pensions` with each verdict. Environment `development` unless `FC_ENV` says otherwise.
- **Feedback:** `act` and `dismiss` post a categorical `partner_decision` score with the reason, owner and rulebook version.
- **Dataset** `firstcall/labelled-headlines`: input is the headline and quote, expected output is the signal type and pension. Experiments post `type_correct` and `pension_correct` per item, and `precision`, `recall` and `link_accuracy` per run. The pytest evals stay the gate.

Measured on 24 October 2026 (`measurements/eval_keyword.json`, `measurements/eval_claude.json`):

| Classifier | Dev precision | Dev recall | Holdout precision | Holdout recall | Link accuracy |
|---|---|---|---|---|---|
| Keyword `kw-3` | 0.95 | 1.00 | 0.90 | 0.83 | 0.99 |
| Claude `fc-classify-v3` | 0.86 | 0.95 | 0.91 | 0.91 | 0.99 |

The keyword rules were tuned on dev, so holdout is the fair comparison. There, Claude catches 2 more of 23 labelled signals at the same precision. With 23 positives per split, one item moves recall by 4 points: treat these as close.

## Data rules

- **Real public news only.** We store headline, link, outlet, publish time and a quote of 300 characters or less. We never fetch or store article text.
- **No join with synthetic data (PRD D-3).** First Call holds no holdings and no prices for any real pension.
- **Public docs show live rankings anonymised.** Real names appear only for sales already publicly reported (WSIB 2022, NYC 2025).
- **Rate limits.** GDELT: one request every 5 seconds, back off 10, 20, 40 s on a refusal. Google News RSS: once per query per run.
- **Out of git:** `data/`, `reports/`, `.env` and `fixtures/frozen_corpus/*.jsonl`.

## Attribution

News discovery uses the GDELT Project DOC 2.0 API (https://www.gdeltproject.org/) and Google News RSS search. Headlines, outlets and links belong to their publishers.

## Worked cases (n = 2, an illustration, not a statistic)

`make cases`, rulebook v2, keyword classifier (`measurements/worked_cases.json`; Claude run in `worked_cases_claude.json`):

| Case | First open sale signal | Score then | First PREPARE | Ground truth | Weeks of warning |
|---|---|---|---|---|---|
| [WSIB 2022](https://www.privateequityinternational.com/as-philosophies-change-washington-state-gears-up-for-first-secondaries-sale/) | 2022-04-20 | 0.62 | 2022-04-20 | 2022-04-20 | 0 |
| [NYC 2025](https://comptroller.nyc.gov/newsroom/nyc-comptroller-lander-and-pension-trustees-close-record-private-equity-secondary-sale) | 2025-03-04, 12 weeks before the close | 0.57 | 2025-05-28 | 2025-05-27 | 0 |

With Claude, NYC reaches PREPARE on 2025-03-04 (12 weeks). That crossing rests on two `policy_review` labels for fossil-fuel exclusion stories, which a buyer would not count. Without them the score is 0.59. Do not read it as a pass.

## Known weaknesses

- **One open sale report alone stays on WATCH.** At confidence 0.85 it scores 1 - e^-0.85 = 0.57. In the NYC case that kept a correct report 12 weeks before the close below the line. Proposal, not applied: make an open sale report with confidence 0.8 or more PREPARE on its own, as an explicit rule, rather than lowering the threshold or raising the weight. A sale report is the event the product exists to catch. Today it crosses only with help from weak signals, so the verdict depends on noise such as the mislabelled ESG stories above. PM to decide; the rulebook stays at the v1 weights and threshold.
- **Lagging reports without a completion verb still count.** "Sale ... might be largest ever" (Axios, after the close) reads as an open sale and caused the NYC PREPARE on 2025-05-28.
- **Linker gaps.** "New York pensions" stays unlinked (city or state is unclear). A multi-employer plan called "NYC Pension" links to the city systems.
- **GDELT refuses often from this machine** (HTTP 429). Backoff keeps the run going on Google News alone.
- **Labels are agent-labelled, PM to review.**
- **A Claude batch run is slow:** about 3 s per linked item, 8 minutes for 155 items. Fine overnight; batch the calls if the schedule tightens.
