from __future__ import annotations

import pandas as pd
from agents.dispatch_agent import BESSConfig, run_dispatch


def sweep_bess_durations(pml_pv: pd.DataFrame, bess_power_mw: float, durations_h: list[int], engine: str = "price_rank") -> pd.DataFrame:
    """Sweep BESS durations and rank by merchant revenue.

    engine: 'price_rank' (simple) or 'lp' (linear optimization).
    """
    rows = []
    for h in durations_h:
        cfg = BESSConfig(mw=bess_power_mw, mwh=bess_power_mw * h)
        dispatch = run_dispatch(pml_pv, cfg, engine=engine, discharge_hours_per_day=h)
        rows.append({
            "engine": engine,
            "bess_mw": bess_power_mw,
            "duration_h": h,
            "bess_mwh": bess_power_mw * h,
            "merchant_revenue": dispatch["merchant_revenue"].sum(),
            "bess_discharge_mwh": dispatch["bess_discharge_mwh"].sum(),
            "pv_to_grid_mwh": dispatch["pv_to_grid_mwh"].sum(),
        })
    return pd.DataFrame(rows).sort_values("merchant_revenue", ascending=False).reset_index(drop=True)


def best_configuration(scenarios: pd.DataFrame) -> dict:
    """Return the top scenario row as a plain dict."""
    if scenarios.empty:
        raise ValueError("No scenarios to choose from")
    return scenarios.iloc[0].to_dict()
