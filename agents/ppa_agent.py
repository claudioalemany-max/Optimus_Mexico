"""PPA / CFE mixed-development agent.

Models revenue under a mixed structure where part of the plant's energy
is sold under a fixed-price PPA (e.g. to CFE under a mixed-development
scheme or a private offtaker) and the remainder is settled merchant at
the nodal PML.

Allocation modes:
- "pro_rata":   every hour, `ppa_fraction` of delivered energy is PPA.
- "baseload":   the first `ppa_mw` of delivered energy each hour is PPA,
                anything above is merchant (typical contracted-block shape).
- "solar_only": PPA covers PV-to-grid energy only; BESS discharge is merchant
                (common when storage was added after the PPA was signed).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

ALLOCATION_MODES = ("pro_rata", "baseload", "solar_only")


@dataclass
class PPAConfig:
    ppa_price_mxn_mwh: float
    mode: str = "pro_rata"
    ppa_fraction: float = 0.7        # used by pro_rata
    ppa_mw: float = 0.0              # used by baseload
    annual_escalation: float = 0.0   # applied per contract-year if multi-year input
    start_year: int | None = None


def _delivered(df: pd.DataFrame) -> pd.Series:
    return df["pv_to_grid_mwh"].fillna(0) + df["bess_discharge_mwh"].fillna(0)


def apply_ppa(dispatch: pd.DataFrame, config: PPAConfig) -> pd.DataFrame:
    """Split delivered energy into PPA and merchant volumes and price them."""
    if config.mode not in ALLOCATION_MODES:
        raise ValueError(f"mode must be one of {ALLOCATION_MODES}")

    df = dispatch.copy()
    delivered = _delivered(df)

    if config.mode == "pro_rata":
        frac = min(max(config.ppa_fraction, 0.0), 1.0)
        df["ppa_mwh"] = delivered * frac
    elif config.mode == "baseload":
        df["ppa_mwh"] = delivered.clip(upper=max(config.ppa_mw, 0.0))
    else:  # solar_only
        df["ppa_mwh"] = df["pv_to_grid_mwh"].fillna(0)

    df["merchant_mwh"] = (delivered - df["ppa_mwh"]).clip(lower=0)

    years = pd.to_datetime(df["datetime"]).dt.year
    base_year = config.start_year or int(years.min())
    escalator = (1 + config.annual_escalation) ** (years - base_year).clip(lower=0)
    df["ppa_price_applied"] = config.ppa_price_mxn_mwh * escalator

    df["ppa_revenue"] = df["ppa_mwh"] * df["ppa_price_applied"]
    df["merchant_revenue"] = df["merchant_mwh"] * df["pml"]
    df["total_energy_revenue"] = df["ppa_revenue"] + df["merchant_revenue"]
    return df


def ppa_summary(priced: pd.DataFrame) -> pd.DataFrame:
    """Annual summary comparing PPA vs merchant performance."""
    df = priced.copy()
    df["year"] = pd.to_datetime(df["datetime"]).dt.year
    grouped = df.groupby("year", as_index=False).agg(
        ppa_mwh=("ppa_mwh", "sum"),
        merchant_mwh=("merchant_mwh", "sum"),
        ppa_revenue=("ppa_revenue", "sum"),
        merchant_revenue=("merchant_revenue", "sum"),
        total_energy_revenue=("total_energy_revenue", "sum"),
        avg_pml=("pml", "mean"),
    )
    delivered = grouped["ppa_mwh"] + grouped["merchant_mwh"]
    grouped["capture_price_mxn_mwh"] = (grouped["total_energy_revenue"] / delivered.replace(0, pd.NA)).astype(float).round(2)
    grouped["ppa_share_of_energy"] = (grouped["ppa_mwh"] / delivered.replace(0, pd.NA)).astype(float).round(4)
    return grouped


def compare_structures(dispatch: pd.DataFrame, configs: dict[str, PPAConfig]) -> pd.DataFrame:
    """Run several PPA structures over one dispatch and compare annual revenue."""
    rows = []
    for name, cfg in configs.items():
        priced = apply_ppa(dispatch, cfg)
        rows.append({
            "structure": name,
            "mode": cfg.mode,
            "ppa_price_mxn_mwh": cfg.ppa_price_mxn_mwh,
            "ppa_mwh": priced["ppa_mwh"].sum(),
            "merchant_mwh": priced["merchant_mwh"].sum(),
            "ppa_revenue": priced["ppa_revenue"].sum(),
            "merchant_revenue": priced["merchant_revenue"].sum(),
            "total_energy_revenue": priced["total_energy_revenue"].sum(),
        })
    return pd.DataFrame(rows).sort_values("total_energy_revenue", ascending=False).reset_index(drop=True)
