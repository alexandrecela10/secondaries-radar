import pytest

from fc.normalize import split_outlet
from fc.pensions import ALL, link


@pytest.mark.parametrize("headline", [
    "Pension Bros raises $20 million Series B",
    "PensionBee widens losses as customers grow",
    "Pension Benefit Guaranty Corp approves aid",
    "State pension board to review private equity allocation",     # no name, no link
    "opers is a word in lower case",                               # acronyms are case-sensitive
    "Washington State beats Oregon in the Apple Cup",               # short form, no finance word
    "Washington State braces for heavy snow and power cuts",
    "Washington State University invests in private equity lab",   # a university, not the pension
    "NYC marathon draws a record crowd",
    "New York teachers strike over class sizes",
    "Blackstone buys $5 billion in private equity stakes from New York pensions",   # city or state: unclear
])
def test_false_positives_link_to_nothing(headline):
    assert link(headline).pension_id is None


def test_outlet_name_is_stripped_before_linking():
    h, outlet = split_outlet("Fund closes $2bn vehicle - Pensions & Investments", "Pensions & Investments")
    assert outlet == "Pensions & Investments" and link(h).pension_id is None


def test_links_known_pensions():
    assert link("Washington State Investment Board weighs secondary sale").pension_id == "wsib"
    assert link("CalPERS cuts PE target").pension_id == "calpers"
    assert link("PennSERS lowers PE target").pension_id == "pa_sers"


def test_at_most_one_pension_earliest_wins():
    r = link("CalSTRS and CalPERS boost private credit")
    assert r.pension_id == "calstrs" and "calpers" in r.reason


def test_pension_list_size():
    assert 50 <= len(ALL) <= 70


@pytest.mark.parametrize("headline, pid", [
    ("As philosophies change, Washington State gears up for first secondaries sale", "wsib"),
    ("WSIB adds $1.3bn to private markets", "wsib"),
    ("Washington State's pension board names new CIO", "wsib"),
    ("NYC Pensions Sell $5B in Private Equity Secondaries to Blackstone", "nyc_funds"),
    ("Strategic Partners emerges as sole buyer in NYC’s $5bn-plus secondaries sale", "nyc_funds"),
    ("NYC Comptroller Lander and Pension Trustees Close Record Private Equity Secondary Sale", "nyc_funds"),
    ("New York City’s Pension Is Changing How It Invests", "nyc_funds"),
    ("New York teachers' pension trims private equity target", "nystrs"),
    ("New York State Teachers names CIO", "nystrs"),           # longer alias wins at the same position
    ("PSERS board to review private equity allocation", "psers"),
    ("Pennsylvania teachers' pension weighs PE sale", "psers"),
])
def test_short_forms_link_with_context(headline, pid):
    assert link(headline).pension_id == pid


def test_former_affiliation_does_not_win():
    assert link("Gov. Kim Reynolds names former Maryland pension chief to lead IPERS").pension_id == "ipers"
