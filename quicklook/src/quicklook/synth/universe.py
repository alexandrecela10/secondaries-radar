"""Seeded generator for managers, funds, quarterly series, holdings and FX.

All names are invented. The generator knows the whole market. The pipeline only
sees "our" view, written to data/reference/:
  - managers and funds in our universe (funds we own plus funds we track),
  - quarterly series only for funds we own (we get their reports),
  - our holdings, FX rates.
Funds outside our universe exist only in data/market/ and are used to build
offers and ground truth labels.
"""
from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

REF_DATE = dt.date(2026, 6, 30)

MANAGER_BASES = [
    "Halvane", "Corrisk", "Tellmar", "Ostrova", "Brennick", "Quillon", "Marrowby", "Fenlow",
    "Arkwell", "Selbrook", "Dunmere", "Varrow", "Orlaith", "Wexcombe", "Thornhale", "Caskel",
    "Mirefield", "Pellam", "Rookhurst", "Stavlen", "Ennor", "Gildmoor", "Hartsell", "Ivelle",
    "Jorvane", "Kittering", "Lowmarch", "Nethervale", "Osgrove", "Penvarrow", "Quenby",
    "Ravelstone", "Sutterby", "Tarnwick", "Ulverly", "Vespergate", "Wrenhollow", "Yarrowdale",
    "Zelmore", "Ambrel",
]
SUFFIX = {"buyout": "Capital Partners", "growth": "Growth Partners", "venture": "Ventures",
          "credit": "Credit Partners", "infra": "Infrastructure"}
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]

# Strategy shapes for the J-curve. tau: years to call ~63% of cap.
# mult: (mean, sd) of the final TVPI. centre: year by which half the value is realised.
SHAPES = {
    "buyout": dict(tau=1.8, cap=(0.90, 1.00), mult=(1.85, 0.35), centre=9.0, jdip=0.15, size=(1500, 0.6)),
    "growth": dict(tau=1.6, cap=(0.88, 0.98), mult=(1.95, 0.50), centre=9.5, jdip=0.12, size=(900, 0.5)),
    "venture": dict(tau=2.5, cap=(0.85, 0.97), mult=(2.10, 0.90), centre=11.0, jdip=0.25, size=(450, 0.5)),
    "credit": dict(tau=1.0, cap=(0.80, 0.95), mult=(1.35, 0.10), centre=4.5, jdip=0.05, size=(1100, 0.5)),
    "infra": dict(tau=2.0, cap=(0.90, 1.00), mult=(1.60, 0.20), centre=10.0, jdip=0.10, size=(2000, 0.5)),
}
REGION_WEIGHTS = {"north_america": 0.5, "europe": 0.32, "asia": 0.12, "global": 0.06}
STRATEGY_WEIGHTS = {"buyout": 0.42, "growth": 0.18, "venture": 0.15, "credit": 0.13, "infra": 0.12}

# Funds planted so the worked examples in the PRD and README exist for any seed.
# Ratios are targets at REF_DATE: (pct_called, dpi, tvpi).
PLANTED = {
    "A": dict(strategy="buyout", region="europe", vintage=2016, owned=True, target=(0.985, 1.21, 1.96), currency="EUR"),
    "B": dict(strategy="buyout", region="europe", vintage=2016, owned=True, target=(0.924, 0.842, 1.712), currency="EUR"),
    "C": dict(strategy="buyout", region="europe", vintage=2016, owned=True, target=(0.881, 0.552, 1.448), currency="EUR"),
    "F9a": dict(strategy="growth", region="north_america", vintage=2019, owned=False, target=None, currency="USD"),
    "F9b": dict(strategy="growth", region="north_america", vintage=2019, owned=False, target=None, currency="USD"),
}


@dataclass
class Manager:
    manager_id: str
    name: str
    strategy: str
    region: str
    in_universe: bool


@dataclass
class Fund:
    fund_id: str
    manager_id: str
    name: str
    vintage: int
    strategy: str
    region: str
    currency: str
    size_m: float
    status: str  # owned | tracked | outside
    planted: str | None = None
    shape: dict = field(default_factory=dict)


def quarter_ends(start_year: int, end: dt.date) -> list[dt.date]:
    out = []
    y, q = start_year, 1
    while True:
        m = q * 3
        d = dt.date(y, m, 31 if m in (3, 12) else 30)
        if d > end:
            return out
        out.append(d)
        q += 1
        if q == 5:
            y, q = y + 1, 1


def size_band(size_m: float) -> str:
    if size_m < 500:
        return "<$500M"
    if size_m < 1000:
        return "$500M-1bn"
    if size_m < 2500:
        return "$1-2.5bn"
    if size_m < 5000:
        return "$2.5-5bn"
    return ">$5bn"


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def ratio_path(fund: Fund, dates: list[dt.date], rng: random.Random) -> list[tuple[float, float, float]]:
    """Return (pct_called, dpi, tvpi) per quarter: a J-curve."""
    s = fund.shape
    cap, tau, m, centre, jdip = s["cap"], s["tau"], s["mult"], s["centre"], s["jdip"]
    out = []
    noise = 0.0
    for d in dates:
        a = (d - dt.date(fund.vintage, 1, 1)).days / 365.25
        called = cap * (1 - math.exp(-a / tau))
        growth = 1 / (1 + math.exp(-(a - 5.5) / 1.5)) - 1 / (1 + math.exp(5.5 / 1.5))
        noise += rng.gauss(0, 0.012)
        tvpi = 1 - jdip * math.exp(-a / 1.5) + (m - 1) * growth + noise * min(1.0, a / 3)
        realised = 0.97 / (1 + math.exp(-(a - centre) / 1.4)) - 0.97 / (1 + math.exp(centre / 1.4))
        dpi = max(0.0, m * realised)
        tvpi = max(tvpi, dpi + 0.02)
        out.append((called, dpi, tvpi))
    # Distributions only go up.
    best = 0.0
    fixed = []
    for c, d_, t in out:
        best = max(best, d_)
        fixed.append((c, best, max(t, best + 0.02)))
    return fixed


def plant_targets(path: list[tuple[float, float, float]], target: tuple[float, float, float]) -> list[tuple[float, float, float]]:
    """Rescale a path so the last point hits the target ratios exactly."""
    c_end, d_end, t_end = path[-1]
    tc, td, tt = target
    n = len(path)
    out = []
    for i, (c, d, t) in enumerate(path):
        w = (i + 1) / n
        c2 = c * tc / c_end
        d2 = d * (td / d_end if d_end > 0 else 1)
        t2 = t + (tt - t_end) * w
        out.append((c2, d2, max(t2, d2 + 0.02)))
    out[-1] = (tc, td, tt)
    return out


def generate(seed: int = 42, n_managers: int = 40, n_funds: int = 120, n_owned: int = 60,
             n_outside_managers: int = 8) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    managers: list[Manager] = []
    for i, base in enumerate(MANAGER_BASES[:n_managers]):
        strat = _pick(rng, STRATEGY_WEIGHTS)
        region = _pick(rng, REGION_WEIGHTS)
        managers.append(Manager(f"M{i + 1:02d}", f"{base} {SUFFIX[strat]}", strat, region,
                                in_universe=i < n_managers - n_outside_managers))
    # Managers that host planted funds: fixed strategy and region.
    planted_mgr = {"B": 0, "A": 1, "C": 2, "F9a": 3, "F9b": 4}
    for key, idx in planted_mgr.items():
        p = PLANTED[key]
        m = managers[idx]
        m.strategy, m.region = p["strategy"], p["region"]
        m.name = f"{MANAGER_BASES[idx]} {SUFFIX[p['strategy']]}"

    planted_keys = {(p["vintage"], p["strategy"], p["region"]) for p in PLANTED.values()}
    funds: list[Fund] = []
    counts = {m.manager_id: 0 for m in managers}

    def new_fund(m: Manager, vintage: int, planted: str | None = None) -> Fund:
        counts[m.manager_id] += 1
        strat = m.strategy
        sh = SHAPES[strat]
        cur = "USD"
        if m.region == "europe":
            cur = "GBP" if rng.random() < 0.25 else "EUR"
        if planted:
            cur = PLANTED[planted]["currency"]
        mean, sd = sh["size"]
        size = round(mean * math.exp(rng.gauss(0, sd)) * (1 + 0.15 * (counts[m.manager_id] - 1)), -1)
        shape = dict(tau=sh["tau"] * rng.uniform(0.85, 1.15), cap=rng.uniform(*sh["cap"]),
                     mult=max(0.6, rng.gauss(*sh["mult"])), centre=sh["centre"] + rng.uniform(-1, 1),
                     jdip=sh["jdip"])
        base = m.name.replace(" " + SUFFIX[strat], "")
        name = f"{base} {SUFFIX[strat]} {ROMAN[counts[m.manager_id] - 1]}"
        return Fund(f"F{len(funds) + 1:03d}", m.manager_id, name, vintage, strat, m.region, cur,
                    max(size, 100.0), "tbd", planted, shape)

    # Planted funds first, as the latest fund of their manager.
    for key, idx in planted_mgr.items():
        m = managers[idx]
        for v in (PLANTED[key]["vintage"] - 6, PLANTED[key]["vintage"] - 3):
            if (v, m.strategy, m.region) not in planted_keys:
                funds.append(new_fund(m, v))
        funds.append(new_fund(m, PLANTED[key]["vintage"], planted=key))

    # Remaining funds: each manager raises a fund about every 3 years, vintages 2008 to 2024.
    slots = []
    for m in managers[5:]:
        v = rng.randint(2008, 2015)
        while v <= 2024 and len([s for s in slots if s[0] is m]) < 5:
            if (v, m.strategy, m.region) not in planted_keys:
                slots.append((m, v))
            v += rng.choice([2, 3, 3, 4])
    chosen = rng.sample(slots, n_funds - len(funds))
    chosen.sort(key=lambda s: (s[0].manager_id, s[1]))
    for m, v in chosen:
        funds.append(new_fund(m, v))

    # Ownership: n_owned funds we hold among universe managers; planted flags win.
    mgr_in = {m.manager_id: m.in_universe for m in managers}
    universe = [f for f in funds if mgr_in[f.manager_id]]
    for f in funds:
        f.status = "tracked" if mgr_in[f.manager_id] else "outside"
    pool = [f for f in universe if f.planted is None]
    rng.shuffle(pool)
    n_planted_owned = sum(1 for p in PLANTED.values() if p["owned"])
    for f in pool[: n_owned - n_planted_owned]:
        f.status = "owned"
    for f in funds:
        if f.planted:
            f.status = "owned" if PLANTED[f.planted]["owned"] else "tracked"

    # Series, reports and restatements.
    series = []
    for f in funds:
        dates = quarter_ends(f.vintage, REF_DATE)
        path = ratio_path(f, dates, rng)
        if f.planted and PLANTED[f.planted]["target"]:
            path = plant_targets(path, PLANTED[f.planted]["target"])
        for d, (c, dp, tv) in zip(dates, path):
            called = f.size_m * c
            dist = called * dp
            nav = called * (tv - dp)
            rep = d + dt.timedelta(days=rng.randint(45, 88))
            sid = f"{f.fund_id}:{d.isoformat()}:0"
            series.append(dict(row_id=sid, fund_id=f.fund_id, as_of_date=d, reported_at=rep,
                               called=round(called, 4), distributed=round(dist, 4), nav=round(nav, 4),
                               restatement=0))
            # About 6% of reports are restated later, with a small value change.
            if rng.random() < 0.06 and not f.planted:
                rep2 = rep + dt.timedelta(days=rng.randint(80, 130))
                series.append(dict(row_id=f"{f.fund_id}:{d.isoformat()}:1", fund_id=f.fund_id, as_of_date=d,
                                   reported_at=rep2, called=round(called, 4), distributed=round(dist, 4),
                                   nav=round(nav * rng.uniform(0.97, 1.03), 4), restatement=1))

    holdings = []
    for f in funds:
        if f.status == "owned":
            commit = round(rng.uniform(10, 60) * 2) / 2
            holdings.append(dict(holding_id=f"H{f.fund_id}", fund_id=f.fund_id, commitment=commit,
                                 currency=f.currency))

    fx = []
    eur, gbp = 1.12, 1.30
    for y in range(2008, 2027):
        for mth in range(1, 13):
            d = (dt.date(y, mth, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
            if d > dt.date(2026, 10, 31):
                break
            eur = min(1.45, max(0.98, eur * math.exp(rng.gauss(0, 0.02))))
            gbp = min(1.65, max(1.15, gbp * math.exp(rng.gauss(0, 0.02))))
            if d == REF_DATE:
                eur, gbp = 1.08, 1.27  # fixed so the worked example reads cleanly
            fx.append(dict(row_id=f"EUR:{d}", currency="EUR", rate_date=d, usd_per_unit=round(eur, 4)))
            fx.append(dict(row_id=f"GBP:{d}", currency="GBP", rate_date=d, usd_per_unit=round(gbp, 4)))
            fx.append(dict(row_id=f"USD:{d}", currency="USD", rate_date=d, usd_per_unit=1.0))

    mrows = [asdict(m) for m in managers]
    frows = []
    for f in funds:
        r = asdict(f)
        r.pop("shape")
        r["size_band"] = size_band(f.size_m)
        frows.append(r)
    return dict(managers=mrows, funds=frows, fund_series=series, holdings=holdings, fx_rates=fx)


def write_parquet(rows: list[dict], path: Path) -> None:
    """Write a list of dicts to parquet through DuckDB (no pandas needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    cols = list(rows[0])
    types = []
    for c in cols:
        v = rows[0][c]
        if isinstance(v, bool):
            types.append("BOOLEAN")
        elif isinstance(v, int):
            types.append("BIGINT")
        elif isinstance(v, float):
            types.append("DOUBLE")
        elif isinstance(v, dt.date):
            types.append("DATE")
        else:
            types.append("VARCHAR")
    con.execute(f"CREATE TABLE t ({', '.join(f'{c} {t}' for c, t in zip(cols, types))})")
    con.executemany(f"INSERT INTO t VALUES ({', '.join('?' for _ in cols)})",
                    [[r[c] for c in cols] for r in rows])
    con.execute(f"COPY (SELECT * FROM t ORDER BY 1) TO '{path}' (FORMAT PARQUET)")
    con.close()


def write_all(data: dict[str, list[dict]], data_dir: Path) -> None:
    """market/ holds everything (generator only). reference/ is what the pipeline sees."""
    for name, rows in data.items():
        write_parquet(rows, data_dir / "market" / f"{name}.parquet")
    status = {f["fund_id"]: f["status"] for f in data["funds"]}
    in_uni = {m["manager_id"] for m in data["managers"] if m["in_universe"]}
    ref = dict(
        managers=[m for m in data["managers"] if m["in_universe"]],
        funds=[{k: v for k, v in f.items() if k not in ("planted",)} for f in data["funds"]
               if f["manager_id"] in in_uni],
        fund_series=[s for s in data["fund_series"] if status[s["fund_id"]] == "owned"],
        holdings=data["holdings"],
        fx_rates=data["fx_rates"],
    )
    for f in ref["funds"]:
        f["owned"] = f.pop("status") == "owned"
    for name, rows in ref.items():
        write_parquet(rows, data_dir / "reference" / f"{name}.parquet")
