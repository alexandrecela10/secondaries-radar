"""Deterministic parser: regular expressions for body text, header mapping for tables.

Used when no API key is set, and as the reference the model parser is checked
against. It reads the formats our brokers use; a new format needs a new rule.
"""
from __future__ import annotations

import re

from ..emailio import EmailContent, Table
from ..models import ParsedOffer
from .base import (ParseError, currency_of, money_millions, normalise_region, normalise_strategy,
                   parse_date, parse_number)

CUR_TOK = r"(US\$|\bUSD\b|\bEUR\b|\bGBP\b|[$€£])"
AMT = CUR_TOK + r"\s*([\d][\d,]*(?:\.\d+)?)\s*(m|mn|million|bn|billion)?\b"

LINE_RE = re.compile(r"^\s*(?:Fund|Line|Interest)\s*#?\s*(\d{1,3})\s*(?:\(\s*\"([^\"]*)\"\s*\))?\s*[:\-–]\s*(.+)$",
                     re.IGNORECASE)
SELLERS = [("pension", "pension"), ("insurer", "insurer"), ("insurance", "insurer"), ("endowment", "endowment"),
           ("foundation", "foundation"), ("family office", "family office"), ("sovereign", "sovereign fund")]

HEADERS = {
    "label": ["line", "fund #", "fund", "#", "interest", "fund no"],
    "description": ["strategy", "description", "fund description", "type"],
    "region": ["region", "geography", "geo"],
    "vintage": ["vintage", "vintage year"],
    "size_band": ["fund size", "size band", "size"],
    "currency": ["currency", "ccy"],
    "commitment": ["commitment (m)", "commitment", "commitment (mn)"],
    "pct_called": ["% called", "paid-in %", "% funded", "called %", "% paid-in"],
    "dpi": ["dpi", "dpi (x)"],
    "tvpi": ["tvpi", "tvpi (x)"],
    "value": ["nav (m)", "nav", "value", "nav (mn)"],
    "unfunded": ["unfunded (m)", "unfunded", "unfunded (mn)"],
    "name_fragment": ["notes", "comment", "comments", "name hint"],
}


def _search(pat: str, text: str, flags=re.IGNORECASE):
    return re.search(pat, text, flags)


def parse_line_text(n: int, hint: str | None, text: str) -> dict:
    row: dict = dict(line_no=n, label=f"Fund {n}", name_fragment=hint or None)
    year = _search(r"(?<![/\d])(199\d|20[0-3]\d)(?![/\d])", text)
    row["vintage"] = int(year[1]) if year else None
    desc = text[: year.start()] if year else text.split(",")[0]
    row["description"] = desc.strip(" ,;")
    row["strategy"] = normalise_strategy(row["description"])
    row["region"] = normalise_region(row["description"])
    size = _search(r"fund size\s*([^,;]+)", text)
    row["size_band"] = size[1].strip() if size else None
    m = _search(r"commit(?:ment|ted)?\s*(?:of\s*)?" + AMT, text)
    row["commitment"] = money_millions(m[2], m[3]) if m else None
    row["currency"] = currency_of(m[1]) if m else None
    m = _search(r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:called|funded|paid[- ]in|drawn)", text) or \
        _search(r"(?:called|funded|paid[- ]in|drawn)\s*(\d{1,3}(?:\.\d+)?)\s*%", text)
    row["pct_called"] = float(m[1]) / 100 if m else None
    m = _search(r"\bDPI\s*(?:of\s*)?(\d+(?:\.\d+)?)", text)
    row["dpi"] = float(m[1]) if m else None
    m = _search(r"\bTVPI\s*(?:of\s*)?(\d+(?:\.\d+)?)", text)
    row["tvpi"] = float(m[1]) if m else None
    m = _search(r"\b(?:NAV|value)\s*(?:of\s*)?(?:~|approx\.?\s*|c\.\s*)?" + AMT, text)
    row["value"] = money_millions(m[2], m[3]) if m else None
    if m and not row["currency"]:
        row["currency"] = currency_of(m[1])
    m = _search(r"unfunded\s*(?:of\s*)?" + AMT, text)
    row["unfunded"] = money_millions(m[2], m[3]) if m else None
    return row


def _header_index(header: list[str]) -> dict[str, int]:
    idx = {}
    norm = [h.strip().lower() for h in header]
    for field, names in HEADERS.items():
        for name in names:
            if name in norm:
                idx[field] = norm.index(name)
                break
    return idx


def parse_table(table: Table) -> list[dict]:
    if not table.rows:
        return []
    idx = _header_index(table.rows[0])
    if "label" not in idx or not ({"value", "dpi", "tvpi"} & idx.keys()):
        return []
    out = []
    for i, r in enumerate(table.rows[1:], start=1):
        cell = lambda f: (r[idx[f]].strip() if f in idx and idx[f] < len(r) else "")  # noqa: E731
        if not cell("label"):
            continue
        num = lambda f: parse_number(cell(f)) if cell(f) else None  # noqa: E731
        n = int(parse_number(cell("label")) or i)
        desc = cell("description")
        pc = num("pct_called")
        if pc is not None and (pc > 1.5 or "." not in cell("pct_called")):
            pc = pc / 100
        vint = num("vintage")
        out.append(dict(
            line_no=n, label=f"Fund {n}", description=desc,
            strategy=normalise_strategy(desc), region=normalise_region(cell("region") or desc),
            vintage=int(vint) if vint else None, size_band=cell("size_band") or None,
            currency=currency_of(cell("currency")), commitment=num("commitment"), pct_called=pc,
            dpi=num("dpi"), tvpi=num("tvpi"), value=num("value"), unfunded=num("unfunded"),
            name_fragment=cell("name_fragment") or None,
        ))
    return out


class OfflineParser:
    name = "offline-regex"
    version = "offline-v1"
    offline = True

    def parse(self, content: EmailContent) -> ParsedOffer:
        text = content.subject + "\n" + content.body
        code = _search(r"Project\s+([A-Z][A-Za-z]+)", text, 0)
        if not code:
            raise ParseError("no project code name found")
        ref = _search(r"reference date\s*[:\-]?\s*([^\n]+)", text)
        ref_date = parse_date(ref[1]) if ref else None
        if ref_date is None:
            raise ParseError("no reference date found")
        lines: list[dict] = []
        for raw in content.body.splitlines():
            m = LINE_RE.match(raw)
            if m:
                lines.append(parse_line_text(int(m[1]), m[2], m[3]))
        for t in content.tables:
            lines += parse_table(t)
        if not lines:
            raise ParseError("no fund lines found in the body or attachments")
        seller = next((label for key, label in SELLERS if key in content.body.lower()), None)
        total = _search(r"total nav[^\n\d]*?" + AMT, text)
        asking = _search(r"(?:pricing guidance|price guidance|asking price|guidance)\s*[:\-]?\s*([^\n]+)", text)
        asking_text = None
        if asking:
            asking_text = re.sub(r"\s*%?\s*of\s+nav.*$", "", asking[1].strip().rstrip("."), flags=re.I).strip()
        due = _search(r"bids?\s+(?:are\s+)?due\s*(?:by|on)?\s*[:\-]?\s*([^\n]+)", content.body)
        return ParsedOffer(
            code_name=code[1], seller_type=seller, reference_date=ref_date,
            currency=currency_of(total[1]) if total else "USD",
            total_value=money_millions(total[2], total[3]) if total else None,
            asking_price_text=asking_text, bid_deadline=parse_date(due[1]) if due else None,
            lines=lines,
        )
