from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class BESSConfig:
    mw: float
    mwh: float
    rte: float = 0.925
    reserve_fraction: float = 0.10
    allow_grid_charging: bool = False
    daily_cycle_limit: float = 1.0


def dispatch_price_rank(pml_pv: pd.DataFrame, bess: BESSConfig, discharge_hours_per_day: int | None = None) -> pd.DataFrame:
    """Simple transparent dispatch engine.

    Priority:
    1) PV generation sells to grid unless used to charge BESS.
    2) Charge BESS from PV surplus in low-price daylight hours.
    3) Discharge in the highest-price hours of each day.

    This is intentionally simple and auditable; the optimizer can replace it
    later with linear programming.
    """
    df = pml_pv.copy().sort_values("datetime").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["datetime"]).dt.date
    df["hour"] = pd.to_datetime(df["datetime"]).dt.hour + 1
    df["pv_mwh"] = pd.to_numeric(df.get("pv_mwh", 0), errors="coerce").fillna(0).clip(lower=0)
    df["pml"] = pd.to_numeric(df["pml"], errors="coerce")

    discharge_n = discharge_hours_per_day or max(1, int(round(bess.mwh / max(bess.mw, 1))))
    df["is_discharge_hour"] = False
    for day, idx in df.groupby("date").groups.items():
        sub = df.loc[list(idx)]
        top = sub.nlargest(discharge_n, "pml").index
        df.loc[top, "is_discharge_hour"] = True

    soc = bess.mwh * bess.reserve_fraction
    min_soc = bess.mwh * bess.reserve_fraction
    max_soc = bess.mwh
    rows = []
    for _, row in df.iterrows():
        pv = float(row["pv_mwh"])
        charge = 0.0
        discharge = 0.0
        pv_to_grid = pv
        if not row["is_discharge_hour"]:
            available_space = max_soc - soc
            charge = min(pv, bess.mw, available_space)
            soc += charge
            pv_to_grid -= charge
        else:
            available = max(0.0, soc - min_soc)
            discharge = min(bess.mw, available * bess.rte)
            soc -= discharge / bess.rte
        rows.append({
            "soc_mwh": soc,
            "pv_to_bess_mwh": charge,
            "bess_discharge_mwh": discharge,
            "pv_to_grid_mwh": pv_to_grid,
        })
    add = pd.DataFrame(rows)
    out = pd.concat([df, add], axis=1)
    out["merchant_revenue"] = (out["pv_to_grid_mwh"] + out["bess_discharge_mwh"]) * out["pml"]
    return out


def dispatch_lp(pml_pv: pd.DataFrame, bess: BESSConfig) -> pd.DataFrame:
    """Linear-programming dispatch, solved day by day with SOC carry-over.

    Maximizes hourly revenue pml * (pv - charge + discharge) subject to:
    - power limits on charge and discharge
    - SOC dynamics with one-way efficiency sqrt(RTE)
    - SOC bounds (reserve floor to full capacity)
    - daily cycle (throughput) limit
    - PV-only charging unless allow_grid_charging is set

    Falls back to `dispatch_price_rank` if scipy is unavailable.
    """
    try:
        from scipy.optimize import linprog
    except ImportError:
        return dispatch_price_rank(pml_pv, bess)

    df = pml_pv.copy().sort_values("datetime").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["datetime"]).dt.date
    df["hour"] = pd.to_datetime(df["datetime"]).dt.hour + 1
    df["pv_mwh"] = pd.to_numeric(df.get("pv_mwh", 0), errors="coerce").fillna(0).clip(lower=0)
    df["pml"] = pd.to_numeric(df["pml"], errors="coerce").fillna(0)

    eta = float(np.sqrt(max(min(bess.rte, 1.0), 0.01)))
    min_soc = bess.mwh * bess.reserve_fraction
    max_soc = bess.mwh
    usable = max(max_soc - min_soc, 1e-9)
    soc0 = min_soc

    charge_all, discharge_all, soc_all = [], [], []

    for _, day_df in df.groupby("date", sort=True):
        n = len(day_df)
        pml = day_df["pml"].to_numpy()
        pv = day_df["pv_mwh"].to_numpy()

        # Variables: [c_0..c_{n-1}, d_0..d_{n-1}, s_0..s_{n-1}]
        nc, nd = n, n
        nv = 3 * n
        cost = np.zeros(nv)
        cost[:nc] = pml          # charging forgoes selling at pml
        cost[nc:nc + nd] = -pml  # discharging earns pml (minimize negative)

        # SOC dynamics: s_t - s_{t-1} - eta*c_t + d_t/eta = 0
        a_eq = np.zeros((n, nv))
        b_eq = np.zeros(n)
        for t in range(n):
            a_eq[t, 2 * n + t] = 1.0
            if t > 0:
                a_eq[t, 2 * n + t - 1] = -1.0
            else:
                b_eq[t] = soc0
            a_eq[t, t] = -eta
            a_eq[t, n + t] = 1.0 / eta

        # Daily throughput limit: sum(d_t)/eta <= cycle_limit * usable
        a_ub = np.zeros((1, nv))
        a_ub[0, n:2 * n] = 1.0 / eta
        b_ub = np.array([bess.daily_cycle_limit * usable])

        bounds = []
        for t in range(n):
            c_max = bess.mw if bess.allow_grid_charging else min(bess.mw, float(pv[t]))
            bounds.append((0.0, max(c_max, 0.0)))
        bounds += [(0.0, bess.mw)] * n
        bounds += [(min_soc, max_soc)] * n

        res = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
        if not res.success:
            c = np.zeros(n)
            d = np.zeros(n)
            s = np.full(n, soc0)
        else:
            c = res.x[:n]
            d = res.x[n:2 * n]
            s = res.x[2 * n:]

        charge_all.append(c)
        discharge_all.append(d)
        soc_all.append(s)
        soc0 = float(s[-1])

    charge = np.concatenate(charge_all)
    discharge = np.concatenate(discharge_all)
    soc = np.concatenate(soc_all)

    out = df.copy()
    out["pv_to_bess_mwh"] = np.minimum(charge, out["pv_mwh"].to_numpy())
    out["grid_to_bess_mwh"] = np.maximum(charge - out["pv_mwh"].to_numpy(), 0.0)
    out["bess_discharge_mwh"] = discharge
    out["soc_mwh"] = soc
    out["pv_to_grid_mwh"] = (out["pv_mwh"] - out["pv_to_bess_mwh"]).clip(lower=0)
    out["merchant_revenue"] = (
        (out["pv_to_grid_mwh"] + out["bess_discharge_mwh"]) * out["pml"]
        - out["grid_to_bess_mwh"] * out["pml"]
    )
    return out


def run_dispatch(pml_pv: pd.DataFrame, bess: BESSConfig, engine: str = "price_rank", discharge_hours_per_day: int | None = None) -> pd.DataFrame:
    """Dispatch dispatcher: engine is 'price_rank' (simple, auditable) or 'lp' (optimal)."""
    if engine == "lp":
        return dispatch_lp(pml_pv, bess)
    return dispatch_price_rank(pml_pv, bess, discharge_hours_per_day=discharge_hours_per_day)
