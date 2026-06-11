"""Behind-the-Meter BESS / PV+BESS dispatch (15-minute, no-export).

Implements the rule-based benchmark engine from the BTM spec:
- PV serves load first; surplus charges the BESS; residual PV is curtailed
  (no-export mode is the default and only mode in v1).
- BESS discharges (a) whenever net import would exceed the monthly peak-shaving
  cap and (b) in punta periods (TOU energy shifting).
- BESS charges from PV surplus and, when allowed, from the grid in base
  periods (never during punta), without creating a new peak above the cap.
- The monthly import cap is found by binary search: the lowest cap the battery
  can sustain for the whole month given its power and energy limits.

Modes (spec section 4): "bess_only", "pv_bess" (no export), grid charging flag.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

INTERVAL_HOURS = 0.25


@dataclass
class BTMBessConfig:
    power_kw: float
    energy_kwh: float
    rte: float = 0.90
    soc_min_pct: float = 0.10
    allow_grid_charging: bool = True
    backup_reserve_pct: float = 0.0   # extra SOC floor locked for resilience

    @property
    def soc_floor_kwh(self) -> float:
        return self.energy_kwh * max(self.soc_min_pct, self.backup_reserve_pct)


def _simulate_month(load: np.ndarray, pv: np.ndarray, period: np.ndarray,
                    bess: BTMBessConfig, cap_kw: float) -> dict[str, np.ndarray]:
    """Simulate one month at a fixed import cap. Returns 15-min series."""
    n = len(load)
    eta = float(np.sqrt(max(min(bess.rte, 1.0), 0.01)))
    soc = bess.soc_floor_kwh
    soc_max = bess.energy_kwh

    pv_to_load = np.minimum(load, pv)
    net_load = load - pv_to_load          # residual load after direct PV
    pv_surplus = pv - pv_to_load

    charge_pv = np.zeros(n)
    charge_grid = np.zeros(n)
    discharge = np.zeros(n)
    soc_series = np.zeros(n)
    curtailed = np.zeros(n)

    for t in range(n):
        # 1) PV surplus charges the battery (free energy, always take it).
        room_kwh = soc_max - soc
        c_pv = min(pv_surplus[t], bess.power_kw, room_kwh / (INTERVAL_HOURS * eta) if eta else 0.0)
        c_pv = max(c_pv, 0.0)
        soc += c_pv * INTERVAL_HOURS * eta
        curtailed[t] = pv_surplus[t] - c_pv

        # 2) Discharge to keep import at/below cap, or shift punta energy.
        need_kw = max(net_load[t] - cap_kw, 0.0)
        want_kw = net_load[t] if period[t] == "punta" else need_kw
        avail_kw = max(soc - bess.soc_floor_kwh, 0.0) * eta / INTERVAL_HOURS
        d = min(want_kw, bess.power_kw - c_pv if c_pv < bess.power_kw else 0.0, avail_kw, net_load[t])
        d = max(d, 0.0)
        soc -= d * INTERVAL_HOURS / eta

        # 3) Grid charging in base periods, never pushing import above cap.
        c_grid = 0.0
        if bess.allow_grid_charging and period[t] == "base" and d == 0.0:
            headroom_kw = max(cap_kw - (net_load[t] - d), 0.0)
            room_kwh = soc_max - soc
            c_grid = min(headroom_kw, bess.power_kw - c_pv, room_kwh / (INTERVAL_HOURS * eta) if eta else 0.0)
            c_grid = max(c_grid, 0.0)
            soc += c_grid * INTERVAL_HOURS * eta

        charge_pv[t] = c_pv
        charge_grid[t] = c_grid
        discharge[t] = d
        soc_series[t] = soc

    grid_import = net_load - discharge + charge_grid
    return {
        "pv_to_load_kw": pv_to_load, "pv_curtailed_kw": curtailed,
        "charge_pv_kw": charge_pv, "charge_grid_kw": charge_grid,
        "discharge_kw": discharge, "soc_kwh": soc_series,
        "grid_import_kw": np.maximum(grid_import, 0.0),
        "grid_export_kw": np.zeros(n),
    }


def _best_cap(load: np.ndarray, pv: np.ndarray, period: np.ndarray, bess: BTMBessConfig) -> float:
    """Binary-search the lowest sustainable monthly import cap."""
    net_peak = float(np.max(load - np.minimum(load, pv))) if len(load) else 0.0
    lo, hi = max(net_peak - bess.power_kw, 0.0), net_peak
    for _ in range(18):
        mid = (lo + hi) / 2
        sim = _simulate_month(load, pv, period, bess, mid)
        if float(np.max(sim["grid_import_kw"])) <= mid + 1e-6:
            hi = mid
        else:
            lo = mid
    return hi


def dispatch_btm(df: pd.DataFrame, bess: BTMBessConfig, mode: str = "pv_bess") -> pd.DataFrame:
    """Dispatch the battery month by month.

    `df` needs columns: timestamp, load_kw, period — and pv_kw for pv_bess mode.
    Returns the frame with dispatch columns added; `grid_import_kw` is the
    optimized metered load to re-bill.
    """
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    out = out.sort_values("timestamp").reset_index(drop=True)
    if mode == "bess_only" or "pv_kw" not in out.columns:
        out["pv_kw"] = 0.0
    out["pv_kw"] = pd.to_numeric(out["pv_kw"], errors="coerce").fillna(0).clip(lower=0)
    out["load_kw"] = pd.to_numeric(out["load_kw"], errors="coerce").fillna(0).clip(lower=0)

    pieces = []
    for _, month_df in out.groupby(out["timestamp"].dt.to_period("M"), sort=True):
        load = month_df["load_kw"].to_numpy(dtype=float)
        pv = month_df["pv_kw"].to_numpy(dtype=float)
        period = month_df["period"].to_numpy()
        cap = _best_cap(load, pv, period, bess)
        sim = _simulate_month(load, pv, period, bess, cap)
        piece = month_df.copy()
        for k, v in sim.items():
            piece[k] = v
        piece["import_cap_kw"] = cap
        pieces.append(piece)
    return pd.concat(pieces, ignore_index=True)


def dispatch_summary(dispatched: pd.DataFrame) -> dict[str, float]:
    d = dispatched
    return {
        "load_mwh": float((d["load_kw"] * INTERVAL_HOURS).sum() / 1000),
        "pv_self_consumed_mwh": float(((d["pv_to_load_kw"] + d["charge_pv_kw"]) * INTERVAL_HOURS).sum() / 1000),
        "pv_curtailed_mwh": float((d["pv_curtailed_kw"] * INTERVAL_HOURS).sum() / 1000),
        "bess_discharge_mwh": float((d["discharge_kw"] * INTERVAL_HOURS).sum() / 1000),
        "grid_import_mwh": float((d["grid_import_kw"] * INTERVAL_HOURS).sum() / 1000),
        "max_export_kw": float(d["grid_export_kw"].max()),
        "peak_before_kw": float(d["load_kw"].max()),
        "peak_after_kw": float(d["grid_import_kw"].max()),
    }
