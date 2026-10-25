"""The team's rulebook: load, check, evaluate, explain.

Two questions. Fit: score = weighted share of checked rules that are met. A rule whose metric
has no value is "not checked" and left out of the score. Price (from v3): the asking price
against our range, which uses what our own fund reports told us since the broker's date.
LOOK CLOSER needs score >= threshold, no failed veto, and an asking price no more than the
price veto gap above the top of our range.
"""
from __future__ import annotations

import hashlib
import operator
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from .metrics import REGISTRY
from .models import RuleResult, Verdict

OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}
OP_WORDS = {"<": "under", "<=": "at most", ">": "over", ">=": "at least", "==": "equal to"}
PCT_METRICS = {k for k in REGISTRY if k.startswith("share_") or k in ("data_matched", "top5_concentration",
               "price_range_low", "price_range_high", "asking_price_mid", "guidance_minus_range_top")}


class RulebookError(ValueError):
    pass


class Rule(BaseModel):
    id: str
    description: str
    metric: str
    op: Literal["<", "<=", ">", ">=", "=="]
    value: float
    weight: float = Field(gt=0)
    veto: bool = False


class Confidence(BaseModel):
    data_matched_strong: float = 0.9
    data_matched_weak: float = 0.7
    margin_strong: float = 0.2
    margin_weak: float = 0.1


class Price(BaseModel):
    veto_gap: float = Field(ge=0, le=1)  # PASS if the ask is above the top of our range by more than this


PRICE_VETO = "price_veto"


class Rulebook(BaseModel):
    version: str
    threshold: float = Field(ge=0, le=1)
    confidence: Confidence = Confidence()
    quick_look: list[Rule]
    price: Price | None = None
    sha: str = ""

    @field_validator("version", mode="before")
    @classmethod
    def _v(cls, v):
        return str(v)


def load(path: Path) -> Rulebook:
    raw = path.read_bytes()
    data = yaml.safe_load(raw) or {}
    rules = data.get("quick_look") or []
    for r in rules:
        if r.get("metric") not in REGISTRY:
            raise RulebookError(
                f"Rule '{r.get('id')}' uses unknown metric '{r.get('metric')}'. "
                f"Valid metrics: {', '.join(sorted(REGISTRY))}.")
    ids = [r.get("id") for r in rules]
    if len(ids) != len(set(ids)):
        raise RulebookError("Rule ids must be unique.")
    data = {k: v for k, v in data.items() if k in ("version", "threshold", "confidence", "quick_look", "price")}
    try:
        rb = Rulebook(**data)
    except Exception as e:  # pydantic error -> one readable line
        raise RulebookError(f"Rulebook {path.name} is invalid: {e}") from e
    rb.sha = hashlib.sha256(raw).hexdigest()[:12]
    return rb


def fmt_threshold(metric: str, v: float) -> str:
    return f"{v:.0%}" if metric in PCT_METRICS and metric != "guidance_minus_range_top" else f"{v:g}"


def as_sentences(rb: Rulebook) -> list[str]:
    out = [f"Quick Look rulebook, version {rb.version}.",
           f"An offer gets LOOK CLOSER when the weighted share of rules met is {rb.threshold:.0%} or more "
           f"and no veto rule fails. Otherwise it gets PASS.",
           "A rule we cannot check (its number is missing) is left out of the score and listed as not checked."]
    for i, r in enumerate(sorted(rb.quick_look, key=lambda r: (not r.veto, -r.weight)), start=1):
        name = REGISTRY[r.metric][1].lower()
        target = ("above our range by no more than 0 points" if r.metric == "guidance_minus_range_top"
                  else f"{OP_WORDS[r.op]} {fmt_threshold(r.metric, r.value)}")
        s = f"{i}. {r.description}. Checks that {name} is {target}. Weight {r.weight:g}."
        if r.metric == "guidance_minus_range_top":
            s = f"{i}. {r.description}. Checks that the asking price is at or below the top of our range. Weight {r.weight:g}."
        if r.veto:
            s += " Veto: if this fails, the verdict is PASS whatever the score."
        out.append(s)
    if rb.price:
        out.append(f"Price, judged apart from fit: for funds we own, our range starts from the broker's value and "
                   f"adds what our own fund reports told us since. If the asking price is more than "
                   f"{rb.price.veto_gap * 100:.0f} points above the top of our range, the verdict is PASS.")
    c = rb.confidence
    out.append(f"Confidence is high when data matched is {c.data_matched_strong:.0%} or more and the score is at "
               f"least {c.margin_strong:g} from the threshold; low when data matched is under "
               f"{c.data_matched_weak:.0%} or the gap is under {c.margin_weak:g}; medium otherwise.")
    return out


def evaluate(rb: Rulebook, metrics: dict[str, float | None]) -> Verdict:
    results: list[RuleResult] = []
    for r in rb.quick_look:
        v = metrics.get(r.metric)
        status = "not checked" if v is None else ("met" if OPS[r.op](v, r.value) else "not met")
        results.append(RuleResult(rule_id=r.id, description=r.description, metric=r.metric, op=r.op,
                                  threshold=r.value, weight=r.weight, veto=r.veto, status=status, value=v))
    results.sort(key=lambda x: (not x.veto, -x.weight, x.rule_id))
    checked = [x for x in results if x.status != "not checked"]
    score = (sum(x.weight for x in checked if x.status == "met") / sum(x.weight for x in checked)) if checked else None
    veto = next((x for x in results if x.veto and x.status == "not met"), None)
    gap = metrics.get("guidance_minus_range_top")
    price_view = "no view" if gap is None else ("cheap" if gap <= 1e-9 else "expensive")
    price_veto = veto is None and rb.price is not None and gap is not None and gap > rb.price.veto_gap + 1e-9
    look = veto is None and not price_veto and score is not None and score >= rb.threshold
    margin = None if (veto or price_veto) else (abs(score - rb.threshold) if score is not None else 0.0)
    dm = metrics.get("data_matched") or 0.0
    c = rb.confidence
    dm_strong, dm_weak = dm >= c.data_matched_strong, dm < c.data_matched_weak
    decided = veto is not None or price_veto
    m_strong = decided or (margin is not None and margin >= c.margin_strong)
    m_weak = not decided and (margin is None or margin < c.margin_weak)
    band = "high" if (dm_strong and m_strong) else ("low" if (dm_weak or m_weak) else "medium")
    not_checked = sum(1 for x in results if x.status == "not checked")
    if not_checked and band != "low":
        band = "medium" if band == "high" else "low"
    known = metrics.get("share_value_known_manager")
    if not known:
        band = "low"
    verdict = Verdict(verdict="LOOK CLOSER" if look else "PASS", score=score, threshold=rb.threshold,
                      veto_rule=veto.rule_id if veto else (PRICE_VETO if price_veto else None), data_matched=dm,
                      margin=margin, price_view=price_view, band=band,
                      headline="", reasons=results, rulebook_version=rb.version, rulebook_sha=rb.sha)
    verdict.headline_segments = headline_segments(verdict, metrics, rb)
    verdict.headline = "".join(seg if isinstance(seg, str) else seg[2] for seg in verdict.headline_segments)
    return verdict


def _fmt(kind: str, v: float) -> str:
    return f"{v:.0%}" if kind == "pct" else f"{v:.0f}"


PRICE_WORDS = {"cheap": "the price looks right by our own data",
               "expensive": "the price looks high by our own data",
               "no view": "we have no price view (we own none of these funds)"}


def headline_segments(v: Verdict, metrics: dict[str, float | None], rb: Rulebook) -> list:
    """Headline as text pieces and (metric, kind, formatted) tuples, so the brief can source each number.

    Plain words first: the decision, then fit and price, then how sure we are and why."""
    def num(metric: str, kind: str):
        return (metric, kind, _fmt(kind, metrics[metric]))

    if v.veto_rule == PRICE_VETO:
        return ["PASS: the seller asks about ", num("asking_price_mid", "pct"), " of value, and by our own data "
                "we would pay at most ", num("price_range_high", "pct"), "."]
    if v.veto_rule:
        rule = next(r for r in v.reasons if r.rule_id == v.veto_rule)
        return [f"PASS: fails \"{rule.description}\" (", num(rule.metric, "pct"), ")."]
    if not metrics.get("share_value_known_manager"):
        return [f"{v.verdict}: we know none of these funds. Low confidence."]
    fit = "fits our rules" if v.verdict == "LOOK CLOSER" else "does not fit our rules well enough"
    joiner = ", and " if v.price_view == "no view" else (
        ", but " if (v.verdict == "LOOK CLOSER") == (v.price_view == "expensive") else ", and ")
    segs: list = [f"{v.verdict}: {fit}{joiner}{PRICE_WORDS[v.price_view]}. {v.band.capitalize()} confidence"]
    why = []
    n = metrics.get("count_unresolved") or 0
    if n:
        why += [num("count_unresolved", "count"), f" fund{'s' if n > 1 else ''} (",
                num("share_value_unresolved", "pct"), " of value) not identified"]
    nc = sum(1 for r in v.reasons if r.status == "not checked")
    if nc:
        why.append(("; " if why else "") + f"{'one rule' if nc == 1 else 'some rules'} could not be checked")
    if v.margin is not None and v.margin < rb.confidence.margin_strong:
        why.append(("; " if why else "") + "close to the line on fit")
    if not why:
        return segs + ["."]
    return segs + ([". Still open: "] if v.band == "high" else [": "]) + why + ["."]


def diff_sentences(old: dict[str, Verdict], new: dict[str, Verdict]) -> list[str]:
    out = []
    for code in sorted(new):
        a, b = old.get(code), new[code]
        sa = "n/a" if (a is None or a.score is None) else f"{a.score:.2f}"
        sb = "n/a" if b.score is None else f"{b.score:.2f}"
        if a is None:
            out.append(f"{code} is new: {b.verdict} (score {sb}).")
        elif a.verdict != b.verdict:
            why = f", blocked by veto {b.veto_rule}" if b.veto_rule else ""
            out.append(f"{code} flips to {b.verdict} (score {sa} to {sb}{why}).")
        else:
            out.append(f"{code} stays {b.verdict} (score {sa} to {sb}).")
    return out
