"""Identify each offer line by its fingerprint. Code decides, not the model.

Step 1: filter our universe by vintage, strategy and region (and size band if given).
Step 2: for funds we own, compare the line's ratios with our own report at the
offer's reference date: % called, DPI, TVPI, value / commitment.

Confidence = 1 - mean normalised distance. A ratio's distance is |gap| / (5 x tolerance),
capped at 1. Tolerances: 2 points on % called, 0.05x on multiples and on value / commitment.
So 0.8 means "on average, every ratio is inside its tolerance".

Tiers: same fund (ratio match >= 0.8, and clear of the next best),
same manager other fund (description points to one manager), unknown.
Nothing is matched silently: every result carries its method and candidates.
"""
from __future__ import annotations

import datetime as dt
import re

from .models import Candidate, LineMatch, ParsedLine, Tier
from .reference import Reference

VERSION = "match-v1"
TOL = {"pct_called": 0.02, "dpi": 0.05, "tvpi": 0.05, "value_per_commitment": 0.05}
SCALE = {k: 5 * v for k, v in TOL.items()}
SAME_FUND_MIN = 0.8
CLEAR_GAP = 0.05  # best must beat the runner-up by this much
RULED_OUT_BELOW = 0.6  # a fund we own scoring under this is not the line
MIN_RATIOS = 2  # one number is not a fingerprint
YOUNG_FUND_YEARS = 4  # younger funds have weak fingerprints
FRAGMENT_BONUS, FRAGMENT_PENALTY = 0.03, 0.10
ROMAN = re.compile(r"^(?=[IVXL]+$)[IVXL]+$")


def fragment_matches(fragment: str | None, fund_name: str) -> bool:
    if not fragment:
        return False
    pieces = [p.strip(" \"'") for p in re.split(r"\.\.\.|…", fragment) if p.strip(" \"'")]
    if not pieces:
        return False
    name = fund_name.lower()
    words = fund_name.split()
    for p in pieces:
        if ROMAN.match(p):
            if words[-1] != p:
                return False
        elif p.lower() not in name:
            return False
    return True


def line_ratios(line: ParsedLine) -> dict[str, float]:
    r = {}
    for k in ("pct_called", "dpi", "tvpi"):
        v = getattr(line, k)
        if v is not None:
            r[k] = v
    if line.value is not None and line.commitment:
        r["value_per_commitment"] = line.value / line.commitment
    return r


def fund_ratios(called: float, distributed: float, nav: float, size: float) -> dict[str, float]:
    return {"pct_called": called / size, "dpi": distributed / called, "tvpi": (distributed + nav) / called,
            "value_per_commitment": nav / size}


def display_slack(v: float, key: str) -> float:
    """Rounding the broker applied: 1.7x may be anything from 1.65x to 1.75x; 92% from 91.5% to 92.5%."""
    if key == "value_per_commitment":
        return 0.0
    scale = 100 if key == "pct_called" else 1  # % called is shown in points
    for dec in (0, 1, 2):
        if abs(v * scale * 10 ** dec - round(v * scale * 10 ** dec)) < 1e-6:
            return 0.5 * 10 ** -dec / scale
    return 0.0


def within_tolerance(line_r: dict[str, float], fund_r: dict[str, float]) -> list[str]:
    """Ratios outside tolerance (plus the broker's rounding). Empty means all inside."""
    return [k for k, v in line_r.items() if abs(v - fund_r[k]) > TOL[k] + display_slack(v, k) + 1e-9]


def ratio_score(line_r: dict[str, float], fund_r: dict[str, float]) -> tuple[float, dict[str, float]]:
    """1 - mean normalised distance over the ratios the line gives."""
    d = {k: min(1.0, abs(v - fund_r[k]) / SCALE[k]) for k, v in line_r.items()}
    return 1 - sum(d.values()) / len(d), d


def _cand(ref: Reference, f: dict, score: float | None = None, note: str = "") -> Candidate:
    return Candidate(fund_id=f["fund_id"], fund_name=f["name"], manager_id=f["manager_id"],
                     manager_name=ref.manager_name(f["manager_id"]), owned=bool(f["owned"]),
                     score=None if score is None else round(score, 4), note=note)


def match_line(line: ParsedLine, line_id: str, ref: Reference, reference_date: dt.date,
               known_at: dt.datetime) -> LineMatch:
    flags: list[str] = []
    if line.vintage is None and line.strategy is None and line.region is None:
        return LineMatch(line_id=line_id, tier=Tier.UNKNOWN, resolved=False, method="no description",
                         flags=["line has no vintage, strategy or region"])
    cands = ref.candidates(line.vintage, line.strategy, line.region)
    if line.size_band:
        sized = [f for f in cands if f["size_band"] == line.size_band.strip()]
        if sized:
            cands = sized
        elif cands:
            flags.append("size band matched no candidate; ignored")
    frag_hits = {f["fund_id"] for f in cands if fragment_matches(line.name_fragment, f["name"])}

    lr = line_ratios(line)
    scored: list[tuple[float, dict, str]] = []
    if lr:
        for f in cands:
            if not f["owned"]:
                continue
            row = ref.series_at(f["fund_id"], reference_date, known_at)
            if row is None or row.called <= 0:
                flags.append(f"no report for {f['fund_id']} at the reference date; not compared")
                continue
            fr = fund_ratios(row.called, row.distributed, row.nav, f["size_m"])
            s, _ = ratio_score(lr, fr)
            if line.name_fragment:
                s = min(1.0, s + FRAGMENT_BONUS) if f["fund_id"] in frag_hits else max(0.0, s - FRAGMENT_PENALTY)
            scored.append((s, f, row.row_id, within_tolerance(lr, fr)))
        scored.sort(key=lambda x: (-x[0], x[1]["fund_id"]))

    if lr and len(lr) < MIN_RATIOS:
        flags.append("only one ratio given; not enough to confirm a fund")
    if scored and scored[0][0] >= SAME_FUND_MIN and len(lr) >= MIN_RATIOS and scored[0][3]:
        flags.append(f"best fit {scored[0][1]['fund_id']} is outside tolerance on {', '.join(scored[0][3])}")
    if scored and scored[0][0] >= SAME_FUND_MIN and len(lr) >= MIN_RATIOS and not scored[0][3]:
        best_s, best, row_id, _ = scored[0]
        if len(scored) > 1 and scored[1][0] >= SAME_FUND_MIN and best_s - scored[1][0] < CLEAR_GAP:
            two = [scored[0], scored[1]]
            same_mgr = two[0][1]["manager_id"] == two[1][1]["manager_id"]
            return LineMatch(
                line_id=line_id, tier=Tier.SAME_MANAGER if same_mgr else Tier.UNKNOWN,
                manager_id=two[0][1]["manager_id"] if same_mgr else None, confidence=round(best_s, 4),
                resolved=False, method="ratios: two funds we own fit",
                candidates=[_cand(ref, f, s) for s, f, _, _ in two],
                flags=flags + ["two funds we own fit the numbers; not matched"])
        # A fund we track but do not own has no series here, so ratios cannot rule it out.
        # That matters for young funds: under 4 years, most funds show DPI near 0 and TVPI near 1.
        age = (reference_date - dt.date(best["vintage"], 1, 1)).days / 365.25
        shadow = [f for f in cands if not f["owned"]] if age < YOUNG_FUND_YEARS else []
        if shadow:
            pair = [best, shadow[0]]
            same_mgr = len({f["manager_id"] for f in pair + shadow}) == 1
            return LineMatch(
                line_id=line_id, tier=Tier.SAME_MANAGER if same_mgr else Tier.UNKNOWN,
                manager_id=best["manager_id"] if same_mgr else None, confidence=round(best_s, 4),
                resolved=False, method="ratios fit a fund we own, but a fund we track also fits the description",
                candidates=[_cand(ref, best, best_s), _cand(ref, shadow[0], None, "we have no ratios for it")],
                flags=flags + [f"{len(shadow)} fund(s) we track share the description; ratios cannot rule them out"])
        return LineMatch(
            line_id=line_id, tier=Tier.SAME_FUND, fund_id=best["fund_id"], manager_id=best["manager_id"],
            confidence=round(best_s, 4), resolved=True, method="ratios at reference date",
            candidates=[_cand(ref, f, s) for s, f, _, _ in scored[:2]], flags=flags, compared_series_row=row_id)

    ruled_out = {f["fund_id"] for s, f, _, out in scored if s < RULED_OUT_BELOW or (len(lr) >= MIN_RATIOS and len(out) >= 2)}
    remaining = [f for f in cands if f["fund_id"] not in ruled_out]
    score_of = {f["fund_id"]: s for s, f, _, _ in scored}
    best_conf = round(scored[0][0], 4) if scored else None

    def ranked(fs: list[dict]) -> list[dict]:
        return sorted(fs, key=lambda f: (f["fund_id"] not in frag_hits, -score_of.get(f["fund_id"], 0.0),
                                         f["fund_id"]))

    hits = [f for f in remaining if f["fund_id"] in frag_hits]
    if len(hits) == 1:
        f = hits[0]
        return LineMatch(line_id=line_id, tier=Tier.SAME_MANAGER, manager_id=f["manager_id"],
                         confidence=best_conf, resolved=True, method="description and name fragment",
                         candidates=[_cand(ref, f, score_of.get(f["fund_id"]), "name fragment fits")], flags=flags)
    if not remaining:
        shown = [_cand(ref, f, s, "ruled out by ratios") for s, f, _, _ in scored[:2]]
        return LineMatch(line_id=line_id, tier=Tier.UNKNOWN, confidence=best_conf, resolved=True,
                         method="nothing in our universe fits" if not cands else "ratios rule out every fund we know",
                         candidates=shown, flags=flags)
    mgrs = {f["manager_id"] for f in remaining}
    top = ranked(remaining)[:2]
    if len(mgrs) == 1:
        return LineMatch(line_id=line_id, tier=Tier.SAME_MANAGER, manager_id=top[0]["manager_id"],
                         confidence=best_conf, resolved=True, method="description points to one manager",
                         candidates=[_cand(ref, f, score_of.get(f["fund_id"])) for f in top], flags=flags)
    return LineMatch(line_id=line_id, tier=Tier.UNKNOWN, confidence=best_conf, resolved=False,
                     method="description fits several managers",
                     candidates=[_cand(ref, f, score_of.get(f["fund_id"])) for f in top],
                     flags=flags + [f"{len(remaining)} funds from {len(mgrs)} managers fit the description"])


def analyst_pick(m: LineMatch, fund: dict, ref: Reference, who: str) -> LineMatch:
    """An analyst says which fund an open line is. Code keeps the tier rules:
    a fund we own becomes "same fund", a fund we only track becomes "same manager".
    The ratio score is not invented: confidence stays empty and the method says who decided."""
    tier = Tier.SAME_FUND if fund["owned"] else Tier.SAME_MANAGER
    return LineMatch(line_id=m.line_id, tier=tier, fund_id=fund["fund_id"], manager_id=fund["manager_id"],
                     confidence=None, resolved=True, method=f"analyst pick by {who}",
                     candidates=m.candidates or [_cand(ref, fund)],
                     flags=m.flags + [f"code said {m.tier.value} ({m.method}); analyst picked {fund['fund_id']}"])
