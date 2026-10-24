"""Offline classifier on the labelled real headlines. Labels: agent-labelled, PM to review."""
from fc.classify.keyword import KeywordClassifier
from fc.evals import evaluate, load_labels


def test_labelled_set_is_real_and_marked():
    rows = load_labels()
    assert len(rows) >= 50
    assert all(r["label_status"] == "agent-labelled, PM to review" for r in rows)
    assert all(r["link"].startswith("https://") for r in rows)


def test_precision_meets_target_on_holdout():
    r = evaluate(KeywordClassifier(), "holdout")
    print(f"holdout precision {r.precision:.2f} recall {r.recall:.2f}")
    assert r.precision >= 0.80


def test_precision_and_recall_all():
    r = evaluate(KeywordClassifier())
    print(f"all precision {r.precision:.2f} recall {r.recall:.2f} link {r.link_accuracy:.2f}")
    assert r.precision >= 0.80
    assert r.recall >= 0.75          # regression guard, not a PRD target
    assert r.link_accuracy >= 0.95


def test_prd_examples():
    k = KeywordClassifier()
    assert k.classify("State pension board to review private equity allocation after it climbs to 14% "
                      "against a 10% target").signal_type == "allocation_above_target"
    assert k.classify("Pension's private equity returns trail public markets").signal_type == "none"
    assert k.classify("CalPERS commits $500m to secondaries fund").signal_type == "none"   # buyer side
