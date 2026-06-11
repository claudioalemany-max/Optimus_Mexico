"""Generate synthetic 15-minute industrial load + PV data for the BTM module.

Creates one calendar year (2026) of:
- data/sample/btm_load_15min_sample.csv  (timestamp, load_kw)
- data/sample/btm_pv_15min_sample.csv    (timestamp, pv_kw)

Profile: a two-shift industrial plant (06:00-22:00 weekdays) with ~750 kW
day peak, ~200 kW night base, reduced Saturdays, minimal Sundays, plus a
500 kWp PV plant. Synthetic, for testing only.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("data/sample")
YEAR = 2026
PV_KWP = 500.0


def main() -> None:
    rng = np.random.default_rng(42)
    idx = pd.date_range(f"{YEAR}-01-01", f"{YEAR + 1}-01-01", freq="15min", inclusive="left")

    hours = (idx.hour + idx.minute / 60).to_numpy()
    dow = idx.dayofweek.to_numpy()
    doy = idx.dayofyear.to_numpy()

    # --- industrial load ---
    base = 200.0
    shift = np.where((hours >= 6) & (hours < 22), 550.0, 0.0)
    ramp = np.clip((hours - 6) / 1.5, 0, 1) * np.clip((22 - hours) / 1.5, 0, 1)
    weekday_factor = np.select([dow <= 4, dow == 5], [1.0, 0.45], default=0.15)
    seasonal = 1.0 + 0.08 * np.sin((doy - 200) / 365 * 2 * np.pi)  # summer cooling bump
    noise = rng.normal(0, 18, len(idx))
    spikes = (rng.random(len(idx)) < 0.0008) * rng.uniform(80, 160, len(idx))
    load = (base + shift * ramp * weekday_factor) * seasonal + noise + spikes
    load = np.clip(load, 120, None)

    # --- PV ---
    solar_elev = np.clip(np.sin((hours - 6.5) / 13 * np.pi), 0, None)
    season_pv = 0.85 + 0.15 * np.sin((doy - 172) / 365 * 2 * np.pi + np.pi / 2)
    cloud = np.clip(rng.normal(0.92, 0.12, len(idx)), 0.3, 1.0)
    pv = PV_KWP * solar_elev**1.3 * season_pv * cloud
    pv = np.where(solar_elev > 0, pv, 0.0)

    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"timestamp": idx, "load_kw": np.round(load, 2)}).to_csv(
        OUT / "btm_load_15min_sample.csv", index=False)
    pd.DataFrame({"timestamp": idx, "pv_kw": np.round(pv, 2)}).to_csv(
        OUT / "btm_pv_15min_sample.csv", index=False)
    print(f"Wrote {len(idx):,} intervals to {OUT}/btm_load_15min_sample.csv and btm_pv_15min_sample.csv")
    print(f"Load: peak {load.max():,.0f} kW, avg {load.mean():,.0f} kW | PV: {PV_KWP:,.0f} kWp")


if __name__ == "__main__":
    main()
