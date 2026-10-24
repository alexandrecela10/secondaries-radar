"""Contracts between stages. Each stage takes and returns these, nothing else.

parse -> ParsedOffer (silver contract)
match -> LineMatch
metrics -> MetricValue
rules -> Verdict
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Strategy = Literal["buyout", "growth", "venture", "credit", "infra"]
Region = Literal["north_america", "europe", "asia", "global"]
Currency = Literal["USD", "EUR", "GBP"]

STRATEGIES: tuple[str, ...] = ("buyout", "growth", "venture", "credit", "infra")
REGIONS: tuple[str, ...] = ("north_america", "europe", "asia", "global")


class Tier(str, Enum):
    SAME_FUND = "same_fund"
    SAME_MANAGER = "same_manager"
    UNKNOWN = "unknown"


class ParsedLine(BaseModel):
    """One fund line in an offer. Amounts are in millions of `currency`.

    Ratios are fractions and multiples: 92% called is 0.92, DPI 0.84x is 0.84.
    """

    model_config = ConfigDict(extra="forbid")

    line_no: int = Field(ge=1, le=500)
    label: str
    description: str = ""
    strategy: Strategy | None = None
    region: Region | None = None
    vintage: int | None = Field(default=None, ge=1990, le=2030)
    size_band: str | None = None
    currency: Currency | None = None
    commitment: float | None = Field(default=None, ge=0)
    pct_called: float | None = Field(default=None, ge=0, le=1.1)
    dpi: float | None = Field(default=None, ge=0, le=20)
    tvpi: float | None = Field(default=None, ge=0, le=20)
    value: float | None = Field(default=None, ge=0)
    unfunded: float | None = Field(default=None, ge=0)
    name_fragment: str | None = None

    @model_validator(mode="after")
    def _multiples_consistent(self) -> "ParsedLine":
        # TVPI includes DPI, so TVPI below DPI is a misread, not a fund.
        if self.dpi is not None and self.tvpi is not None and self.tvpi + 0.005 < self.dpi:
            raise ValueError(f"TVPI {self.tvpi} is below DPI {self.dpi}")
        return self

    def has_ratios(self) -> bool:
        return any(v is not None for v in (self.pct_called, self.dpi, self.tvpi))


class ParsedOffer(BaseModel):
    """Offer-level fields. Lines are validated one by one so one bad row
    does not block the offer (it is blocked and logged instead)."""

    model_config = ConfigDict(extra="forbid")

    code_name: str = Field(min_length=1)
    seller_type: str | None = None
    reference_date: dt.date
    currency: Currency = "USD"
    total_value: float | None = Field(default=None, ge=0)
    asking_price_text: str | None = None
    bid_deadline: dt.date | None = None
    lines: list[dict] = Field(default_factory=list)  # raw rows, validated into ParsedLine in silver


class Candidate(BaseModel):
    fund_id: str
    fund_name: str
    manager_id: str
    manager_name: str
    owned: bool
    score: float | None = None  # ratio score when we hold the fund and ratios exist
    note: str = ""


class LineMatch(BaseModel):
    line_id: str
    tier: Tier
    fund_id: str | None = None
    manager_id: str | None = None
    confidence: float | None = None  # ratio match confidence, 0 to 1
    resolved: bool  # tier is settled: ratio match, single manager, or nothing in our universe
    candidates: list[Candidate] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    method: str = ""
    compared_series_row: str | None = None


class Lineage(BaseModel):
    table: str
    row_id: str
    metric: str
    version: str


class MetricValue(BaseModel):
    name: str
    version: str
    value: float | None
    note: str = ""


class RuleResult(BaseModel):
    rule_id: str
    description: str
    metric: str
    op: str
    threshold: float
    weight: float
    veto: bool
    status: Literal["met", "not met", "not checked"]
    value: float | None


class Verdict(BaseModel):
    verdict: Literal["LOOK CLOSER", "PASS"]
    score: float | None
    threshold: float
    veto_rule: str | None
    data_matched: float
    margin: float | None  # None when a veto decides (counts as clear)
    band: Literal["high", "medium", "low"]
    headline: str
    headline_segments: list = Field(default_factory=list)  # text, or [metric, kind, shown]
    reasons: list[RuleResult]
    rulebook_version: str
    rulebook_sha: str
