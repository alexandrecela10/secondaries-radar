"""Rulebook: score, veto, not checked, confidence, load errors, labelled-offer outcomes."""
from pathlib import Path

import pytest

from quicklook import rulebook as R
from quicklook.cli import agreement
from quicklook.config import CONFIG_DIR, RULEBOOK_PATH
from quicklook import store

from .conftest import EVALS

RB = R.load(RULEBOOK_PATH)
RB_V2 = R.load(CONFIG_DIR / "history" / "rulebook_v2.yaml")
BASE = dict(share_value_venture=0.12, share_value_known_manager=0.78, fit_score=0.82,
            guidance_minus_range_top=0.03, asking_price_mid=0.88, price_range_high=0.85,
            data_matched=0.91, count_unresolved=1, share_value_unresolved=0.09)


def test_cedar_like_offer_v2_price_is_a_rule():
    v = R.evaluate(RB_V2, BASE)
    assert v.verdict == "LOOK CLOSER"
    assert v.score == pytest.approx(6 / 8)
    assert v.band == "medium"  # data matched strong, margin 0.15 is medium
    assert v.price_view == "expensive"


def test_cedar_like_offer_v3_price_is_its_own_question():
    v = R.evaluate(RB, BASE)  # asking 3 points above our top: high, but inside the 10-point veto gap
    assert v.verdict == "LOOK CLOSER" and v.price_view == "expensive" and v.veto_rule is None
    assert v.score == pytest.approx(1.0)  # price is no longer in the fit score
    assert v.band == "high"
    assert v.headline == ("LOOK CLOSER: fits our rules, but the price looks high by our own data. "
                          "High confidence. Still open: 1 fund (9% of value) not identified.")
    assert [r.rule_id for r in v.reasons][0] == "no_venture_heavy"  # veto first, then by weight
    assert [r.weight for r in v.reasons[1:]] == sorted([r.weight for r in v.reasons[1:]], reverse=True)


def test_price_looks_right_when_ask_is_inside_our_range():
    v = R.evaluate(RB, dict(BASE, guidance_minus_range_top=-0.02, price_range_high=0.90))
    assert v.verdict == "LOOK CLOSER" and v.price_view == "cheap"
    assert "and the price looks right by our own data" in v.headline


def test_price_veto_passes_an_offer_that_fits():
    v = R.evaluate(RB, dict(BASE, guidance_minus_range_top=0.11, price_range_high=0.77))
    assert v.verdict == "PASS" and v.veto_rule == R.PRICE_VETO and v.margin is None
    assert v.headline == "PASS: the seller asks about 88% of value, and by our own data we would pay at most 77%."


def test_no_fund_we_own_means_no_price_view():
    v = R.evaluate(RB, dict(BASE, guidance_minus_range_top=None, price_range_high=None))
    assert v.price_view == "no view" and v.verdict == "LOOK CLOSER"
    assert "we have no price view" in v.headline


def test_veto_blocks():
    v = R.evaluate(RB, dict(BASE, share_value_venture=0.45))
    assert v.verdict == "PASS" and v.veto_rule == "no_venture_heavy" and v.margin is None
    assert v.headline == 'PASS: fails "Venture is under 30% of value" (45%).'


def test_not_checked_is_excluded_and_lowers_confidence():
    v = R.evaluate(RB, dict(BASE, fit_score=None))
    assert next(r for r in v.reasons if r.rule_id == "plan_fit").status == "not checked"
    assert v.score == pytest.approx(1.0)  # 4 of 4 checked weight
    assert v.band in ("medium", "low") and "could not be checked" in v.headline


def test_nothing_matched_is_low_confidence():
    v = R.evaluate(RB, dict(BASE, share_value_known_manager=0.0, data_matched=1.0))
    assert v.band == "low" and "we know none of these funds" in v.headline


def test_unknown_metric_fails_load_and_names_rule(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(RULEBOOK_PATH.read_text().replace("metric: fit_score", "metric: irr_net"))
    with pytest.raises(R.RulebookError) as e:
        R.load(p)
    assert "plan_fit" in str(e.value) and "irr_net" in str(e.value) and "share_value_venture" in str(e.value)


def test_print_as_sentences():
    s = "\n".join(R.as_sentences(RB))
    assert "Veto: if this fails" in s and "60% or more" in s and "more than 10 points above" in s


def _verdicts(env, rb):
    con = env.con()
    out = {}
    for o in env.labels["offers"]:
        off = store.resolve_offer(con, o["code"])
        m = {r["metric"]: r["value"] for r in store.rows(con, "SELECT metric, value FROM gold_offer_metrics WHERE offer_id = ?", [off["offer_id"]])}
        out[o["code"]] = R.evaluate(rb, m)
    con.close()
    return out, {o["code"]: o for o in env.labels["offers"]}


def test_eval_labelled_offers(env):
    """Gate on the tune split (what v2 was tuned on). The holdout is reported, never gated:
    a failing gate on the holdout would invite tuning on it."""
    v, labels = _verdicts(env, RB)
    kept, n, good_passed = agreement(v, labels, "tune")
    hk, hn, hgp = agreement(v, labels, "holdout")
    v1, _ = _verdicts(env, R.load(CONFIG_DIR / "history" / "rulebook_v1.yaml"))
    k1, _, gp1 = agreement(v1, labels, "tune")
    hk1, _, hgp1 = agreement(v1, labels, "holdout")
    misses = sorted(c for c in v if labels[c]["is_test_offer"]
                    and (v[c].verdict == "LOOK CLOSER") != (labels[c]["pm_label"] == "LOOK"))
    EVALS["verdicts_vs_pm_labels"] = {
        "rulebook": RB.version, "tune": {"kept": kept, "total": n, "share": kept / n, "good_offers_passed": good_passed},
        "holdout": {"kept": hk, "total": hn, "share": hk / hn, "good_offers_passed": hgp},
        "target": 0.8, "rulebook_v1": {"tune_kept": k1, "tune_good_passed": gp1, "holdout_kept": hk1,
                                       "holdout_good_passed": hgp1},
        "disagreements": {c: {"label": labels[c]["pm_label"], "verdict": v[c].verdict, "split": labels[c]["split"],
                              "archetype": labels[c]["archetype"], "score": v[c].score, "veto": v[c].veto_rule}
                          for c in misses}}
    # Guardrail: good offers passed. v3 passes two the builder labelled LOOK, both on the tune split,
    # named here so any new one fails the gate. The PM decides (outputs/prds, decision D5):
    #   Poplar: v2 said LOOK only because the price rule carried its score; we know 22% of its value.
    #   Myrtle: our reports show its funds down 6% since June; at par we would pay 17 points over our top.
    passed = {c for c, d in EVALS["verdicts_vs_pm_labels"]["disagreements"].items()
              if d["label"] == "LOOK" and d["split"] == "tune"}
    assert passed <= {"Poplar", "Myrtle"} and good_passed == len(passed)
    assert kept / n >= 0.8


def test_rules_diff_sentence(env):
    v2, _ = _verdicts(env, RB_V2)
    v3, _ = _verdicts(env, RB)
    lines = R.diff_sentences(v2, v3)
    assert any(l.startswith("Hornbeam flips to PASS") for l in lines)
