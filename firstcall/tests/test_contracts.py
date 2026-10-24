from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from fc import pipeline, store
from fc.classify.keyword import KeywordClassifier
from fc.config import load_rulebook
from fc.models import Classification, RawItem
from tests.conftest import T0


def test_quote_over_300_chars_is_rejected():
    with pytest.raises(ValidationError):
        Classification(signal_type="none", confidence=0.5, quote="x" * 301)


def test_bad_row_is_blocked_and_logged(tmp_path):
    con = store.connect(tmp_path / "t.duckdb")
    bad = RawItem(source="google_news", query="q", title="short", link="not a url", fetched_at=T0)
    pipeline.process_many(con, [bad], KeywordClassifier(), T0, "batch")
    assert con.execute("SELECT count(*) FROM silver_items").fetchone()[0] == 0
    assert con.execute("SELECT stage FROM rejected_rows").fetchall() == [("silver",)]


def test_bronze_keeps_no_article_body(tmp_path):
    con = store.connect(tmp_path / "t.duckdb")
    r = RawItem(source="gdelt", query="q", title="CalPERS cuts private equity target", link="https://ex.com/z",
                summary="s" * 2000, fetched_at=T0)
    pipeline.process_many(con, [r], KeywordClassifier(), T0, "batch")
    assert all(len(q) <= 300 for (q,) in con.execute("SELECT quote FROM silver_items").fetchall())
    assert all(len(raw) < 1000 for (raw,) in con.execute("SELECT raw::VARCHAR FROM bronze_items").fetchall())


def test_rulebook_rejects_unknown_signal(tmp_path):
    p = tmp_path / "rb.yaml"
    p.write_text("version: 2\nfirst_call:\n  prepare_threshold: 0.6\n  half_life_days: 90\n  window_days: 365\n"
                 "  story_window_days: 7\n  signal_weights: {sale_or_advisor: 1.0, vibes: 0.3}\n")
    with pytest.raises(ValueError, match="vibes.*Valid"):
        load_rulebook(p)
