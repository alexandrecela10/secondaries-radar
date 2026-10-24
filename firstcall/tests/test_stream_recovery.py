"""Stream semantics on the file log: keys and partitions, offsets, at-least-once, idempotent sink, DLQ."""
from datetime import timedelta

from fc import pipeline, replay, store
from fc.classify.keyword import KeywordClassifier
from fc.classify.testing import FlakyClassifier
from fc.models import RawItem
from fc.stream import consumer
from fc.stream.log import FileLog, partition_for
from tests.conftest import T0

AS_OF = T0 + timedelta(days=10)


def gold(db):
    con = store.connect(db)
    out = con.execute("SELECT signal_id, signal_type, n_copies FROM gold_signals ORDER BY 1").fetchall()
    con.close()
    return out


def test_same_key_same_partition():
    assert partition_for("calpers") == partition_for("calpers")
    assert {partition_for(k) for k in ("calpers", "wsib", "nystrs", "psers", "unlinked", "swib")} <= {0, 1, 2}


def test_offsets_resume_after_restart(tmp_path, items):
    lg = FileLog(tmp_path / "log")
    replay.emit(items, 0, log_=lg, topic="t.raw")
    c = lg.consumer("t.raw", "g")
    for _ in range(3):
        c.commit(c.poll(0.2))
    c2 = lg.consumer("t.raw", "g")              # a new process reads the committed offsets
    seen = 0
    while c2.poll(0.2) is not None:
        seen += 1
    assert seen == len(items) - 3


def test_crash_before_commit_redelivers_and_sink_stays_idempotent(tmp_path, items):
    lg = FileLog(tmp_path / "log")
    replay.emit(items, 0, log_=lg, topic="t.raw")
    db = tmp_path / "s.duckdb"
    # process 4 records but commit only 3: the crash happens between sink write and commit
    con = store.connect(db)
    c = lg.consumer("t.raw", "g")
    for i in range(4):
        rec = c.poll(0.2)
        pipeline.process_one(con, RawItem.model_validate(rec.value), KeywordClassifier(), AS_OF, "stream")
        if i < 3:
            c.commit(rec)
    con.close()
    stats = consumer.run(lg, db, KeywordClassifier(), as_of=AS_OF, group="g", idle_exit=0.3,
                         topic="t.raw", dlq_topic="t.dlq")
    assert stats["processed"] == len(items) - 3            # record 4 was delivered twice
    ref = tmp_path / "ref.duckdb"
    rcon = store.connect(ref)
    pipeline.process_many(rcon, items, KeywordClassifier(), AS_OF, "batch")
    rcon.close()
    assert gold(db) == gold(ref)                            # 0 duplicates, 0 missed
    assert lg.committed("t.raw", "g") and sum(lg.committed("t.raw", "g").values()) == len(items)


def test_dead_letter_then_redrive(tmp_path, items):
    lg = FileLog(tmp_path / "log")
    replay.emit(items, 0, log_=lg, topic="t.raw")
    db = tmp_path / "s.duckdb"
    s = consumer.run(lg, db, FlakyClassifier(fail_every=2), as_of=AS_OF, group="g", idle_exit=0.3,
                     topic="t.raw", dlq_topic="t.dlq")
    assert s["dlq"] > 0 and s["processed"] == len(items)    # failures did not block the partition
    assert consumer.redrive_dlq(lg, topic="t.raw", dlq_topic="t.dlq") == s["dlq"]
    consumer.run(lg, db, KeywordClassifier(), as_of=AS_OF, group="g", idle_exit=0.3, topic="t.raw", dlq_topic="t.dlq")
    ref = tmp_path / "ref.duckdb"
    rcon = store.connect(ref)
    pipeline.process_many(rcon, items, KeywordClassifier(), AS_OF, "batch")
    rcon.close()
    assert gold(db) == gold(ref)


def test_torn_tail_line_is_not_read(tmp_path, items):
    lg = FileLog(tmp_path / "log")
    replay.emit(items[:1], 0, log_=lg, topic="t.raw")
    seg = next((tmp_path / "log" / "t.raw").glob("p*/*.seg"))
    with seg.open("a") as f:
        f.write('{"key": "x", "value": {"half')                # a crash mid-write
    c = lg.consumer("t.raw", "g")
    assert c.poll(0.2) is not None and c.poll(0.2) is None
