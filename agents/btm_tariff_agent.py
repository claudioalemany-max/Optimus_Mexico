"""Behind-the-Meter tariff engine (CFE basic-supply tariffs).

Implements the spec in Optimus_Mexico_Behind_TheMeter_Developer_Ready_v2:
- 15-minute tariff calendar (season / day_type / period) for GDMTH (primary)
  and GDMTO, driven by data/reference/tariff_periods.csv.
- Official-holiday generation (LFT Article 74).
- Baseline CFE bill reconstruction with the CNE 2026 formulas:
  capacity_billing_kw     = min(Dmaxpunta, Q / (24 * days * factor_carga))
  distribution_billing_kw = min(Dmaxmensual, Q / (24 * days * factor_carga))
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from pathlib import Path

import pandas as pd

REFERENCE_DIR = Path("data/reference")
INTERVAL_HOURS = 0.25

FACTOR_CARGA_DEFAULT = {"GDMTO": 0.55, "GDMTH": 0.57, "DIST": 0.74, "DIT": 0.71}


@dataclass
class CustomerConfig:
    customer_name: str = "Cliente Industrial"
    state: str = "Ciudad de Mexico"
    municipality: str = "Coyoacan"
    cfe_division: str = "Valle de Mexico Sur"
    tariff: str = "GDMTH"
    contracted_demand_kw: float = 500.0
    connected_load_kw: float = 800.0
    power_factor_target: float = 0.90


# ---------------------------------------------------------------- holidays

def mexico_official_holidays(year: int) -> list[date]:
    """LFT Article 74 mandatory rest days (statutory logic, no scraping)."""

    def nth_weekday(month: int, weekday: int, n: int) -> date:
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + timedelta(days=offset + 7 * (n - 1))

    holidays = [
        date(year, 1, 1),
        nth_weekday(2, 0, 1),   # first Monday of February (Constitution Day)
        nth_weekday(3, 0, 3),   # third Monday of March (Benito Juarez)
        date(year, 5, 1),
        date(year, 9, 16),
        nth_weekday(11, 0, 3),  # third Monday of November (Revolution Day)
        date(year, 12, 25),
    ]
    if (year - 2024) % 6 == 0:  # federal executive transmission day
        holidays.append(date(year, 10, 1))
    return sorted(holidays)


# ---------------------------------------------------------------- calendar

def map_division_to_system(division: str) -> str:
    d = division.lower()
    if "baja california sur" in d:
        return "BCS"
    if "baja california" in d:
        return "BC"
    return "SIN"


def classify_season(ts: pd.Timestamp, tariff: str, system: str) -> str:
    if tariff == "GDMTO":
        return "all"
    # GDMTH starter rule (configurable): summer = April-October.
    return "summer" if 4 <= ts.month <= 10 else "winter"


def classify_day_type(ts: pd.Timestamp, is_holiday: bool) -> str:
    if is_holiday or ts.weekday() == 6:
        return "sunday_holiday"
    if ts.weekday() == 5:
        return "saturday"
    return "weekday"


def load_period_rules(path: str | Path | None = None) -> pd.DataFrame:
    path = Path(path) if path else REFERENCE_DIR / "tariff_periods.csv"
    rules = pd.read_csv(path)
    rules["start_time"] = rules["start_time"].astype(str)
    rules["end_time"] = rules["end_time"].astype(str)
    return rules


def build_tariff_calendar(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    tariff: str = "GDMTH",
    division: str = "Valle de Mexico Sur",
    holidays: list[date] | None = None,
    period_rules: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Label every 15-minute interval with system, season, day_type, period."""
    system = map_division_to_system(division)
    rules = period_rules if period_rules is not None else load_period_rules()
    rules = rules[(rules["tariff"] == tariff) & (rules["system"] == system)]
    if rules.empty:
        raise ValueError(f"No period rules for tariff={tariff} system={system}")

    idx = pd.date_range(start, end, freq="15min", inclusive="left")
    if holidays is None:
        holidays = []
        for year in sorted({ts.year for ts in [idx[0], idx[-1]]}):
            holidays.extend(mexico_official_holidays(year))
    holiday_set = set(holidays)

    # Pre-index rules: (season, day_type) -> list of (start_minutes, end_minutes, period)
    rule_map: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
    for _, r in rules.iterrows():
        sh, sm = map(int, r["start_time"].split(":"))
        eh, em = map(int, r["end_time"].split(":"))
        rule_map.setdefault((r["season"], r["day_type"]), []).append(
            (sh * 60 + sm, eh * 60 + em, r["period"])
        )

    rows = []
    for ts in idx:
        is_holiday = ts.date() in holiday_set
        day_type = classify_day_type(ts, is_holiday)
        season = classify_season(ts, tariff, system)
        minutes = ts.hour * 60 + ts.minute
        period = None
        for lo, hi, p in rule_map.get((season, day_type), []):
            if lo <= minutes < hi:
                period = p
                break
        if period is None:
            raise ValueError(f"Calendar gap: no period rule covers {ts} ({season}/{day_type})")
        rows.append((ts, system, season, day_type, period, is_holiday))
    return pd.DataFrame(rows, columns=["timestamp", "system", "season", "day_type", "period", "is_holiday"])


# ---------------------------------------------------------------- rates

@dataclass
class TariffRates:
    """Monthly rates for one tariff/division. Values in MXN."""
    fixed_month: float
    energy: dict[str, float]          # period -> $/kWh
    distribution_kw: float
    capacity_kw: float
    factor_carga: float

    @classmethod
    def from_table(cls, rates: pd.DataFrame, tariff: str = "GDMTH",
                   division: str | None = None, year: int | None = None,
                   month: int | None = None) -> "TariffRates":
        df = rates[rates["tariff"] == tariff]
        if division is not None and "division" in df.columns:
            sub = df[df["division"] == division]
            df = sub if not sub.empty else df
        if year is not None and "year" in df.columns and (df["year"] == year).any():
            df = df[df["year"] == year]
        if month is not None and "month" in df.columns and (df["month"] == month).any():
            df = df[df["month"] == month]

        def one(component: str, default: float = 0.0) -> float:
            sub = df[df["charge_component"] == component]
            return float(sub["value_mxn"].iloc[0]) if len(sub) else default

        energy = {
            str(r["period"]): float(r["value_mxn"])
            for _, r in df[df["charge_component"] == "energy"].iterrows()
        }
        return cls(
            fixed_month=one("fixed"),
            energy=energy,
            distribution_kw=one("distribution"),
            capacity_kw=one("capacity"),
            factor_carga=FACTOR_CARGA_DEFAULT.get(tariff, 0.57),
        )


def load_starter_rates(path: str | Path | None = None) -> pd.DataFrame:
    path = Path(path) if path else REFERENCE_DIR / "tariff_rates_starter.csv"
    return pd.read_csv(path)


# ---------------------------------------------------------------- bill engine

@dataclass
class MonthlyBill:
    year: int
    month: int
    billing_days: int
    kwh_by_period: dict[str, float]
    dmax_mensual_kw: float
    dmax_punta_kw: float
    capacity_billing_kw: float
    distribution_billing_kw: float
    energy_charge: float
    capacity_charge: float
    distribution_charge: float
    fixed_charge: float

    @property
    def total(self) -> float:
        return self.energy_charge + self.capacity_charge + self.distribution_charge + self.fixed_charge

    def as_dict(self) -> dict:
        d = {
            "year": self.year, "month": self.month, "billing_days": self.billing_days,
            "dmax_mensual_kw": self.dmax_mensual_kw, "dmax_punta_kw": self.dmax_punta_kw,
            "capacity_billing_kw": round(self.capacity_billing_kw, 2),
            "distribution_billing_kw": round(self.distribution_billing_kw, 2),
            "energy_charge": round(self.energy_charge, 2),
            "capacity_charge": round(self.capacity_charge, 2),
            "distribution_charge": round(self.distribution_charge, 2),
            "fixed_charge": round(self.fixed_charge, 2),
            "total": round(self.total, 2),
        }
        for p, kwh in self.kwh_by_period.items():
            d[f"kwh_{p}"] = round(kwh, 1)
        return d


def compute_monthly_bill(df: pd.DataFrame, rates: TariffRates, load_col: str = "load_kw") -> MonthlyBill:
    """Bill one calendar month of 15-minute data labeled with `period`."""
    ts = pd.to_datetime(df["timestamp"])
    year, month = int(ts.dt.year.iloc[0]), int(ts.dt.month.iloc[0])
    billing_days = int(ts.dt.date.nunique())
    load = pd.to_numeric(df[load_col], errors="coerce").fillna(0).clip(lower=0)

    kwh_by_period = (load * INTERVAL_HOURS).groupby(df["period"]).sum().to_dict()
    q_mensual = float(sum(kwh_by_period.values()))
    dmax_mensual = math.ceil(load.max()) if len(load) else 0
    punta_load = load[df["period"] == "punta"]
    dmax_punta = math.ceil(punta_load.max()) if len(punta_load) else 0

    formula_kw = q_mensual / (24 * billing_days * rates.factor_carga) if billing_days else 0.0
    capacity_billing = min(dmax_punta, formula_kw) if dmax_punta > 0 else formula_kw
    distribution_billing = min(dmax_mensual, formula_kw)

    energy_charge = float(sum(kwh * rates.energy.get(p, 0.0) for p, kwh in kwh_by_period.items()))
    return MonthlyBill(
        year=year, month=month, billing_days=billing_days,
        kwh_by_period=kwh_by_period,
        dmax_mensual_kw=dmax_mensual, dmax_punta_kw=dmax_punta,
        capacity_billing_kw=capacity_billing,
        distribution_billing_kw=distribution_billing,
        energy_charge=energy_charge,
        capacity_charge=capacity_billing * rates.capacity_kw,
        distribution_charge=distribution_billing * rates.distribution_kw,
        fixed_charge=rates.fixed_month,
    )


def reconstruct_annual_bill(load_15min: pd.DataFrame, calendar: pd.DataFrame,
                            rates: TariffRates, load_col: str = "load_kw") -> pd.DataFrame:
    """Merge load with calendar and bill every month. Returns one row per month."""
    df = load_15min.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.merge(calendar, on="timestamp", how="inner")
    if df.empty:
        raise ValueError("No overlap between load data and tariff calendar")
    df["_ym"] = df["timestamp"].dt.to_period("M")
    bills = [compute_monthly_bill(g, rates, load_col=load_col) for _, g in df.groupby("_ym", sort=True)]
    return pd.DataFrame([b.as_dict() for b in bills])


def bill_totals(bills: pd.DataFrame) -> dict[str, float]:
    return {
        "energy": float(bills["energy_charge"].sum()),
        "capacity": float(bills["capacity_charge"].sum()),
        "distribution": float(bills["distribution_charge"].sum()),
        "fixed": float(bills["fixed_charge"].sum()),
        "total": float(bills["total"].sum()),
    }


def gdmto_migration_check(load_15min: pd.DataFrame) -> pd.DataFrame:
    """Flag months where measured demand reaches/exceeds 100 kW (GDMTO -> GDMTH)."""
    df = load_15min.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["_ym"] = df["timestamp"].dt.to_period("M")
    out = df.groupby("_ym").agg(peak_kw=("load_kw", "max")).reset_index()
    out["gdmth_migration_flag"] = out["peak_kw"] >= 100.0
    out["_ym"] = out["_ym"].astype(str)
    return out.rename(columns={"_ym": "month"})
