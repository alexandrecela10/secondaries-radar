# Streaming, learned on First Call

Eight concepts, each tied to the file and function that implements it. Run `make up`, then `make measure`, and read along. The numbers come from `measurements/*.json`.

The picture to keep in mind:

```
poller  --(event, key = pension)-->  topic news.raw (3 partitions)  -->  consumer group "firstcall-classifier"
                                                                            |  classify -> sink (DuckDB, upserts)
                                                                            |  commit offset after the sink write
                                                                            +--> news.dlq on classifier failure
```

## 1. Keys and partitions

**What it is.** A topic is split into partitions. Each event has a key. The same key always goes to the same partition, and order is kept only inside one partition.

**Here.** `fc/stream/poller.py: event_key` sets the key to the linked pension id, or `unlinked`. `fc/stream/log.py: partition_for` hashes the key to a partition. Redpanda uses its own hash; the rule is the same.

**Worked example.** Three CalPERS headlines arrive. All three land on one partition, so the consumer sees them in order and rebuilds CalPERS once per event with the right history.

**What we saw.** On the first replay, 623 of 725 events landed on partition 2. That is the `unlinked` key: most news mentions no pension. A hot key caps parallelism, because one partition is read by one consumer. A fix would be to drop unlinked items at the poller or key them by outlet.

## 2. Consumer groups and offsets

**What it is.** Consumers that share a group name split the partitions between them. Each group stores, per partition, the offset of the next record to read. A new process in the same group resumes from there.

**Here.** `fc/stream/consumer.py: run` joins group `firstcall-classifier`. `fc/stream/log.py: KafkaConsumer` sets `enable.auto.commit` to false. `FileConsumer.commit` writes offsets to `offsets/<group>.json` with an atomic rename. Test: `tests/test_stream_recovery.py: test_offsets_resume_after_restart`.

**What we saw.** After a SIGKILL, the restarted Redpanda consumer read its first record 5.3 s later. The broker waits for the dead member's session (6 s) to expire, then rebalances. Our first harness gave up after 4 s idle and reported 31 missed signals. The data was fine; the harness was wrong.

## 3. At-least-once delivery and an idempotent sink

**What it is.** The consumer commits only after the sink write. A crash between the write and the commit replays that record. So every record is processed at least once, and sometimes twice. The sink must make the second write a no-op.

**Here.** `fc/stream/consumer.py: run` calls `pipeline.process_one`, then `consumer.commit`. Every write in `fc/store.py` is an upsert on a natural key (`item_id`, `signal_id`, `(pension_id, as_of)`). `fc/pipeline.py: ingest` skips the model call when the item is already classified, so a redelivery costs nothing.

**Worked example.** `tests/test_stream_recovery.py: test_crash_before_commit_redelivers_and_sink_stays_idempotent` processes 4 records and commits 3. Record 4 is delivered again. Gold matches the batch reference exactly.

## 4. Event time and processing time

**What it is.** Event time is when the news was published. Processing time is when we saw it. They differ, and items can arrive out of order.

**Here.** `fc/models.py: NewsItem` keeps `published_at` (event time) and `fetched_at` (processing time). `fc/scoring.py: contribution` ages a signal from `published_at` and ignores anything published after the as-of date. `fc/store.py: rebuild_pension` recomputes a pension from its full stored history on every event.

**Worked example.** `fc/measure.py: run_out_of_order` delivers the replay in random order. Gold is identical. We need no grace period, because we never close a window: each event triggers a recompute from state. Stream engines that emit a window result once (Flink, Quix) need a grace period instead, and late events after it are dropped or sent elsewhere.

## 5. Windowed state: the 90-day decay

**What it is.** State is what the consumer remembers between events. A window limits that state in time.

**Here.** The state is the set of signals per pension in `gold_signals`. `fc/scoring.py: decay` halves a signal's weight every 90 days. `window_days: 365` in `config/rulebook.yaml` drops anything older (weight under 6%). This is a sliding window with exponential decay, not a fixed bucket.

**Worked example.** A sale report with confidence 0.85, 58 days old: 1.0 x 0.85 x 0.5^(58/90) = 0.54. That is the top line of `python -m fc.cli why nyc_funds`.

## 6. Dead-letter topic

**What it is.** A record that fails processing goes to a separate topic. The main partition keeps moving, and nothing is lost.

**Here.** `fc/stream/consumer.py: run` catches `ClassifierError` (timeout, refusal, schema check failure from `fc/classify/claude.py`), sends the record to `news.dlq` with the error, and commits. `redrive_dlq` sends them back after the cause is fixed. Any other error stops the consumer without a commit, so the record is replayed.

**What we saw.** A classifier that fails 1 in 7 items dead-lettered 22 events. 7 signals were missing. After the redrive, gold matched the reference (`measurements/dead_letter.json`).

## 7. Backpressure: the model call is the bottleneck

**What it is.** When events arrive faster than the consumer can process them, lag grows. Lag is end offset minus committed offset.

**Here.** `fc/measure.py: run_backpressure` sends 60 linked items at 10 per second to a consumer whose classifier takes 1 s (`fc/classify/testing.py: SlowClassifier`).

**What we saw.** Lag peaked at 55 and drained in 63 s (`measurements/backpressure.json`). With a 4-week decision window that is harmless. Fixes when it matters: more partitions and consumers, batching model calls, or filtering before the model (we already skip unlinked items).

## 8. Replay and backfill

**What it is.** Because the log keeps every event, a new consumer group can read from offset 0 and rebuild every output. That is how you backfill after a rule change.

**Here.** `fc/replay.py: emit` sends stored items in event-time order to the topic and to the batch source file at the same moment. `fc/measure.py: run_replay_time` rebuilds from offset 0 with a new group. `fc/cases.py: replay` replays a frozen historical corpus day by day.

**What we saw.** Rebuild from bronze: stream 12.6 s, batch 10.1 s, identical gold. The batch path can do the same from the bronze table, so replay is not a reason to pick streaming here.

## Quiz

1. Why do all events for one pension need the same key?
2. The consumer writes a signal, then crashes before the commit. What happens on restart, and why is the score still right?
3. Our first recovery run reported 31 missed signals on Redpanda but 0 on the file log. What caused it?
4. A headline published on 1 March arrives on 10 March. Which date does the score use, and where in the code?
5. Streaming cut latency from minutes to milliseconds. Why did batch still win for First Call?

## Answers

1. A key maps to one partition, and order holds only inside a partition. Same key, so one pension's events are processed in order by one consumer.
2. The record is delivered again (at-least-once). The sink upserts on `item_id` and `signal_id`, and the classification is cached, so the second pass writes nothing new.
3. The dead consumer's group membership lasted until its 6-second session timed out, then the group rebalanced. The restarted consumer gave up after 4 idle seconds, before it was assigned any partition.
4. Event time, 1 March. `fc/scoring.py: contribution` computes age from `published_at`; `fetched_at` is kept only for latency.
5. Both paths passed freshness, parity and recovery. The source refreshes every 15 minutes and the decision window is 4 weeks, so the gain changes no decision. The rule then picks fewer moving parts: 1 against 3.
