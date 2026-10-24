"""Event log port and two adapters with the same semantics.

- FileLog: append-only segment files per partition, consumer-group offsets committed to a file.
  Works without Docker. Single host, single consumer per group.
- KafkaLog: Redpanda (Kafka API) through confluent-kafka. Manual commits after the sink write.

Both give at-least-once delivery: a record is committed only after it is processed, so a crash
between processing and commit replays that record. The sink must be idempotent.
"""
from __future__ import annotations

import json
import os
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

TOPIC_RAW = os.environ.get("FC_TOPIC_PREFIX", "") + "news.raw"
TOPIC_DLQ = os.environ.get("FC_TOPIC_PREFIX", "") + "news.dlq"
PARTITIONS = 3


@dataclass(frozen=True)
class Record:
    topic: str
    partition: int
    offset: int
    key: str
    value: dict


class Producer(Protocol):
    def send(self, topic: str, key: str, value: dict) -> None: ...
    def flush(self) -> None: ...


class Consumer(Protocol):
    def poll(self, timeout: float = 1.0) -> Record | None: ...
    def commit(self, record: Record) -> None: ...
    def lag(self) -> int: ...
    def close(self) -> None: ...


class Log(Protocol):
    name: str
    def ensure_topic(self, topic: str, partitions: int = PARTITIONS) -> None: ...
    def producer(self) -> Producer: ...
    def consumer(self, topic: str, group: str) -> Consumer: ...
    def reset_offsets(self, topic: str, group: str) -> None: ...
    def end_offsets(self, topic: str) -> dict[int, int]: ...
    def committed(self, topic: str, group: str) -> dict[int, int]: ...


def partition_for(key: str, partitions: int = PARTITIONS) -> int:
    """Same key, same partition, so events for one pension stay in order."""
    return zlib.crc32(key.encode()) % partitions


# ---------------------------------------------------------------- file adapter
class FileLog:
    """data/stream_log/<topic>/p<n>/<base_offset>.seg holds JSON lines; offsets/<group>.json holds commits."""

    name = "file"
    SEGMENT_RECORDS = 500

    def __init__(self, root: Path):
        self.root = Path(root)

    def _pdir(self, topic: str, p: int) -> Path:
        return self.root / topic / f"p{p}"

    def ensure_topic(self, topic: str, partitions: int = PARTITIONS) -> None:
        for p in range(partitions):
            self._pdir(topic, p).mkdir(parents=True, exist_ok=True)
        (self.root / topic / "offsets").mkdir(parents=True, exist_ok=True)

    def partitions(self, topic: str) -> int:
        return len(list((self.root / topic).glob("p*")))

    def segments(self, topic: str, p: int) -> list[Path]:
        return sorted(self._pdir(topic, p).glob("*.seg"), key=lambda s: int(s.stem))

    def end_offsets(self, topic: str) -> dict[int, int]:
        out = {}
        for p in range(self.partitions(topic)):
            segs = self.segments(topic, p)
            if not segs:
                out[p] = 0
                continue
            last = segs[-1]
            with last.open("rb") as f:
                out[p] = int(last.stem) + sum(1 for _ in f)
        return out

    def producer(self) -> "FileProducer":
        return FileProducer(self)

    def consumer(self, topic: str, group: str) -> "FileConsumer":
        return FileConsumer(self, topic, group)

    def offsets_path(self, topic: str, group: str) -> Path:
        return self.root / topic / "offsets" / f"{group}.json"

    def reset_offsets(self, topic: str, group: str) -> None:
        self.offsets_path(topic, group).unlink(missing_ok=True)

    def committed(self, topic: str, group: str) -> dict[int, int]:
        path = self.offsets_path(topic, group)
        return {int(k): v for k, v in json.loads(path.read_text()).items()} if path.exists() else {}


class FileProducer:
    def __init__(self, log: FileLog):
        self.log = log
        self._ends: dict[tuple[str, int], int] = {}

    def send(self, topic: str, key: str, value: dict) -> None:
        n = self.log.partitions(topic) or PARTITIONS
        p = partition_for(key, n)
        end = self._ends.get((topic, p))
        if end is None:
            end = self.log.end_offsets(topic).get(p, 0)
        segs = self.log.segments(topic, p)
        if not segs or end - int(segs[-1].stem) >= FileLog.SEGMENT_RECORDS:
            seg = self.log._pdir(topic, p) / f"{end:012d}.seg"
        else:
            seg = segs[-1]
        line = json.dumps({"key": key, "value": value}, separators=(",", ":")) + "\n"
        with seg.open("a") as f:      # append-only: existing lines are never rewritten
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self._ends[(topic, p)] = end + 1

    def flush(self) -> None:
        pass


class FileConsumer:
    """Round-robins over partitions. Reads from the committed offset of its group."""

    def __init__(self, log: FileLog, topic: str, group: str):
        self.log, self.topic, self.group = log, topic, group
        path = log.offsets_path(topic, group)
        self.committed: dict[int, int] = {int(k): v for k, v in json.loads(path.read_text()).items()} if path.exists() else {}
        self.position = dict(self.committed)
        self._next_p = 0

    def _read_at(self, p: int, offset: int) -> dict | None:
        for seg in reversed(self.log.segments(self.topic, p)):
            base = int(seg.stem)
            if offset >= base:
                with seg.open() as f:
                    for i, line in enumerate(f):
                        if base + i == offset:
                            return json.loads(line) if line.endswith("\n") else None  # skip a torn tail
                return None
        return None

    def poll(self, timeout: float = 1.0) -> Record | None:
        import time

        deadline = time.monotonic() + timeout
        n = self.log.partitions(self.topic)
        while True:
            for i in range(n):
                p = (self._next_p + i) % n
                off = self.position.get(p, 0)
                rec = self._read_at(p, off)
                if rec is not None:
                    self.position[p] = off + 1
                    self._next_p = (p + 1) % n
                    return Record(self.topic, p, off, rec["key"], rec["value"])
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)

    def commit(self, record: Record) -> None:
        self.committed[record.partition] = record.offset + 1
        path = self.log.offsets_path(self.topic, self.group)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.committed))
        os.replace(tmp, path)        # atomic: a crash leaves the old or the new file, never half

    def lag(self) -> int:
        ends = self.log.end_offsets(self.topic)
        return sum(ends[p] - self.committed.get(p, 0) for p in ends)

    def close(self) -> None:
        pass


# --------------------------------------------------------------- kafka adapter
class KafkaLog:
    name = "redpanda"

    def __init__(self, bootstrap: str):
        self.bootstrap = bootstrap

    def _admin(self):
        from confluent_kafka.admin import AdminClient

        return AdminClient({"bootstrap.servers": self.bootstrap})

    def ensure_topic(self, topic: str, partitions: int = PARTITIONS) -> None:
        from confluent_kafka.admin import NewTopic

        admin = self._admin()
        if topic in admin.list_topics(timeout=10).topics:
            return
        for f in admin.create_topics([NewTopic(topic, num_partitions=partitions, replication_factor=1)]).values():
            try:
                f.result()
            except Exception as exc:  # already exists after a race
                if "TOPIC_ALREADY_EXISTS" not in str(exc):
                    raise

    def committed(self, topic: str, group: str) -> dict[int, int]:
        return _kafka_committed(self.bootstrap, topic, group)

    def producer(self) -> "KafkaProducer":
        return KafkaProducer(self.bootstrap)

    def consumer(self, topic: str, group: str) -> "KafkaConsumer":
        return KafkaConsumer(self.bootstrap, topic, group)

    def end_offsets(self, topic: str) -> dict[int, int]:
        from confluent_kafka import Consumer as CK, TopicPartition

        c = CK({"bootstrap.servers": self.bootstrap, "group.id": "fc-admin-probe"})
        meta = c.list_topics(topic, timeout=10).topics[topic]
        out = {p: c.get_watermark_offsets(TopicPartition(topic, p), timeout=10)[1] for p in meta.partitions}
        c.close()
        return out

    def reset_offsets(self, topic: str, group: str) -> None:
        from confluent_kafka import TopicPartition

        admin = self._admin()
        try:
            from confluent_kafka import ConsumerGroupTopicPartitions

            parts = [TopicPartition(topic, p, 0) for p in self.end_offsets(topic)]
            for f in admin.alter_consumer_group_offsets([ConsumerGroupTopicPartitions(group, parts)]).values():
                f.result()
        except Exception:
            for f in admin.delete_consumer_groups([group]).values():
                try:
                    f.result()
                except Exception:
                    pass


def _kafka_committed(bootstrap: str, topic: str, group: str) -> dict[int, int]:
    from confluent_kafka import Consumer as CK, TopicPartition

    c = CK({"bootstrap.servers": bootstrap, "group.id": group, "enable.auto.commit": False})
    parts = c.list_topics(topic, timeout=10).topics[topic].partitions
    out = {tp.partition: max(tp.offset, 0) for tp in c.committed([TopicPartition(topic, p) for p in parts], timeout=10)}
    c.close()
    return out


class KafkaProducer:
    def __init__(self, bootstrap: str):
        from confluent_kafka import Producer as CP

        # idempotent producer: broker drops duplicates caused by producer retries
        self.p = CP({"bootstrap.servers": bootstrap, "enable.idempotence": True, "linger.ms": 5})

    def send(self, topic: str, key: str, value: dict) -> None:
        self.p.produce(topic, key=key.encode(), value=json.dumps(value).encode())
        self.p.poll(0)

    def flush(self) -> None:
        self.p.flush(30)


class KafkaConsumer:
    def __init__(self, bootstrap: str, topic: str, group: str):
        from confluent_kafka import Consumer as CK

        self.topic = topic
        self.c = CK({"bootstrap.servers": bootstrap, "group.id": group, "enable.auto.commit": False,
                     "auto.offset.reset": "earliest", "session.timeout.ms": 6000})
        self.c.subscribe([topic])
        self.committed: dict[int, int] = {}

    def poll(self, timeout: float = 1.0) -> Record | None:
        m = self.c.poll(timeout)
        if m is None or m.error():
            return None
        return Record(m.topic(), m.partition(), m.offset(), (m.key() or b"").decode(), json.loads(m.value()))

    def commit(self, record: Record) -> None:
        from confluent_kafka import TopicPartition

        self.c.commit(offsets=[TopicPartition(record.topic, record.partition, record.offset + 1)], asynchronous=False)
        self.committed[record.partition] = record.offset + 1

    def lag(self) -> int:
        from confluent_kafka import TopicPartition

        total = 0
        for tp in self.c.assignment():
            hi = self.c.get_watermark_offsets(TopicPartition(tp.topic, tp.partition), timeout=5)[1]
            com = self.c.committed([TopicPartition(tp.topic, tp.partition)], timeout=5)[0].offset
            total += hi - max(com, 0)
        return total

    def close(self) -> None:
        self.c.close()


def get_log(kind: str | None = None) -> Log:
    """kind: 'redpanda', 'file', or None (auto: Redpanda if reachable, else file)."""
    from ..config import DATA_DIR, KAFKA_BOOTSTRAP

    kind = kind or os.environ.get("FC_LOG", "auto")
    if kind in ("redpanda", "auto"):
        import socket

        host, port = KAFKA_BOOTSTRAP.rsplit(":", 1)
        try:     # a TCP probe: quiet, and fast when Docker is down
            socket.create_connection((host, int(port)), timeout=1.0).close()
            return KafkaLog(KAFKA_BOOTSTRAP)
        except OSError:
            if kind == "redpanda":
                raise RuntimeError(f"Redpanda not reachable at {KAFKA_BOOTSTRAP}. Run `make up`.")
    return FileLog(Path(os.environ.get("FC_LOG_DIR", DATA_DIR / "stream_log")))


def iter_records(consumer: Consumer, idle_timeout: float) -> Iterator[Record]:
    while (rec := consumer.poll(idle_timeout)) is not None:
        yield rec
