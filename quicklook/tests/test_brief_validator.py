"""C-2: every number in a brief carries (table, row id, metric, version) and matches its source."""
import pytest

from quicklook import brief, store

from .conftest import EVALS


def lookup_from(values):
    def f(t, r, m):
        return values[(t, r, m)]
    return f


SRC = {("gold_offer_metrics", "o:share", "share"): 0.6812, ("silver_offers", "o", "asking_price_text"): "high 80s"}


def page(body):
    return f"<html><head><style>.a{{width:100px}}</style></head><body>{body}</body></html>"


def n(text, table="gold_offer_metrics", row="o:share", metric="share", version="v1"):
    return f'<span class="n" data-table="{table}" data-row="{row}" data-metric="{metric}" data-version="{version}">{text}</span>'


CASES = [
    ("sourced percent passes", page(f"Known {n('68%')} of value."), True),
    ("68% equals 0.68", page(f"Known {n('0.68')}."), True),
    ("text source passes", page(f"Asking {n('high 80s', 'silver_offers', 'o', 'asking_price_text')}."), True),
    ("year allowed", page("Vintage 2016 funds."), True),
    ("fund ordinal allowed", page("Fund 9 has no ratios."), True),
    ("repeat of sourced number allowed", page(f"{n('68%')} known; again 68% known."), True),
    ("css digits ignored", page("No numbers."), True),
    ("lineage block ignored", page('<div class="lineage">row o:L01 v1 sha 3f2a</div>'), True),
    ("unsourced number fails", page("We expect 12% returns."), False),
    ("invented price fails", page(f"{n('68%')}. Fair price 91%."), False),
    ("wrong value fails", page(f"Known {n('72%')}."), False),
    ("missing lineage fails", page('<span class="n" data-table="gold_offer_metrics">68%</span>'), False),
    ("unknown row fails", page(f"{n('68%', row='nope')}"), False),
    ("number in fund name position fails", page("Ostrova Growth 3 fits."), False),
]


def _lookup(t, r, m):
    if (t, r, m) not in SRC:
        raise KeyError((t, r, m))
    return SRC[(t, r, m)]


@pytest.mark.parametrize("name,html,ok", CASES, ids=[c[0] for c in CASES])
def test_validator_case(name, html, ok):
    problems = brief.validate(html, _lookup)
    assert (problems == []) is ok, problems


def test_normalise():
    assert brief.normalise("68%") == pytest.approx(0.68)
    assert brief.normalise("1.71x") == pytest.approx(1.71)
    assert brief.normalise("€16.1M") == pytest.approx(16.1)
    assert brief.normalise("+3%") == pytest.approx(0.03)
    assert brief.normalise("$1,234.5M") == pytest.approx(1234.5)


def test_eval_every_generated_brief_is_valid(env):
    con = env.con()
    results = {}
    for o in store.rows(con, "SELECT offer_id, code_name FROM silver_offers"):
        html = brief.render(con, o["offer_id"])
        problems = brief.validate(html, lambda t, r, m: store.lookup(con, t, r, m))
        results[o["code_name"]] = problems
        assert html.index("<h1>") < html.index("<h2>Why</h2>") < html.index("<h2>Lines</h2>")  # headline first
    con.close()
    EVALS["briefs_valid"] = {"valid": sum(1 for p in results.values() if not p), "total": len(results)}
    assert all(not p for p in results.values()), results


def test_tampered_brief_fails(env):
    con = env.con()
    o = store.resolve_offer(con, "cedar")
    html = brief.render(con, o["offer_id"])
    bad = html.replace("<h2>Why</h2>", "<h2>Why</h2><p>Expected IRR 18%.</p>")
    assert brief.validate(bad, lambda t, r, m: store.lookup(con, t, r, m))
    con.close()


def test_validator_cases_summary():
    ok = sum((brief.validate(h, _lookup) == []) is exp for _, h, exp in CASES)
    EVALS["validator_cases"] = {"correct": ok, "total": len(CASES)}
    assert ok == len(CASES)
