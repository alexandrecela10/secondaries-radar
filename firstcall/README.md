# First Call (thin build)

First Call reads public news about US public pensions as it is published and flags pensions that show signs they may sell private equity stakes.
Each flag quotes its evidence: headline, outlet, date and link.
The verdict is about our preparation (PREPARE or WATCH), never a claim that a pension is selling.

## One-command demo

```bash
make setup     # once: Python 3.11 venv with uv
make demo      # live fetch from Google News RSS and GDELT, classify, score, weekly list + HTML + one evidence view
```

The HTML page lands in `reports/weekly_<date>.html`.

## Commands

| Command | What it does |
|---|---|
| `make collect-batch` | Batch path: fetch every source once, classify, score (`data/firstcall.duckdb`) |
| `make up` / `make down` | Start or stop Redpanda (Kafka API on `localhost:19092`) |
| `make collect-stream` | Stream path: poll once, publish events, consume until idle (`data/stream.duckdb`). Uses Redpanda if it is up, else the file log |
| `make test` | All unit tests and evals, offline, no API key needed |
| `make eval` | Classifier precision and recall on 130 labelled real headlines, with each error |
| `make cases` | Replay the two worked historical cases point in time |
| `make measure` | Batch vs stream experiment; raw results in `measurements/*.json` |
| `python -m fc.cli weekly --as-of 2026-10-24 --html` | Weekly list for any date |
| `python -m fc.cli why <pension_id>` | Each signal with quote, link, date, weight, confidence and decay |
| `python -m fc.cli rulebook` | The rulebook as sentences |
| `python -m fc.cli pensions` | The 61 pensions we link to, with aliases |
| `python -m fc.cli redrive-dlq` | Send dead-lettered events back to the stream |

Use `--db <path>` to point `weekly` or `why` at the stream sink (`data/stream.duckdb`).

## How it works

```
Google News RSS + GDELT  ->  bronze (as received)  ->  silver (typed, deduplicated, linked)  ->  classify  ->  gold (signals, scores)
                              |                                                                              |
                 batch: a job every N minutes                          stream: poller -> topic news.raw -> consumer group
```

- **Linking:** each headline links to at most one of 61 public pensions by alias, or to none. Decoys such as "Pension Bros" or the outlet "Pensions & Investments" never link.
- **Classify:** six labels from PRD F-2. The Claude adapter (`claude-sonnet-5-5`, structured output, schema-checked) runs when `ANTHROPIC_API_KEY` is set. Otherwise a deterministic keyword adapter runs and the report says so.
- **Score:** `score = 1 - exp(-S)`, `S = sum(weight x confidence x 0.5^(age_days / 90))`. PREPARE at 0.6 or more. Weights live in `config/rulebook.yaml`.
- **Dedupe:** near-identical headlines inside 7 days are one story; at most one signal per pension, type and day.
- **Point in time:** a score at date T uses only items published on or before T.

Architecture choice and numbers: `../outputs/decisions/first-call-collection-architecture.md`. Streaming concepts with pointers into the code: `LEARNING.md`.

## Data rules

- **Real public news only.** We store headline, link, outlet, publish time and a quote of 300 characters or less. The quote is the headline. We never fetch or store article text, and the bronze contract clips any source snippet to 300 characters.
- **No join with synthetic data (PRD D-3).** First Call never touches the synthetic portfolio. It holds no holdings and no prices for any real pension.
- **Wording.** PREPARE and WATCH describe our own preparation. Every page carries that disclaimer.
- **Rate limits.** GDELT allows one request every 5 seconds. We wait at least 5 seconds between calls and back off (10 s, 20 s, 40 s) on a refusal. Google News RSS is called once per query per run.
- **Out of git:** `data/` (bronze payloads, DuckDB files, stream logs) and `fixtures/frozen_corpus/*.jsonl`.

## Attribution

News discovery uses the GDELT Project DOC 2.0 API (https://www.gdeltproject.org/) and Google News RSS search. Headlines, outlets and links belong to their publishers.

## What is stubbed or simplified

- Labels on the 130 headlines are agent-labelled, PM to review. The dev half was used to tune the keyword rules; the holdout half was not.
- The Claude adapter is tested with a fake client only. No key was available in this build.
- The worked cases use a small frozen corpus: Google News RSS with date operators, plus 5 items found by web search.

## Known weaknesses

- **Lagging reports count as fresh.** A story about a sale that already closed scores like a new sale signal (NYC, August 2026, about the 2025 sale).
- **One sale report alone stays on WATCH.** Under rulebook v1 it scores 1 - e^-0.85 = 0.57. PM to decide whether a sale report should PREPARE on its own.
- **Linker gaps.** Short forms such as "Washington State" or "NYC Pensions" don't link, so the 2022 WSIB sale report was missed. The earliest name wins, which mislinks "former Maryland pension chief to lead IPERS".
- **GDELT refuses often from this machine** (HTTP 429), even at one request per 5 seconds. Backoff keeps the run going on Google News alone.
- **Worked cases gave 0 weeks of warning** (n = 2). By PRD section 11, First Call is presented as monitoring only.
