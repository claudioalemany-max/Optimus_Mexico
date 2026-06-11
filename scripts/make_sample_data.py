"""Generate synthetic-but-realistic sample data for end-to-end testing.

Creates:
- data/sample/pml_8760_sample.csv  (hourly PML for one year, MXN/MWh)
- data/sample/pv_8760_sample.csv   (hourly PV energy for one year, MWh)

The PML shape is seeded from the real one-day CENACE download in
outputs/pml/pml_clean.csv when available, otherwise from a default
duck-curve daily profile. Data is clearly synthetic and only intended
for pipeline testing, never for investment decisions.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DAILY_SHAPE = np.array([
    0.85, 0.80, 0.76, 0.73, 0.72, 0.74, 0.80, 0.88,
    0.92, 0.90, 0.85, 0.80, 0.74, 0.70, 0.68, 0.72,
    0.85, 1.05, 1.30, 1.45, 1.40, 1.25, 1.05, 0.92,
])


def _seed_shape() -> np.ndarray:
    seed_csv = ROOT / "outputs" / "pml" / "pml_clean.csv"
    if seed_csv.exists():
        df = pd.read_csv(seed_csv)
        if "pml" in df.columns and df["pml"].notna().sum() >= 24:
            day = df.sort_values("hour")["pml"].to_numpy()[:24]
            return day / day.mean()
    return DEFAULT_DAILY_SHAPE / DEFAULT_DAILY_SHAPE.mean()


def make_pml_8760(year: int, mean_price: float, node: str, rng: np.random.Generator) -> pd.DataFrame:
    idx = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h")
    idx = idx[~((idx.month == 2) & (idx.day == 29))]  # keep 8760 rows on leap years
    shape = _seed_shape()
    hours = idx.hour.to_numpy()
    months = idx.month.to_numpy()
    # Seasonal factor: summer peak typical for SIN
    seasonal = 1.0 + 0.18 * np.sin((months - 4) / 12 * 2 * np.pi)
    noise = rng.lognormal(mean=0.0, sigma=0.18, size=len(idx))
    # Occasional scarcity spikes
    spikes = (rng.random(len(idx)) < 0.004) * rng.uniform(2.0, 6.0, size=len(idx))
    pml = mean_price * shape[hours] * seasonal * noise * (1 + spikes)
    df = pd.DataFrame({
        "datetime": idx,
        "date": idx.date,
        "hour": hours + 1,
        "node": node,
        "pml": pml.round(2),
    })
    df["energy_component"] = (df["pml"] * 0.90).round(2)
    df["losses_component"] = (df["pml"] * 0.07).round(2)
    df["congestion_component"] = (df["pml"] * 0.03).round(2)
    return df


def make_pv_8760(year: int, pv_mw_ac: float, rng: np.random.Generator) -> pd.DataFrame:
    idx = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h")
    idx = idx[~((idx.month == 2) & (idx.day == 29))]
    hours = idx.hour.to_numpy()
    doy = idx.dayofyear.to_numpy()
    # Solar elevation proxy: bell around 13h local, seasonal day-length swing
    day_len = 0.85 + 0.15 * np.sin((doy - 80) / 365 * 2 * np.pi)
    bell = np.exp(-((hours - 13.0) ** 2) / (2 * (2.6 * day_len) ** 2))
    bell[(hours < 6) | (hours > 20)] = 0.0
    cloud = rng.beta(8, 2, size=len(idx))  # mostly clear with cloudy dips
    pv = pv_mw_ac * bell * cloud
    return pd.DataFrame({"datetime": idx, "pv_mwh": pv.round(3)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--mean-price", type=float, default=1100.0, help="Mean PML in MXN/MWh")
    parser.add_argument("--pv-mw", type=float, default=100.0, help="PV plant AC capacity in MW")
    parser.add_argument("--node", default="SAMPLE-NODE")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default=str(ROOT / "data" / "sample"))
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pml = make_pml_8760(args.year, args.mean_price, args.node, rng)
    pv = make_pv_8760(args.year, args.pv_mw, rng)

    pml_path = out_dir / "pml_8760_sample.csv"
    pv_path = out_dir / "pv_8760_sample.csv"
    pml.to_csv(pml_path, index=False, encoding="utf-8-sig")
    pv.to_csv(pv_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {pml_path} ({len(pml)} rows)")
    print(f"Wrote {pv_path} ({len(pv)} rows)")


if __name__ == "__main__":
    main()
