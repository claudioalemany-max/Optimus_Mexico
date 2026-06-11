#!/usr/bin/env python3
"""Run BTM LP optimization from CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from agents.btm_dispatch_agent import BTMBessConfig
from agents.btm_lp_optimizer_agent import optimize_btm_lp_dispatch
from agents.btm_tariff_agent import TariffRates, build_tariff_calendar, load_starter_rates, reconstruct_annual_bill


def main() -> None:
    p = argparse.ArgumentParser(description="BTM LP dispatch optimization")
    p.add_argument("--load", required=True)
    p.add_argument("--pv", help="Optional PV CSV")
    p.add_argument("--tariff", default="GDMTH")
    p.add_argument("--division", default="Valle de Mexico Sur")
    p.add_argument("--bess-kw", type=float, default=300)
    p.add_argument("--bess-kwh", type=float, default=600)
    p.add_argument("--no-export", action="store_true", default=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    load_df = pd.read_csv(args.load)
    load_df["timestamp"] = pd.to_datetime(load_df["timestamp"])
    pv_df = None
    if args.pv:
        pv_df = pd.read_csv(args.pv)
        pv_df["timestamp"] = pd.to_datetime(pv_df["timestamp"])
    start = load_df["timestamp"].min().normalize()
    end = load_df["timestamp"].max().normalize() + pd.Timedelta(days=1)
    cal = build_tariff_calendar(start, end, tariff=args.tariff, division=args.division)
    rates = TariffRates.from_table(load_starter_rates(), tariff=args.tariff, division=args.division)
    baseline = reconstruct_annual_bill(load_df, cal, rates)
    bess = BTMBessConfig(power_kw=args.bess_kw, energy_kwh=args.bess_kwh, allow_grid_charging=False)
    mode = "pv_bess" if pv_df is not None else "bess_only"
    result = optimize_btm_lp_dispatch(load_df, pv_df, cal, rates, bess, mode=mode, baseline_bills=baseline)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    result.dispatch_15min.to_csv(out_dir / "dispatch_15min.csv", index=False)
    result.optimized_bill.to_csv(out_dir / "optimized_bill.csv", index=False)
    meta = {
        "solver_status": result.solver_status,
        "fallback_used": result.fallback_used,
        "savings_by_component": result.savings_by_component,
        "warnings": result.warnings,
    }
    (out_dir / "lp_result.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
