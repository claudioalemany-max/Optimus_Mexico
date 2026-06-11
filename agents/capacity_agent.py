"""Capacity / 100 Critical Hours agent.

Mexico's capacity market (Mercado para el Balance de Potencia) pays
resources for the average power they delivered during the 100 critical
hours of the year (the hours of highest system stress, published by
CENACE after the fact).

This agent:
1. Identifies the 100 critical hours, either from an official CENACE
   critical-hours file or, as a forward-looking proxy, the highest-PML
   hours of the dataset (price spikes track scarcity).
2. Computes the plant's accredited capacity = average MW delivered to
   the grid during those hours.
3. Computes capacity revenue = accredited MW x capacity price
   (MXN/MW-year, the Precio Neto de Potencia for the relevant zone).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

CRITICAL_HOURS_DEFAULT = 100


@dataclass
class CapacityConfig:
    capacity_price_mxn_mw_year: float
    n_critical_hours: int = CRITICAL_HOURS_DEFAULT
    criterion: str = "pml"  # "pml" proxy or "provided" official list


def identify_critical_hours(df: pd.DataFrame, n_hours: int = CRITICAL_HOURS_DEFAULT, criterion: str = "pml") -> pd.DataFrame:
    """Return the n critical hours as a DataFrame with a `critical_rank` column.

    criterion="pml": proxy ranking by highest PML (forward-looking estimate).
    criterion="provided": expects a boolean `is_critical_hour` column already
    set from the official CENACE publication.
    """
    out = df.copy()
    if criterion == "provided":
        if "is_critical_hour" not in out.columns:
            raise ValueError("criterion='provided' requires an is_critical_hour column")
        crit = out[out["is_critical_hour"].astype(bool)].copy()
        crit = crit.sort_values("pml", ascending=False).head(n_hours)
    else:
        crit = out.nlargest(n_hours, "pml").copy()
    crit["critical_rank"] = range(1, len(crit) + 1)
    return crit


def compute_capacity_credit(dispatch: pd.DataFrame, config: CapacityConfig) -> dict:
    """Compute accredited capacity and capacity revenue from a dispatch table.

    Expects dispatch columns: datetime, pml, pv_to_grid_mwh, bess_discharge_mwh.
    Hourly resolution means MWh delivered in an hour == average MW that hour.
    """
    required = {"datetime", "pml", "pv_to_grid_mwh", "bess_discharge_mwh"}
    missing = required - set(dispatch.columns)
    if missing:
        raise ValueError(f"Dispatch table missing columns: {sorted(missing)}")

    crit = identify_critical_hours(dispatch, config.n_critical_hours, config.criterion)
    delivered_mw = crit["pv_to_grid_mwh"].fillna(0) + crit["bess_discharge_mwh"].fillna(0)
    accredited_mw = float(delivered_mw.mean()) if len(crit) else 0.0
    revenue = accredited_mw * config.capacity_price_mxn_mw_year

    return {
        "n_critical_hours": len(crit),
        "criterion": config.criterion,
        "accredited_capacity_mw": round(accredited_mw, 3),
        "capacity_price_mxn_mw_year": config.capacity_price_mxn_mw_year,
        "capacity_revenue_mxn": round(revenue, 2),
        "critical_hours": crit,
    }


def capacity_summary_frame(result: dict) -> pd.DataFrame:
    return pd.DataFrame([
        {"metric": "Critical hours used", "value": result["n_critical_hours"]},
        {"metric": "Criterion", "value": result["criterion"]},
        {"metric": "Accredited capacity (MW)", "value": result["accredited_capacity_mw"]},
        {"metric": "Capacity price (MXN/MW-year)", "value": result["capacity_price_mxn_mw_year"]},
        {"metric": "Capacity revenue (MXN/year)", "value": result["capacity_revenue_mxn"]},
    ])


def write_capacity_workbook(result: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        capacity_summary_frame(result).to_excel(writer, sheet_name="Capacity_Summary", index=False)
        crit = result["critical_hours"].copy()
        crit.to_excel(writer, sheet_name="Critical_Hours", index=False)
    return out_path
