import random
from datetime import timedelta

from fc import pipeline, store
from fc.classify.keyword import KeywordClassifier
from fc.normalize import canonical_link, headline_key, near_identical
from tests.conftest import T0, raw


def test_canonical_link_drops_tracking_and_fragment():
    assert canonical_link("https://WWW.Ex.com/a/?utm_source=x&id=3#top") == "https://ex.com/a?id=3"


def test_near_identical_headlines():
    a = headline_key("CalPERS hires Evercore to run secondary sale of private equity stakes")
    b = headline_key("CalPERS hires Evercore to run a secondary sale of PE stakes")
    assert near_identical(a, b)
    assert not near_identical(a, headline_key("CalPERS names new chief investment officer"))


def test_syndicated_story_counts_once(tmp_path, items):
    con = store.connect(tmp_path / "t.duckdb")
    pipeline.process_many(con, items, KeywordClassifier(), T0 + timedelta(days=10), "batch")
    sale = con.execute("SELECT n_copies FROM gold_signals WHERE pension_id='calpers' AND signal_type='sale_or_advisor'").fetchall()
    assert sale == [(2,)]


def test_one_signal_per_type_per_day(tmp_path):
    rows = [raw("CalPERS sells private equity stakes to Lexington", "https://a.com/1", 0),
            raw("CalPERS offloads buyout fund interests in secondary deal", "https://b.com/2", 0)]
    con = store.connect(tmp_path / "t.duckdb")
    pipeline.process_many(con, rows, KeywordClassifier(), T0 + timedelta(days=1), "batch")
    assert con.execute("SELECT count(*) FROM gold_signals").fetchone()[0] == 1


def test_replaying_twice_changes_nothing(tmp_path, items):
    con = store.connect(tmp_path / "t.duckdb")
    as_of = T0 + timedelta(days=10)
    pipeline.process_many(con, items, KeywordClassifier(), as_of, "batch")
    first = con.execute("SELECT * EXCLUDE (computed_at) FROM gold_scores ORDER BY 1").fetchall()
    pipeline.process_many(con, items, KeywordClassifier(), as_of, "batch")
    assert con.execute("SELECT * EXCLUDE (computed_at) FROM gold_scores ORDER BY 1").fetchall() == first
    assert con.execute("SELECT count(*) FROM classified_items").fetchone()[0] == \
           con.execute("SELECT count(DISTINCT item_id) FROM classified_items").fetchone()[0]


def test_order_does_not_change_gold(tmp_path, items):
    as_of = T0 + timedelta(days=10)
    out = []
    for seed in (1, 2):
        con = store.connect(tmp_path / f"o{seed}.duckdb")
        shuffled = items[:]
        random.Random(seed).shuffle(shuffled)
        for r in shuffled:                         # one at a time, like the stream path
            pipeline.process_one(con, r, KeywordClassifier(), as_of, "stream")
        out.append(con.execute("SELECT signal_id, signal_type, n_copies FROM gold_signals ORDER BY 1").fetchall())
    assert out[0] == out[1]
