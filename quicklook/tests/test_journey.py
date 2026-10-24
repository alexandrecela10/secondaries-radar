"""The associate journey: match side by side, resolve, keep, memo; tracing with a fake client; eval runner."""
from contextlib import contextmanager

import pytest

from quicklook import evals, journey, pipeline, store
from quicklook.parse import OfflineParser
from quicklook.tracing import Tracer

P = OfflineParser()


def _cedar(env, ws):
    pipeline.process_email(env.offer_file("cedar").read_bytes(), ws, P, tracer=Tracer(None))


def test_match_side_by_side_shows_distance_per_ratio(env):
    con = env.con()
    out = "\n".join(journey.match_lines(con, env.ref, "cedar", 4))
    con.close()
    assert "Halvane Capital Partners III" in out and "Score = 1 - mean distance = 0.98" in out
    for r in ("% called", "DPI", "TVPI", "value / commitment"):
        assert r in out
    assert "0.040" in out  # 0.4 points / (5 x 2 points)


def test_match_without_ratios_lists_candidates(env):
    con = env.con()
    out = "\n".join(journey.match_lines(con, env.ref, "cedar", 9))
    con.close()
    assert "no ratios" in out and "F012" in out and "radar resolve cedar 9" in out


def test_resolve_recomputes_and_records_who(env, fresh_ws):
    _cedar(env, fresh_ws)
    with pytest.raises(ValueError):
        pipeline.record_resolve(fresh_ws, "cedar", 9, "F999", "layla", Tracer(None))
    r = pipeline.record_resolve(fresh_ws, "cedar", 9, "F012", "layla", Tracer(None))
    assert r["before"]["data_matched"] < 0.95 and r["after"]["data_matched"] == pytest.approx(1.0)
    assert "every line identified" in r["after"]["headline"] and r["user_name"] == "layla"
    con = store.connect(fresh_ws, read_only=True)
    g = store.one(con, "SELECT * FROM gold_line_matches WHERE label = 'Fund 9'")
    assert g["tier"] == "same_manager" and g["resolved"] and g["method"] == "analyst pick by layla"
    b = store.one(con, "SELECT valid FROM gold_briefs")
    con.close()
    assert b["valid"] and "Matched by hand" in r["brief_path"].read_text()
    # a replay from bronze keeps the analyst's pick
    pipeline.replay(fresh_ws, P)
    con = store.connect(fresh_ws, read_only=True)
    assert store.one(con, "SELECT tier FROM gold_line_matches WHERE label = 'Fund 9'")["tier"] == "same_manager"
    con.close()


def test_keep_then_memo_passes_validator(env, fresh_ws):
    _cedar(env, fresh_ws)
    k = pipeline.record_keep(fresh_ws, "cedar", "layla", Tracer(None))
    assert k["verdict"] == "LOOK CLOSER" and k["rulebook_version"] == "2"
    assert "Team kept the verdict" in k["brief_path"].read_text()
    con = store.connect(fresh_ws, read_only=True)
    text, html, problems, n = journey.memo(con, "cedar")
    con.close()
    assert problems == [] and n >= 10
    assert "I keep this verdict" in text and "sign the NDA" in text and "—" not in text


def test_memo_after_override_proposes_decline(env, fresh_ws):
    _cedar(env, fresh_ws)
    pipeline.record_override(fresh_ws, "cedar", "pass", "Seller will not move on price", "layla", Tracer(None))
    con = store.connect(fresh_ws, read_only=True)
    text, _, problems, _ = journey.memo(con, "cedar")
    con.close()
    assert problems == [] and "override this to PASS" in text and "decline the NDA" in text


# ---------------------------------------------------------------- tracing with a fake client

class FakeObs:
    def __init__(self, log, name, trace_id):
        self.log, self.name, self.trace_id, self.id = log, name, trace_id, name

    def update(self, **kw):
        self.log.append(("update", self.name, kw))
        return self

    def score_trace(self, **kw):
        self.log.append(("score", kw["name"], kw["value"]))


class FakeLangfuse:
    """Records observations and scores the way the pipeline calls them."""

    def __init__(self):
        self.log, self.stack, self.scores = [], [], []

    def get_current_trace_id(self):
        return "t" * 32 if self.stack else None

    @contextmanager
    def start_as_current_observation(self, *, name, as_type="span", **kw):
        self.log.append(("start", name, as_type, kw))
        o = FakeObs(self.log, name, "t" * 32)
        self.stack.append(o)
        try:
            yield o
        finally:
            self.stack.pop()

    def create_score(self, **kw):
        self.scores.append(kw)

    def flush(self):
        pass


def test_one_trace_per_offer_with_steps_and_scores(env, fresh_ws):
    fake = FakeLangfuse()
    tr = Tracer(fake)
    pipeline.process_email(env.offer_file("cedar").read_bytes(), fresh_ws, P, tracer=tr)
    starts = [(e[1], e[2]) for e in fake.log if e[0] == "start"]
    assert starts == [("quick-look-offer", "span"), ("intake", "span"), ("parse", "span"), ("match", "retriever"),
                      ("metrics", "span"), ("verdict", "span"), ("brief", "span"), ("validator", "guardrail")]
    scores = {e[1]: e[2] for e in fake.log if e[0] == "score"}
    assert scores["validator_pass"] == 1 and scores["verdict_band"] == "medium" and 0.9 < scores["data_matched"] < 0.92
    # team feedback lands on the stored trace
    pipeline.record_override(fresh_ws, "cedar", "pass", "Seller will not move on price", "layla", tr)
    got = {s["name"]: s["value"] for s in fake.scores}
    assert got == {"team_decision": "override", "verdict_agrees": 0, "team_label": "PASS"}
    assert all(s["trace_id"] == "t" * 32 for s in fake.scores)


def test_claude_parse_is_a_generation_with_tokens(env, fresh_ws):
    from types import SimpleNamespace

    from .test_parse import FakeClient, _llm_offer
    from quicklook.parse.claude import ClaudeParser

    client = FakeClient(_llm_offer())
    real = client.beta.messages.parse
    client.beta.messages.parse = lambda **kw: SimpleNamespace(**vars(real(**kw)), model="claude-sonnet-5-5",
                                                              usage=SimpleNamespace(input_tokens=3000, output_tokens=900))
    fake = FakeLangfuse()
    pipeline.process_email(env.offer_file("cedar").read_bytes(), fresh_ws, ClaudeParser(client=client),
                           tracer=Tracer(fake))
    start = next(e for e in fake.log if e[0] == "start" and e[1] == "parse")
    assert start[2] == "generation" and start[3]["metadata"]["prompt_version"] == "parse-v2"
    upd = next(e[2] for e in fake.log if e[0] == "update" and e[1] == "parse")
    assert upd["usage_details"] == {"input": 3000, "output": 900} and upd["model"] == "claude-sonnet-5-5"


def test_tracing_off_is_silent(env, fresh_ws):
    r = pipeline.process_email(env.offer_file("cedar").read_bytes(), fresh_ws, P, tracer=Tracer(None))
    con = store.connect(fresh_ws, read_only=True)
    assert r.status == "done" and con.execute("SELECT count(*) FROM offer_traces").fetchone()[0] == 0
    con.close()


# ---------------------------------------------------------------- eval runner (local mode)

def test_eval_runner_local_matches_pytest_evals(env, tmp_path):
    rep = evals.run("offline", local=True, data_dir=env.data, out_dir=tmp_path)
    assert rep["mode"] == "local" and rep["n_offers"] == len(env.labels["offers"])
    assert rep["lines"]["field_accuracy_mean"] == pytest.approx(1.0)
    assert rep["lines"]["match_accuracy"] >= 0.9
    assert rep["offers"]["verdict_agreement_tune"] >= 0.8
    assert "verdict_agreement_holdout" in rep["offers"]
    assert (tmp_path / "eval_offline.json").exists()


def test_line_items_carry_line_text(env):
    lines, offers = evals.build_items(env.data)
    first = next(l for l in lines if l["id"] == "line-cedar-04")
    assert first["input"]["line_text"].startswith("Fund 4:") and first["expected_output"]["true_fund_id"]
    assert {o["metadata"]["split"] for o in offers} == {"tune", "holdout"}
