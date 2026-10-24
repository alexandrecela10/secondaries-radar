"""The partner journey commands, offline (outputs/journey-maps/first-call-partner-journey.md)."""
from datetime import datetime, timedelta, timezone

import pytest

from fc import journey, pensions, pipeline, report, store, tracing
from fc.cli import main
from fc.classify.keyword import KeywordClassifier
from tests.conftest import T0, raw

AS_OF = T0 + timedelta(days=10)


@pytest.fixture
def con(tmp_path, items):
    c = store.connect(tmp_path / "j.duckdb")
    pipeline.process_many(c, items + [raw("Blackstone acquires $2bn PE portfolio from PSERS", "https://ex.com/z", 4)],
                          KeywordClassifier(), AS_OF, "batch")
    return c


@pytest.mark.parametrize("name, pid", [("nyc_funds", "nyc_funds"), ("WSIB", "wsib"), ("Washington State", "wsib"),
                                       ("CalPERS", "calpers"), ("nyc", "nyc_funds"), ("PSERS", "psers")])
def test_resolve_names(name, pid):
    assert journey.resolve(name) == pid


def test_resolve_unknown():
    assert journey.resolve("Pension Bros") is None


def test_timeline_shows_line_and_dated_signals(con):
    out = journey.timeline_text(con, "calpers", AS_OF, weeks=4)
    assert "PREPARE at 0.6" in out and "|" in out
    assert "2026-10-01 sale_or_advisor" in out          # signal on the week it was published
    assert out.count("\n2026-") >= 4


def test_act_and_dismiss_are_recorded_with_who_when_version(con, monkeypatch):
    monkeypatch.setenv("FC_USER", "omar")
    a = journey.record_decision(con, "calpers", "prepare", owner="layla", as_of=AS_OF)
    assert journey.last_decision(con, "calpers", T0) is None      # decided after T0: not shown at T0
    d = journey.record_decision(con, "psers", "dismiss", reason="board review is routine", as_of=AS_OF)
    assert a["decided_by"] == d["decided_by"] == "omar" and a["rulebook_version"] == 2
    assert a["sent_to_langfuse"] is False                # tracing off in tests: stored locally only
    rows = con.execute("SELECT pension_id, action, owner, reason, decided_by, rulebook_version FROM decisions "
                       "ORDER BY pension_id").fetchall()
    assert rows == [("calpers", "prepare", "layla", None, "omar", 2),
                    ("psers", "dismiss", None, "board review is routine", "omar", 2)]
    assert "[prepare: layla" in report.weekly_text(con, datetime.now(timezone.utc))


def test_dismiss_needs_a_reason(con):
    with pytest.raises(ValueError):
        journey.record_decision(con, "psers", "dismiss", reason="  ")


def test_weekly_shows_closed_sale_as_evidence_only(con):
    out = report.weekly_text(con, AS_OF)
    assert "Closed sales in the last 90 days (evidence only, not scored): 1" in out
    assert "No pension crossed PREPARE" in out or "on PREPARE" in out


def test_anon_weekly_hides_names_and_links(con):
    out = report.weekly_text(con, AS_OF, anon=True)
    assert "Pension A" in out and "https://" not in out
    for name in ("CalPERS", "PSERS", "NYSTRS", "calpers", "psers"):
        assert name not in out, name


def test_mask_replaces_short_forms():
    assert pensions.mask("NYC’s pension sells PE to Blackstone") == "[Pension] sells PE to Blackstone"
    assert pensions.mask("Washington State gears up for sale") == "[Pension] gears up for sale"


def test_cli_commands_as_written(tmp_path, items, capsys, monkeypatch):
    db = str(tmp_path / "cli.duckdb")
    c = store.connect(db)
    pipeline.process_many(c, items, KeywordClassifier(), AS_OF, "batch")
    c.close()
    monkeypatch.setenv("FC_USER", "omar")
    for argv in (["weekly"], ["why", "calpers"], ["timeline", "calpers"], ["act", "calpers", "prepare", "--owner", "layla"],
                 ["dismiss", "psers", "--reason", "routine review"]):
        assert main(["--db", db, *argv]) in (0, None)
    out = capsys.readouterr().out
    assert "Recorded prepare, owner layla for calpers by omar" in out
    assert "Recorded dismiss: routine review for psers by omar" in out


def test_tracing_is_a_silent_noop_without_keys():
    assert tracing.enabled() is False and tracing.client() is None
    with tracing.observe("classify-headline", as_type="generation") as obs:
        obs.update(output={"x": 1})
    assert tracing.score_trace("abc", "partner_decision", "prepare", data_type="CATEGORICAL") is False
