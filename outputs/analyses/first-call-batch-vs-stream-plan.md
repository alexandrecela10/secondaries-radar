# Seller Watch: batch vs stream learning spike

**Date:** 2026-10-24
**Owner:** Alexandre Cela
**Status:** Plan
**Timebox:** half a day. Ship batch afterwards.

## Why

- **Learning goal:** the PM builds a working stream pipeline and can explain its moving parts.
- **Decision goal:** measure what streaming buys Seller Watch, then record batch vs stream as a decision with numbers.

## Hypothesis

If both paths process the same signals, then streaming cuts alert latency by 15 minutes or less, because the news index itself publishes in 15-minute batches and board materials appear days before meetings. The decision window is weeks, so no alert changes outcome. Streaming adds a broker, offsets and window state to run.

## Setup

One input, two paths, one comparison.

```
replay producer (historical signals, event-time order, 1 day = 1 s)
live poller (news index every 15 min)
        |                                   |
   bronze table (DuckDB)              topic: signals.raw (Redpanda, keyed by pension)
        |                                   |
   batch job every N min              stream consumer (Quix Streams)
   classify -> score -> alert         classify -> 90-day window per pension -> alert
        |                                   |
        +-------------> alerts table <------+   (dedupe key: source URL hash)
```

- **Broker:** Redpanda in one Docker container. Kafka API, no JVM or ZooKeeper.
- **Stream library:** Quix Streams. Python, stateful windows, Kafka client underneath.
- **Classifier:** the same function in both paths, so differences come from the pipeline, not the model.

## What the PM learns, step by step

| Step | Concept | Exercise |
|---|---|---|
| 1 | Topic, partition, key | Key by pension. Explain why all signals for one pension must land on one partition. |
| 2 | Consumer group, offsets | Kill the consumer, restart, see where it resumes. |
| 3 | At-least-once delivery, idempotent sink | Force a redelivery. The URL-hash key keeps the alert count unchanged. |
| 4 | Event time vs processing time | Replay out of order. Compare window results with and without a grace period. |
| 5 | Windowed state | 90-day signal score per pension, persisted in the state store. |
| 6 | Dead-letter topic | Classifier failures go to `signals.dlq`, not lost, not blocking. |
| 7 | Backpressure | The model call (about 1 s per item) is the bottleneck. Watch consumer lag grow. |
| 8 | Replay and backfill | Reset offsets to 0 and rebuild all alerts. Time it against the batch rerun. |

## Measurements

| Measure | Method | Expected |
|---|---|---|
| Alert parity | Diff both alert tables after replay | Identical. Any gap is a bug to explain. |
| Latency p50 / p95 | Source publish time to alert row | Stream seconds, batch up to N min. Both capped by the 15-min source cadence. |
| Latency vs decision window | p95 latency / median lead time | Under 1% for both paths |
| Moving parts | Processes and containers to run | Batch 1, stream 3 or more |
| Code size | Lines per path, excluding the shared classifier | Stream larger |
| Crash recovery | Kill mid-run; count duplicate and missed alerts | 0 and 0 for both if the sink is idempotent |
| Backfill time | Rebuild 3 years of alerts | Similar; the model calls dominate |
| Running cost | Always-on hours vs scheduled hours per month | Stream about 720 h, batch under 10 h |

## Decision rule

Choose stream only if a latency gain changes an alert's usefulness. That would need a decision window shorter than the source cadence. Otherwise choose batch and file `outputs/decisions/first-call-batch-vs-stream.md`.

## Where streaming would earn its place at a sovereign fund

Market data and intraday risk on public portfolios, treasury cash positions, trade surveillance. In each one, latency in seconds changes a decision.
