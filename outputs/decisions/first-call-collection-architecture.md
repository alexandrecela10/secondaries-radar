# Decision: Seller Watch collects news in batch

**Date:** 2026-10-24
**Owner:** Alexandre Cela
**Status:** Proposed, measured. PM to confirm.
**PRD:** `outputs/prds/secondaries-radar-solution-review.md`, section 9
**Plan:** `outputs/analyses/first-call-batch-vs-stream-plan.md`
**Raw results:** `firstcall/measurements/*.json`

## Decision

Seller Watch runs as a scheduled batch job every 15 minutes. The stream path (Redpanda, consumer group, dead-letter topic) stays in the repo as a tested, measured alternative. It is not the default.

## Why

- **Both paths pass criteria 1 to 3.** The rule picks the simplest one, so batch wins on moving parts (1 against 3).
- **Freshness is capped by the source, not by us.** GDELT refreshes every 15 minutes. The freshest Google News item in a pull was 4.6 hours old. Pensions publish board papers days before a meeting.
- **The decision window is 4 weeks.** Batch p95 at a 15-minute schedule is about 0.24 hours. The limit is 67.2 hours. Streaming's gain, under a second, changes nothing a partner does that week.
- **Stream costs more to run and to own.** It needs 7 times the path-specific code and 2,160 always-on hours a month (3 processes), against about 11 hours for batch.

## Measured table

Same input for both paths: 928 real items (live Google News RSS pulls plus the frozen worked-case corpus), replayed in event-time order at 20 items per second. Same processing code (`firstcall/fc/pipeline.py`). Stream path ran on Redpanda v24.2.7 in Docker. Classifier: offline keyword adapter in both paths.

| # | Criterion | Stream | Batch | Pass rule |
|---|---|---|---|---|
| 1 | p95 latency, item to verdict row | 0.03 s measured | 5.7 s measured at a 5 s schedule; about 0.24 h projected at 15 min | Under 10% of 4 weeks (67.2 h), source cadence (0.25 h) added: both pass |
| 2 | Output parity on replay | 40 signals, 42 pension scores | Identical, 0 differences | Identical: pass |
| 3 | Kill mid-run (SIGKILL), restart | 0 duplicates, 0 missed; first record 5.3 s after restart (group rebalance) | 0 duplicates, 0 missed | 0 and 0: both pass |
| 4 | Moving parts | 3: poller, consumer, broker | 1: scheduled job | Fewer wins |
| 5 | Path-specific lines of code | 376 | 54 | Context |
| 6 | Always-on hours per month | 2,160 | about 11 (2,880 runs x 13 s) | Context |
| 7 | Replay: rebuild all outputs from bronze | 12.6 s (new group from offset 0) | 10.1 s (one pass) | Context; both rebuild identical gold |

**Also measured:**

- **Out-of-order delivery:** a shuffled replay produced identical gold, because each event recomputes its pension from the full stored history.
- **Dead-letter topic:** a classifier that fails 1 in 7 items sent 22 events to `news.dlq` without blocking. A redrive restored parity.
- **Backpressure:** at 10 events per second against a 1-second classifier, lag peaked at 55 events and drained in 63 s. With the model on every linked item, model time dominates both paths equally.

## What this decision does not settle

- **Classifier:** all runs used the keyword adapter. A Claude run will add about 1 s per linked item to both paths. It doesn't change the ranking.
- **Single host:** the file log and the DuckDB sink are single-writer. A shared deployment would need a server database.
- **Worked cases:** neither case gave 8 weeks of warning (WSIB 2022: 0 weeks; NYC 2025: 0 weeks). By PRD section 11, Seller Watch is shown as monitoring only. That finding doesn't depend on the collection path.

## Where streaming would earn its place at a sovereign fund

Streaming pays when a decision changes within seconds or minutes of an event.

- **Intraday risk on public portfolios:** market data drives limits and hedges during the trading day.
- **Treasury cash positions:** payment and settlement events move liquidity buffers within hours.
- **Trade surveillance:** a pattern has to be flagged before the next order goes out, not at the end of the day.

In each case the source emits events continuously and the decision window is shorter than any batch schedule. Seller Watch has the opposite profile: a source that refreshes every 15 minutes and a decision window of weeks.

## Revisit when

- A source starts pushing events (for example a paid news wire with a webhook) **and** a use case needs action within an hour.
- Seller Watch feeds an alert that someone must act on the same day.
