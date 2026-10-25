"""What happened after the reference date, in funds we own: the price check's input.

As an investor in a fund we get its letters and notices between quarterly reports: a company
sold above or below its last value, a write-down. Brokers price an offer from the value at the
reference date, so these events are what we know and most bidders do not.

reference/fund_events.parquet: events, each with the date we were told (point in time).
market/fund_next.parquet: each owned fund's value at the next quarter end. The generator knows it;
the pipeline never sees it. Only the eval reads it, to check our estimate against what came next.
"""
from __future__ import annotations

import datetime as dt
import math
import random

from .universe import REF_DATE

NEXT_DATE = dt.date(2026, 9, 30)
# Invented company names: a word and a sector, drawn without repeats.
NAME_WORDS = ["Arcwright", "Brisa", "Lumen", "Ostler", "Tamsin", "Verity", "Halden", "Quarry", "Wickham", "Ember",
              "Fallow", "Grist", "Juno", "Marl", "Neve", "Pellucid", "Corran", "Delph", "Esk", "Fintry", "Garrow",
              "Hesket", "Inver", "Kelso", "Lathe", "Morven", "Nairn", "Orrin", "Petra", "Rillo"]
SECTORS = ["Software", "Health", "Foods", "Dental", "Analytics", "Labs", "Marine", "Fibre", "Tools", "Education",
           "Agritech", "Payments", "Chemicals", "Clinics", "Logistics", "Pet Care", "Testing", "Packaging"]

# Planted so the worked examples exist for any seed: (offer, line_no) -> events on that line's fund.
# share: the company's share of the fund's value at the reference date. factor: new value / old value.
PLANTED = {
    ("Cedar", 1): [dict(kind="exit", share=0.20, factor=1.60, day=dt.date(2026, 8, 12), told=dt.date(2026, 8, 19),
                        company="Nordhavn Logistics")],
    ("Cedar", 4): [dict(kind="exit", share=0.15, factor=1.55, day=dt.date(2026, 7, 28), told=dt.date(2026, 8, 6),
                        company="Kestrel Testing")],
    ("Hornbeam", 1): [dict(kind="write_down", share=0.30, factor=0.40, day=dt.date(2026, 9, 2),
                           told=dt.date(2026, 9, 10), company="Calder Packaging")],
    ("Hornbeam", 2): [dict(kind="write_down", share=0.20, factor=0.50, day=dt.date(2026, 8, 20),
                           told=dt.date(2026, 9, 1), company="Sable Security")],
}


def _note(kind: str, company: str, factor: float, day: dt.date) -> str:
    if kind == "exit":
        return f"Sale of {company} agreed on {day:%d %B}, at {factor:.2f} times its 30 June value."
    return f"{company} written down on {day:%d %B} to {factor:.2f} times its 30 June value, after a weak trading update."


def generate_events(data: dict[str, list[dict]], labels: list[dict], seed: int = 42) -> dict[str, list[dict]]:
    """Events for funds we own, and their value at the next quarter end. A separate random stream, so
    adding events changes nothing else in the synthetic world."""
    rng = random.Random(seed + 7)
    owned = sorted(f["fund_id"] for f in data["funds"] if f["status"] == "owned")
    nav = {s["fund_id"]: s["nav"] for s in data["fund_series"] if s["as_of_date"] == REF_DATE and s["restatement"] == 0}
    by_code = {l["code"]: l for l in labels}
    planted: dict[str, list[dict]] = {}
    for (code, line_no), evs in PLANTED.items():
        line = next(x for x in by_code[code]["lines"] if x["line_no"] == line_no)
        planted.setdefault(line["true_fund_id"], []).extend(evs)

    taken = {e["company"] for evs in PLANTED.values() for e in evs}
    names = [f"{w} {sec}" for w in NAME_WORDS for sec in SECTORS]
    rng.shuffle(names)
    names = [n for n in names if n not in taken and n.split()[0] not in {t.split()[0] for t in taken}]
    events, nxt = [], []
    for fid in owned:
        if fid not in nav or nav[fid] <= 0:
            continue
        evs = list(planted.get(fid, []))
        if not evs:
            for _ in range(rng.choices([0, 1, 2], [0.7, 0.24, 0.06])[0]):
                exit_ = rng.random() < 0.6
                day = REF_DATE + dt.timedelta(days=rng.randint(5, 107))
                evs.append(dict(kind="exit" if exit_ else "write_down",
                                share=rng.uniform(0.04, 0.12) if exit_ else rng.uniform(0.03, 0.10),
                                factor=rng.uniform(1.15, 1.6) if exit_ else rng.uniform(0.4, 0.8),
                                day=day, told=day + dt.timedelta(days=rng.randint(3, 20)),
                                company=names.pop()))
        change = 0.0
        for i, e in enumerate(evs, start=1):
            before = nav[fid] * e["share"]
            after = before * e["factor"]
            change += after - before
            events.append(dict(event_id=f"{fid}:E{i}", fund_id=fid, kind=e["kind"], company=e["company"],
                               event_date=e["day"], reported_at=e["told"], value_before=round(before, 4),
                               value_after=round(after, 4), fund_value_ref=round(nav[fid], 4),
                               ref_date=REF_DATE, note=_note(e["kind"], e["company"], e["factor"], e["day"])))
        # Next quarter: the events, plus the drift nobody told us about.
        nxt.append(dict(fund_id=fid, as_of_date=NEXT_DATE, fund_value_ref=round(nav[fid], 4),
                        nav_next=round((nav[fid] + change) * math.exp(rng.gauss(0, 0.025)), 4),
                        n_events=len(evs)))
    return dict(fund_events=events, fund_next=nxt)
