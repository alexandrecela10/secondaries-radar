"""Schema contracts between layers. A row that fails a contract is blocked and logged."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

SignalType = Literal[
    "sale_or_advisor",
    "allocation_above_target",
    "liquidity_need",
    "policy_review",
    "leadership_change",
    "none",
]

QUOTE_MAX = 300


class RawItem(BaseModel):
    """Bronze: one item as a source returned it. Only legal fields are kept."""

    source: Literal["google_news", "gdelt", "frozen"]
    query: str
    title: str
    link: str
    outlet: str | None = None
    published: str | None = None      # source string, parsed in silver
    summary: str | None = None        # source snippet; cut to 300 chars before storage
    fetched_at: datetime              # when we received it (processing time)

    @field_validator("summary")
    @classmethod
    def _clip_summary(cls, v: str | None) -> str | None:
        # Legal rule: never store more than a 300-character quote, not even in bronze.
        return v[:QUOTE_MAX] if v else v


class NewsItem(BaseModel):
    """Silver: typed, validated, normalised."""

    item_id: str
    source: str
    canonical_link: HttpUrl
    headline: str = Field(min_length=8, max_length=400)
    outlet: str | None
    published_at: datetime            # event time
    fetched_at: datetime              # processing time
    quote: str = Field(max_length=QUOTE_MAX)
    headline_key: str

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return v


class Classification(BaseModel):
    """What a classifier returns for one item. The same schema for every adapter."""

    signal_type: SignalType
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str = Field(max_length=QUOTE_MAX)
    rationale: str = Field(default="", max_length=300)
