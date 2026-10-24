"""Public list of about 60 US public pensions and the linker.

Real names, used only to link public news. Never joined with synthetic data (PRD D-3).
Acronym aliases match case-sensitively on word boundaries. Long names match case-insensitively.
Short forms ("Washington State", "NYC") link only when a pension or finance word sits in the
same headline, so sports, weather or city news never links.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (id, official name, state, aliases)
PENSIONS: list[tuple[str, str, str, list[str]]] = [
    ("calpers", "California Public Employees' Retirement System", "CA", ["CalPERS", "Calpers", "California Public Employees"]),
    ("calstrs", "California State Teachers' Retirement System", "CA", ["CalSTRS", "Calstrs", "California State Teachers"]),
    ("nystrs", "New York State Teachers' Retirement System", "NY", ["NYSTRS", "New York State Teachers"]),
    ("nyscrf", "New York State Common Retirement Fund", "NY", ["New York State Common", "New York Common Retirement Fund", "NY Common", "New York State Pension", "New York State pension fund", "DiNapoli"]),
    ("nyc_funds", "New York City Retirement Systems", "NY", ["New York City Retirement Systems", "NYC pension funds", "New York City pension funds", "New York City pension", "NYC pension", "NYC Retirement System", "NYC pension systems", "NYCERS", "New York City Employees' Retirement System", "Teachers' Retirement System of the City of New York"]),
    ("wsib", "Washington State Investment Board", "WA", ["Washington State Investment Board", "WSIB", "Washington State pension", "WA pension", "WA's pension", "Washington pension board", "Washington pension"]),
    ("psers", "Pennsylvania Public School Employees' Retirement System", "PA", ["PSERS", "Pennsylvania Public School Employees", "Pennsylvania school employees"]),
    ("pa_sers", "Pennsylvania State Employees' Retirement System", "PA", ["Pennsylvania State Employees' Retirement System", "Pennsylvania SERS", "PennSERS", "Pennsylvania State Employees"]),
    ("nj_doi", "New Jersey Division of Investment", "NJ", ["New Jersey Division of Investment", "New Jersey State Investment Council", "New Jersey pension", "NJ pension", "New Jersey's pension"]),
    ("ohio_sers", "School Employees Retirement System of Ohio", "OH", ["School Employees Retirement System of Ohio", "Ohio SERS", "SERS Ohio"]),
    ("ohio_strs", "State Teachers Retirement System of Ohio", "OH", ["State Teachers Retirement System of Ohio", "STRS Ohio", "Ohio STRS", "Ohio teachers' pension", "Ohio Teachers", "Ohio State Teachers"]),
    ("opers", "Ohio Public Employees Retirement System", "OH", ["Ohio Public Employees Retirement System", "OPERS", "Ohio PERS"]),
    ("ohio_pfpf", "Ohio Police & Fire Pension Fund", "OH", ["Ohio Police & Fire", "Ohio Police and Fire"]),
    ("nc_rs", "North Carolina Retirement Systems", "NC", ["North Carolina Retirement System", "North Carolina pension", "N.C. pension", "North Carolina state pension", "North Carolina Treasurer"]),
    ("trs_texas", "Teacher Retirement System of Texas", "TX", ["Teacher Retirement System of Texas", "Texas Teachers", "TRS Texas", "Texas TRS", "Texas teacher pension"]),
    ("texas_ers", "Employees Retirement System of Texas", "TX", ["Employees Retirement System of Texas", "ERS of Texas", "Texas ERS"]),
    ("tcdrs", "Texas County & District Retirement System", "TX", ["Texas County & District", "Texas County and District", "TCDRS"]),
    ("florida_sba", "Florida State Board of Administration", "FL", ["Florida State Board of Administration", "Florida SBA", "Florida pension"]),
    ("oregon", "Oregon Public Employees Retirement Fund", "OR", ["Oregon Investment Council", "Oregon PERS", "OPERF", "Oregon Public Employees", "Oregon State Treasury", "Oregon pension"]),
    ("swib", "State of Wisconsin Investment Board", "WI", ["State of Wisconsin Investment Board", "Wisconsin Investment Board", "SWIB"]),
    ("michigan", "State of Michigan Retirement Systems", "MI", ["Michigan Retirement Systems", "SMRS", "Michigan Bureau of Investments", "Michigan pension"]),
    ("minnesota", "Minnesota State Board of Investment", "MN", ["Minnesota State Board of Investment", "Minnesota SBI", "Minnesota pension"]),
    ("massprim", "Massachusetts Pension Reserves Investment Management Board", "MA", ["MassPRIM", "Mass PRIM", "Massachusetts PRIM", "PRIM Board", "Massachusetts Pension Reserves"]),
    ("vrs", "Virginia Retirement System", "VA", ["Virginia Retirement System", "VRS", "Virginia pension"]),
    ("maryland", "Maryland State Retirement and Pension System", "MD", ["Maryland State Retirement", "Maryland pension"]),
    ("trs_illinois", "Teachers' Retirement System of Illinois", "IL", ["Teachers' Retirement System of Illinois", "TRS Illinois", "Illinois TRS", "Illinois Teachers"]),
    ("imrf", "Illinois Municipal Retirement Fund", "IL", ["Illinois Municipal Retirement Fund", "IMRF"]),
    ("isbi", "Illinois State Board of Investment", "IL", ["Illinois State Board of Investment", "ISBI"]),
    ("colorado_pera", "Colorado Public Employees' Retirement Association", "CO", ["Colorado PERA", "Colorado Public Employees"]),
    ("asrs", "Arizona State Retirement System", "AZ", ["Arizona State Retirement System", "ASRS"]),
    ("psprs", "Arizona Public Safety Personnel Retirement System", "AZ", ["Public Safety Personnel Retirement System", "PSPRS"]),
    ("urs", "Utah Retirement Systems", "UT", ["Utah Retirement Systems"]),
    ("nvpers", "Public Employees' Retirement System of Nevada", "NV", ["Nevada PERS", "Public Employees' Retirement System of Nevada", "Nevada pension"]),
    ("nm_erb", "New Mexico Educational Retirement Board", "NM", ["New Mexico Educational Retirement Board", "New Mexico ERB"]),
    ("nm_pera", "Public Employees Retirement Association of New Mexico", "NM", ["New Mexico PERA"]),
    ("lacera", "Los Angeles County Employees Retirement Association", "CA", ["LACERA", "Los Angeles County Employees Retirement"]),
    ("lafpp", "Los Angeles Fire and Police Pensions", "CA", ["LAFPP", "Los Angeles Fire and Police Pension"]),
    ("sfers", "San Francisco Employees' Retirement System", "CA", ["SFERS", "San Francisco Employees' Retirement System", "San Francisco pension"]),
    ("sdcera", "San Diego County Employees Retirement Association", "CA", ["SDCERA", "San Diego County Employees Retirement"]),
    ("ocers", "Orange County Employees Retirement System", "CA", ["OCERS", "Orange County Employees Retirement"]),
    ("connecticut", "Connecticut Retirement Plans and Trust Funds", "CT", ["Connecticut Retirement Plans", "Connecticut pension"]),
    ("kppa", "Kentucky Public Pensions Authority", "KY", ["Kentucky Public Pensions Authority", "Kentucky Retirement Systems", "KPPA"]),
    ("ktrs", "Teachers' Retirement System of the State of Kentucky", "KY", ["Kentucky Teachers' Retirement System", "Kentucky Teachers", "TRS of Kentucky"]),
    ("alaska_armb", "Alaska Retirement Management Board", "AK", ["Alaska Retirement Management Board"]),
    ("hawaii_ers", "Employees' Retirement System of the State of Hawaii", "HI", ["Hawaii ERS", "Employees' Retirement System of the State of Hawaii", "Hawaii Employees' Retirement System"]),
    ("ipers", "Iowa Public Employees' Retirement System", "IA", ["IPERS", "Iowa PERS", "Iowa Public Employees"]),
    ("mosers", "Missouri State Employees' Retirement System", "MO", ["MOSERS", "Missouri State Employees"]),
    ("psrs_missouri", "Public School and Education Employee Retirement Systems of Missouri", "MO", ["Missouri PSRS", "Public School Retirement System of Missouri"]),
    ("trsl", "Teachers' Retirement System of Louisiana", "LA", ["Teachers' Retirement System of Louisiana", "TRSL", "Louisiana Teachers"]),
    ("inprs", "Indiana Public Retirement System", "IN", ["Indiana Public Retirement System", "INPRS"]),
    ("sc_rsic", "South Carolina Retirement System Investment Commission", "SC", ["South Carolina Retirement System Investment Commission", "South Carolina RSIC", "RSIC"]),
    ("tcrs", "Tennessee Consolidated Retirement System", "TN", ["Tennessee Consolidated Retirement System", "TCRS"]),
    ("trs_georgia", "Teachers Retirement System of Georgia", "GA", ["Teachers Retirement System of Georgia", "Georgia Teachers"]),
    ("montana_boi", "Montana Board of Investments", "MT", ["Montana Board of Investments"]),
    ("ctpf", "Chicago Teachers' Pension Fund", "IL", ["Chicago Teachers' Pension Fund", "Chicago Teachers Pension Fund", "CTPF"]),
    ("fppa", "Fire and Police Pension Association of Colorado", "CO", ["Fire and Police Pension Association of Colorado", "FPPA"]),
    ("cook_county", "County Employees' and Officers' Annuity and Benefit Fund of Cook County", "IL", ["Cook County pension", "Cook County Pension Fund"]),
    ("tmrs", "Texas Municipal Retirement System", "TX", ["Texas Municipal Retirement System", "TMRS"]),
    ("trs_oklahoma", "Teachers' Retirement System of Oklahoma", "OK", ["Oklahoma Teachers", "Teachers' Retirement System of Oklahoma"]),
    ("lacers", "Los Angeles City Employees' Retirement System", "CA", ["LACERS", "Los Angeles City Employees"]),
    ("sdcers", "San Diego City Employees' Retirement System", "CA", ["SDCERS", "San Diego city pension", "San Diego City Employees"]),
]

# Short forms that are also ordinary place names. They link only with a context word nearby.
SHORT_FORMS: dict[str, list[str]] = {
    "wsib": ["Washington State", "Washington state's pension"],
    "nyc_funds": ["NYC", "New York City", "NYC teachers", "New York City teachers"],
    "nystrs": ["New York teachers", "NY teachers", "New York State teachers"],
    "nyscrf": ["New York State"],
    "psers": ["Pennsylvania teachers", "Pennsylvania school pension"],
}

# Pension or finance words. A short form needs one of these in the headline, outside the match.
CONTEXT = re.compile(
    r"\b(pensions?|retirement|retirees?|comptroller|trustees|private[- ]equity|private[- ]markets?|PE|"
    r"secondar(y|ies)|CIO|chief investment officer|(asset )?allocation|fund stakes?|LP stakes?|"
    r"buyouts?|alternatives)\b",
    re.IGNORECASE,
)

# Text that looks like a pension but is not one (PRD section 6 "Pension Bros" case).
NOT_A_PENSION = re.compile(
    r"Pension\s*Bros|PensionBee|Pensions?\s*&\s*Investments|Pension\s*Fund\s*Online|Pensioenfonds|"
    r"Pension Protection Fund|Pensions Regulator|Pension Benefit Guaranty|Washington State University",
    re.IGNORECASE,
)
_POSSESSIVE = re.compile(r"(\w)['’]s\b")
_FORMER = re.compile(r"\b(former|ex-)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Pension:
    id: str
    name: str
    state: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Link:
    pension_id: str | None
    reason: str


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias).replace(r"\ ", r"\s+").replace("'", "['’]?")
    is_acronym = alias.isupper() or (alias.replace(" ", "").isalpha() and sum(c.isupper() for c in alias) >= 3 and len(alias) <= 8)
    flags = 0 if is_acronym else re.IGNORECASE
    # Optional plural ("NYC Pensions"); possessives are stripped from the text before matching.
    return re.compile(rf"(?<![\w-]){escaped}s?(?![\w-])", flags)


ALL: dict[str, Pension] = {p[0]: Pension(p[0], p[1], p[2], tuple(p[3])) for p in PENSIONS}
# (pension id, pattern, needs a context word)
_PATTERNS: list[tuple[str, re.Pattern[str], bool]] = [
    (pid, _alias_pattern(a), False) for pid, _, _, aliases in PENSIONS for a in sorted(aliases, key=len, reverse=True)
] + [(pid, _alias_pattern(a), True) for pid, forms in SHORT_FORMS.items() for a in forms]


def mask(text: str, label: str = "[Pension]") -> str:
    """Replace every pension name or short form with a label (public docs show live rankings anonymised)."""
    text = _POSSESSIVE.sub(r"\1", text)
    for _, pat, _ in sorted(_PATTERNS, key=lambda t: -len(t[1].pattern)):
        text = pat.sub(label, text)
    return text


def _has_context(text: str, start: int, end: int) -> bool:
    return bool(CONTEXT.search(text[:start] + " " + text[end:]))


def link(headline: str) -> Link:
    """Link a headline to at most one pension.

    Rules: decoys removed first; short forms need a context word; the earliest mention wins,
    except a mention right after "former" or "ex-" goes last; at the same position the longer
    alias wins; two different pensions on the same span give no link. Outlet suffixes must be
    stripped by the caller.
    """
    text = _POSSESSIVE.sub(r"\1", NOT_A_PENSION.sub(" ", headline))
    hits: dict[str, tuple[int, int, int]] = {}      # pid -> (demoted, start, -length)
    for pid, pat, gated in _PATTERNS:
        for m in pat.finditer(text):
            if gated and not _has_context(text, m.start(), m.end()):
                continue
            key = (1 if _FORMER.search(text[: m.start()]) else 0, m.start(), -(m.end() - m.start()))
            if pid not in hits or key < hits[pid]:
                hits[pid] = key
            break
    if not hits:
        return Link(None, "no alias found")
    ordered = sorted(hits.items(), key=lambda kv: kv[1])
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return Link(None, f"ambiguous: {ordered[0][0]} and {ordered[1][0]}")
    pid, (demoted, start, _) = ordered[0]
    others = [p for p, _ in ordered[1:]]
    reason = f"alias match at {start}" + (" (after 'former')" if demoted else "") + (
        f"; also mentions {', '.join(others)}" if others else "")
    return Link(pid, reason)
