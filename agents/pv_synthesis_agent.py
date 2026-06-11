"""Synthesize PV production profiles from system specs (MW AC, MWp, yield, degradation)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

INTERVAL_HOURS_15MIN = 0.25


@dataclass
class PVSystemSpec:
    mw_ac: float
    mwp: float
    yield_kwh_kwp_yr: float
    degradation_pct_yr: float = 0.4
    operation_year: int = 1  # 1 = first year of operation (no prior degradation)


def annual_production_mwh(spec: PVSystemSpec) -> float:
    """Nameplate annual energy at the given operation year (MWh/year).

    kWh/year = MWp * 1000 * yield * (1 - degradation)^(year-1)
    """
    if spec.mwp <= 0 or spec.yield_kwh_kwp_yr <= 0:
        return 0.0
    years_elapsed = max(int(spec.operation_year), 1) - 1
    deg_factor = (1.0 - spec.degradation_pct_yr / 100.0) ** years_elapsed
    kwh_yr = spec.mwp * 1000.0 * spec.yield_kwh_kwp_yr * deg_factor
    return kwh_yr / 1000.0


def annual_production_summary(spec: PVSystemSpec) -> dict[str, float]:
    mwh = annual_production_mwh(spec)
    kwp = spec.mwp * 1000.0
    effective_yield = (mwh * 1000.0 / kwp) if kwp > 0 else 0.0
    dc_ac_ratio = (spec.mwp / spec.mw_ac) if spec.mw_ac > 0 else 0.0
    return {
        "annual_mwh": mwh,
        "annual_gwh": mwh / 1000.0,
        "effective_yield_kwh_kwp": effective_yield,
        "dc_ac_ratio": dc_ac_ratio,
        "capacity_factor_pct": (mwh / (spec.mw_ac * 8760.0) * 100.0) if spec.mw_ac > 0 else 0.0,
    }


def _as_datetime_index(timestamps: pd.Series | pd.DatetimeIndex | pd.Index) -> pd.DatetimeIndex:
    """Always return a DatetimeIndex (Series has no .hour; Index does)."""
    return pd.DatetimeIndex(pd.to_datetime(timestamps, errors="coerce"))


def _solar_weights(timestamps: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    ts = _as_datetime_index(timestamps)
    # DatetimeIndex only — never call .hour on a Series.
    hours = np.asarray(ts.hour, dtype=float) + np.asarray(ts.minute, dtype=float) / 60.0
    doy = np.asarray(ts.dayofyear, dtype=float)
    day_len = 0.85 + 0.15 * np.sin((doy - 80.0) / 365.0 * 2.0 * np.pi)
    bell = np.exp(-((hours - 13.0) ** 2) / (2.0 * (2.6 * day_len) ** 2))
    bell[(hours < 6.0) | (hours > 20.0)] = 0.0
    return bell


def _scale_and_clip_power(
    timestamps: pd.Series | pd.DatetimeIndex,
    weights: np.ndarray,
    target_mwh: float,
    mw_ac: float,
    interval_hours: float,
) -> np.ndarray:
    w = np.asarray(weights, dtype=float)
    w = np.clip(w, 0.0, None)
    total_w = w.sum()
    if total_w <= 0 or target_mwh <= 0:
        return np.zeros(len(w))
    energy_per_weight = target_mwh / total_w
    energy = w * energy_per_weight
    if mw_ac > 0:
        cap_mwh = mw_ac * interval_hours
        energy = np.minimum(energy, cap_mwh)
        clipped = energy.sum()
        if clipped > 0 and clipped < target_mwh * 0.999:
            # Re-normalize after clipping so annual target is preserved (iterative once).
            energy *= target_mwh / clipped
            energy = np.minimum(energy, cap_mwh)
    return energy


def synthesize_pv_8760(
    timestamps: pd.Series | pd.DatetimeIndex,
    spec: PVSystemSpec,
) -> pd.DataFrame:
    """Build hourly (or sub-hourly) PV energy aligned to `timestamps`.

    Returns columns: datetime, pv_mwh
    """
    ts = _as_datetime_index(timestamps)
    if len(ts) == 0:
        return pd.DataFrame(columns=["datetime", "pv_mwh"])
    inferred = pd.infer_freq(ts[: min(len(ts), 10)])
    interval_hours = 1.0
    if inferred and inferred.endswith("min"):
        interval_hours = pd.Timedelta(inferred).total_seconds() / 3600.0
    elif inferred in ("H", "h"):
        interval_hours = 1.0
    elif len(ts) > 1:
        interval_hours = (ts[1] - ts[0]).total_seconds() / 3600.0

    target = annual_production_mwh(spec)
    weights = _solar_weights(ts)
    pv_mwh = _scale_and_clip_power(ts, weights, target, spec.mw_ac, interval_hours)
    return pd.DataFrame({"datetime": ts, "pv_mwh": np.round(pv_mwh, 6)})


def synthesize_pv_15min(
    timestamps: pd.Series | pd.DatetimeIndex,
    spec: PVSystemSpec,
) -> pd.DataFrame:
    """Build 15-minute PV power (kW) aligned to load timestamps."""
    ts = _as_datetime_index(timestamps)
    if len(ts) == 0:
        return pd.DataFrame(columns=["timestamp", "pv_kw"])
    target_mwh = annual_production_mwh(spec)
    weights = _solar_weights(ts)
    energy_mwh = _scale_and_clip_power(ts, weights, target_mwh, spec.mw_ac, INTERVAL_HOURS_15MIN)
    pv_kw = energy_mwh / INTERVAL_HOURS_15MIN * 1000.0
    if spec.mw_ac > 0:
        pv_kw = np.minimum(pv_kw, spec.mw_ac * 1000.0)
    return pd.DataFrame({"timestamp": ts, "pv_kw": np.round(pv_kw, 4)})


def spec_from_dict(d: dict) -> PVSystemSpec:
    return PVSystemSpec(
        mw_ac=float(d["mw_ac"]),
        mwp=float(d["mwp"]),
        yield_kwh_kwp_yr=float(d["yield_kwh_kwp_yr"]),
        degradation_pct_yr=float(d.get("degradation_pct_yr", 0.4)),
        operation_year=int(d.get("operation_year", 1)),
    )
