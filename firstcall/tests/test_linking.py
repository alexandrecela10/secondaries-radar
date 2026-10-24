import pytest

from fc.normalize import split_outlet
from fc.pensions import ALL, link


@pytest.mark.parametrize("headline", [
    "Pension Bros raises $20 million Series B",
    "PensionBee widens losses as customers grow",
    "Pension Benefit Guaranty Corp approves aid",
    "State pension board to review private equity allocation",     # no name, no link
    "opers is a word in lower case",                               # acronyms are case-sensitive
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
