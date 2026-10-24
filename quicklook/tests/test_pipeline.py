"""Layers, idempotency, review queue, blocked rows, overrides, replay."""
import hashlib
from email.message import EmailMessage

import pytest

from quicklook import pipeline, store
from quicklook.parse import OfflineParser

P = OfflineParser()


def snapshot(ws):
    con = store.connect(ws, read_only=True)
    out = {}
    for t in ("silver_offers", "silver_offer_lines", "silver_rejects", "gold_line_matches", "gold_offer_metrics",
              "gold_exposure", "gold_rule_results", "gold_verdicts"):
        rows = con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall()
        out[t] = hashlib.sha256(repr(rows).encode()).hexdigest()
    out["bronze_count"] = con.execute("SELECT count(*) FROM bronze_emails").fetchone()[0]
    out["briefs"] = sorted((r[0], r[1]) for r in con.execute("SELECT path, sha256 FROM gold_briefs").fetchall())
    con.close()
    return out


def test_reingest_same_message_id_changes_nothing(env, fresh_ws):
    raw = env.offer_file("cedar").read_bytes()
    first = pipeline.process_email(raw, fresh_ws, P)
    before = snapshot(fresh_ws)
    again = pipeline.process_email(raw, fresh_ws, P)
    assert first.status == "done" and again.status == "duplicate"
    assert snapshot(fresh_ws) == before


def test_bronze_keeps_email_as_received(env, fresh_ws):
    raw = env.offer_file("maple").read_bytes()
    pipeline.process_email(raw, fresh_ws, P)
    con = store.connect(fresh_ws, read_only=True)
    r = store.one(con, "SELECT * FROM bronze_emails")
    con.close()
    assert (fresh_ws.bronze_dir / r["raw_path"]).read_bytes() == raw


def test_parse_failure_goes_to_review_queue(env, fresh_ws):
    r = pipeline.process_email(env.offer_file("thistle").read_bytes(), fresh_ws, P)
    assert r.status == "parse_failed" and r.verdict is None
    assert "Could not read this offer" in r.brief_path.read_text() and "Saved to review queue" in r.brief_path.read_text()
    con = store.connect(fresh_ws, read_only=True)
    assert con.execute("SELECT count(*) FROM review_queue").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM gold_verdicts").fetchone()[0] == 0
    con.close()


def _offer_email(lines: list[str]) -> bytes:
    m = EmailMessage()
    m["From"] = "x@broker.example"
    m["To"] = "y@ourfund.example"
    m["Subject"] = "Project Quince: sale"
    m["Date"] = "Fri, 23 Oct 2026 09:00:00 +0000"
    m["Message-ID"] = "<quince-1@broker.example>"
    m.set_content("Project Quince.\nReference date: 30 June 2026.\nPricing guidance: mid 80s.\n" + "\n".join(lines))
    return bytes(m)


def test_bad_row_is_blocked_and_logged(fresh_ws):
    raw = _offer_email(["Fund 1: US mid-market buyout, 2016, commitment $20M, 92% called, DPI 1.20x, TVPI 0.90x, NAV $10.0M",
                        "Fund 2: US mid-market buyout, 2018, commitment $20M, 90% called, DPI 0.50x, TVPI 1.50x, NAV $18.0M"])
    r = pipeline.process_email(raw, fresh_ws, P)
    assert r.status == "done"
    con = store.connect(fresh_ws, read_only=True)
    rej = store.rows(con, "SELECT * FROM silver_rejects")
    assert len(rej) == 1 and "below DPI" in rej[0]["reason"]
    assert con.execute("SELECT count(*) FROM silver_offer_lines").fetchone()[0] == 1
    con.close()
    assert "Rows blocked at validation" in r.brief_path.read_text()


def test_override_needs_reason_and_records_rulebook(env, fresh_ws):
    pipeline.process_email(env.offer_file("cedar").read_bytes(), fresh_ws, P)
    with pytest.raises(ValueError):
        pipeline.record_override(fresh_ws, "cedar", "pass", "", "alex")
    r = pipeline.record_override(fresh_ws, "cedar", "pass", "Seller will not move on price", "alex")
    assert r["original_verdict"] == "LOOK CLOSER" and r["new_verdict"] == "PASS"
    assert r["rulebook_version"] == "2" and r["rulebook_sha"]
    html = r["brief_path"].read_text()
    assert "Team override: PASS" in html
    con = store.connect(fresh_ws, read_only=True)
    from quicklook import brief
    assert brief.validate(html, lambda t, rid, m: store.lookup(con, t, rid, m)) == []
    con.close()


def test_replay_from_bronze_rebuilds_same_gold(env, fresh_ws):
    for c in ("cedar", "alder", "spruce"):
        pipeline.process_email(env.offer_file(c).read_bytes(), fresh_ws, P)
    before = snapshot(fresh_ws)
    res = pipeline.replay(fresh_ws, P)
    assert [r.status for r in res] == ["done"] * 3
    assert snapshot(fresh_ws) == before


def test_resume_after_crash_between_bronze_and_gold(env, fresh_ws):
    raw = env.offer_file("birch").read_bytes()
    con = store.connect(fresh_ws)
    from quicklook.emailio import read_email
    pipeline.record_bronze(con, fresh_ws, raw, read_email(raw), "smtp")  # crash right after bronze
    con.close()
    res = pipeline.resume_pending(fresh_ws, P)
    assert [r.status for r in res] == ["done"]
    assert pipeline.process_email(raw, fresh_ws, P).status == "duplicate"
