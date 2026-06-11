"""BTM LP dispatch engine — Fix 2.

Bankable dispatch: daily linear program at 15-minute resolution inside a
monthly import-cap sweep, then CFE bill recalculation (post-optimization).

Falls back to rule-based dispatch if scipy is unavailable or the solver fails.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from agents.btm_dispatch_agent import BTMBessConfig, INTERVAL_HOURS, dispatch_btm, dispatch_summary
from agents.btm_tariff_agent import TariffRates, reconstruct_annual_bill


@dataclass
class BTMLPResult:
    dispatch_15min: pd.DataFrame
    optimized_bill: pd.DataFrame | None = None
    savings_by_component: dict[str, float] = field(default_factory=dict)
    feasibility_status: str = "ok"
    warnings: list[str] = field(default_factory=list)
    engine: str = "lp"
    solver: str = "highs"
    solver_status: str = "success"
    fallback_used: bool = False


def _period_rates(periods: np.ndarray, rates: TariffRates) -> np.ndarray:
    default = rates.energy.get("intermedia", 0.0)
    return np.array([rates.energy.get(str(p), default) for p in periods], dtype=float)


def _simulate_month_lp(load: np.ndarray, pv: np.ndarray, period: np.ndarray,
                       bess: BTMBessConfig, cap_kw: float, period_rates: np.ndarray) -> dict[str, np.ndarray] | None:
    try:
        from scipy.optimize import linprog
    except ImportError:
        return None

    n = len(load)
    eta = float(np.sqrt(max(min(bess.rte, 1.0), 0.01)))
    soc0 = bess.soc_floor_kwh
    min_soc, max_soc = bess.soc_floor_kwh, bess.energy_kwh

    charge_pv = np.zeros(n)
    charge_grid = np.zeros(n)
    discharge = np.zeros(n)
    soc_series = np.zeros(n)
    pv_to_load = np.zeros(n)
    curtailed = np.zeros(n)
    grid_import = np.zeros(n)

    soc = soc0
    for day_start in range(0, n, 96):
        day_end = min(day_start + 96, n)
        dn = day_end - day_start
        if dn <= 0:
            continue
        ld = load[day_start:day_end]
        pv_d = pv[day_start:day_end]
        pr = period_rates[day_start:day_end]
        per = period[day_start:day_end]

        # Variables: c_pv, c_g, d, pv_l, g_imp  each dn long -> 5*dn
        nv = 5 * dn
        cost = np.zeros(nv)
        for t in range(dn):
            cost[t] = 0.0                    # c_pv free
            cost[dn + t] = pr[t] * INTERVAL_HOURS  # grid charge forgoes selling... actually costs energy
            cost[2 * dn + t] = -pr[t] * INTERVAL_HOURS  # discharge saves import at period rate
            cost[3 * dn + t] = 0.0
            cost[4 * dn + t] = pr[t] * INTERVAL_HOURS  # grid import cost

        a_eq_rows, b_eq = [], []
        for t in range(dn):
            row = np.zeros(nv)
            row[t] = 1.0
            row[3 * dn + t] = 1.0
            row[2 * dn + t] = 1.0
            row[4 * dn + t] = -1.0
            a_eq_rows.append(row)
            b_eq.append(ld[t])
            row2 = np.zeros(nv)
            row2[t] = 1.0
            row2[3 * dn + t] = 1.0
            row2[4 * dn + t] = -1.0
            a_eq_rows.append(row2)
            b_eq.append(pv_d[t])

        # SOC dynamics (single day, start from soc)
        a_eq_soc = np.zeros((dn, nv))
        b_soc = np.zeros(dn)
        for t in range(dn):
            a_eq_soc[t, 2 * dn + t] = INTERVAL_HOURS / eta
            a_eq_soc[t, t] = -INTERVAL_HOURS * eta
            a_eq_soc[t, dn + t] = -INTERVAL_HOURS * eta
            if t == 0:
                b_soc[t] = soc
            else:
                a_eq_soc[t, 2 * dn + t - 1] = -1.0
        a_eq = np.vstack(a_eq_rows + [a_eq_soc])
        b_eq = np.array(b_eq + list(b_soc))

        bounds = []
        for t in range(dn):
            bounds.append((0.0, min(bess.power_kw, max(pv_d[t], 0.0))))  # c_pv
        for t in range(dn):
            cgrid_max = bess.power_kw if (bess.allow_grid_charging and per[t] == "base") else 0.0
            bounds.append((0.0, cgrid_max))
        for _ in range(dn):
            bounds.append((0.0, bess.power_kw))
        for t in range(dn):
            bounds.append((0.0, min(ld[t], pv_d[t])))
        for t in range(dn):
            bounds.append((0.0, cap_kw))

        a_ub = np.zeros((1, nv))
        a_ub[0, 4 * dn:5 * dn] = 1.0
        b_ub = np.array([cap_kw * dn])  # soft aggregate; peak enforced in outer sweep

        res = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
        if not res.success:
            return None

        x = res.x
        cp = x[:dn]
        cg = x[dn:2 * dn]
        di = x[2 * dn:3 * dn]
        pvl = x[3 * dn:4 * dn]
        gi = x[4 * dn:5 * dn]
        soc_traj = np.zeros(dn)
        s = soc
        for t in range(dn):
            s = s + (cp[t] + cg[t]) * INTERVAL_HOURS * eta - di[t] * INTERVAL_HOURS / eta
            s = min(max(s, min_soc), max_soc)
            soc_traj[t] = s
        soc = float(soc_traj[-1])

        sl = slice(day_start, day_end)
        charge_pv[sl] = cp
        charge_grid[sl] = cg
        discharge[sl] = di
        pv_to_load[sl] = pvl
        grid_import[sl] = gi
        soc_series[sl] = soc_traj
        curtailed[sl] = np.maximum(pv_d - pvl - cp, 0.0)

    if float(grid_import.max()) > cap_kw + 1e-3:
        return None

    return {
        "pv_to_load_kw": pv_to_load, "pv_curtailed_kw": curtailed,
        "charge_pv_kw": charge_pv, "charge_grid_kw": charge_grid,
        "discharge_kw": discharge, "soc_kwh": soc_series,
        "grid_import_kw": grid_import, "grid_export_kw": np.zeros(n),
    }


def _best_cap_lp(load, pv, period, bess, rates: TariffRates) -> tuple[float, dict | None]:
    pr = _period_rates(period, rates)
    net_peak = float(np.max(load - np.minimum(load, pv))) if len(load) else 0.0
    lo, hi = max(net_peak - bess.power_kw, 0.0), net_peak
    best_cap, best_sim = hi, None
    for _ in range(16):
        mid = (lo + hi) / 2
        sim = _simulate_month_lp(load, pv, period, bess, mid, pr)
        if sim is not None and float(np.max(sim["grid_import_kw"])) <= mid + 1e-3:
            best_cap, best_sim = mid, sim
            hi = mid
        else:
            lo = mid
    return best_cap, best_sim


def dispatch_btm_lp(df: pd.DataFrame, bess: BTMBessConfig, rates: TariffRates, mode: str = "pv_bess") -> BTMLPResult:
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    out = out.sort_values("timestamp").reset_index(drop=True)
    if mode == "bess_only" or "pv_kw" not in out.columns:
        out["pv_kw"] = 0.0
    out["pv_kw"] = pd.to_numeric(out["pv_kw"], errors="coerce").fillna(0).clip(lower=0)
    out["load_kw"] = pd.to_numeric(out["load_kw"], errors="coerce").fillna(0).clip(lower=0)

    pieces, warnings = [], []
    fallback = False
    for _, month_df in out.groupby(out["timestamp"].dt.to_period("M"), sort=True):
        load = month_df["load_kw"].to_numpy(dtype=float)
        pv = month_df["pv_kw"].to_numpy(dtype=float)
        period = month_df["period"].to_numpy()
        cap, sim = _best_cap_lp(load, pv, period, bess, rates)
        if sim is None:
            from agents.btm_dispatch_agent import _best_cap, _simulate_month
            cap = _best_cap(load, pv, period, bess)
            sim = _simulate_month(load, pv, period, bess, cap)
            fallback = True
            warnings.append(f"LP infeasible for {month_df['timestamp'].iloc[0]:%Y-%m}; used rule-based fallback.")
        piece = month_df.copy()
        for k, v in sim.items():
            piece[k] = v
        piece["import_cap_kw"] = cap
        pieces.append(piece)

    dispatched = pd.concat(pieces, ignore_index=True)
    return BTMLPResult(
        dispatch_15min=dispatched,
        feasibility_status="fallback" if fallback else "ok",
        warnings=warnings,
        engine="lp",
        solver_status="fallback_rule_based" if fallback else "success",
        fallback_used=fallback,
    )


def optimize_btm_lp_dispatch(
    load_15min: pd.DataFrame,
    pv_15min: pd.DataFrame | None,
    tariff_calendar: pd.DataFrame,
    tariff_rates: TariffRates,
    bess_config: BTMBessConfig,
    mode: str = "pv_bess",
    baseline_bills: pd.DataFrame | None = None,
) -> BTMLPResult:
    merged = load_15min.copy()
    merged["timestamp"] = pd.to_datetime(merged["timestamp"])
    merged = merged.merge(tariff_calendar, on="timestamp", how="inner")
    if pv_15min is not None and mode == "pv_bess":
        pv_15min = pv_15min.copy()
        pv_15min["timestamp"] = pd.to_datetime(pv_15min["timestamp"])
        merged = merged.merge(pv_15min, on="timestamp", how="left")
    result = dispatch_btm_lp(merged, bess_config, tariff_rates, mode=mode)
    opt_bills = reconstruct_annual_bill(
        result.dispatch_15min[["timestamp", "grid_import_kw"]],
        tariff_calendar, tariff_rates, load_col="grid_import_kw",
    )
    result.optimized_bill = opt_bills
    if baseline_bills is not None:
        result.savings_by_component = {
            "energy": float(baseline_bills["energy_charge"].sum() - opt_bills["energy_charge"].sum()),
            "capacity": float(baseline_bills["capacity_charge"].sum() - opt_bills["capacity_charge"].sum()),
            "distribution": float(baseline_bills["distribution_charge"].sum() - opt_bills["distribution_charge"].sum()),
            "total": float(baseline_bills["total"].sum() - opt_bills["total"].sum()),
        }
    return result
