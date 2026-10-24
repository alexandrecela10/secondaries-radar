"""Parser port and the deterministic text helpers both adapters share."""
from __future__ import annotations

import datetime as dt
import re
from typing import Protocol

from ..emailio import EmailContent
from ..models import ParsedOffer


class ParseError(Exception):
    """The email could not be read as an offer. It goes to the review queue."""


class Parser(Protocol):
    name: str
    version: str
    offline: bool

    def parse(self, content: EmailContent) -> ParsedOffer: ...


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
CUR = {"$": "USD", "usd": "USD", "us$": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP"}


def parse_date(text: str) -> dt.date | None:
    t = text.strip().lower().replace(",", " ")
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", t)
    if m:  # day first, the convention in these emails
        y = int(m[3])
        return dt.date(y + 2000 if y < 100 else y, int(m[2]), int(m[1]))
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?\s+(\d{4})\b", t)
    if m and m[2] in MONTHS:
        return dt.date(int(m[3]), MONTHS[m[2]], int(m[1]))
    m = re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})\b", t)
    if m and m[1] in MONTHS:
        return dt.date(int(m[3]), MONTHS[m[1]], int(m[2]))
    return None


def parse_number(text: str) -> float | None:
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text.replace(" ", ""))
    if not m:
        return None
    return float(m[0].replace(",", ""))


def money_millions(num: str, unit: str | None) -> float:
    v = float(num.replace(",", ""))
    if unit and unit.lower().startswith("b"):
        v *= 1000
    return v


def currency_of(token: str | None) -> str | None:
    if not token:
        return None
    return CUR.get(token.strip().lower())


STRATEGY_WORDS = [
    ("venture", r"venture|early[- ]stage|seed|vc\b"),
    ("growth", r"growth"),
    ("credit", r"credit|lending|debt|mezz"),
    ("infra", r"infra"),
    ("buyout", r"buyout|lbo|mid[- ]market|large[- ]cap|small[- ]cap|private equity"),
]
REGION_WORDS = [
    ("north_america", r"north americ|\bus\b|\bu\.s\.|\busa\b|\bamerican\b"),
    ("europe", r"europe|\buk\b|nordic|dach"),
    ("asia", r"asia|apac|china|japan|india"),
    ("global", r"global|world"),
]


def normalise_strategy(text: str | None) -> str | None:
    if not text:
        return None
    t = text.lower()
    for name, pat in STRATEGY_WORDS:
        if re.search(pat, t):
            return name
    return None


def normalise_region(text: str | None) -> str | None:
    if not text:
        return None
    t = text.lower()
    for name, pat in REGION_WORDS:
        if re.search(pat, t):
            return name
    return None
