#!/usr/bin/env python3
"""Run BTM investment readiness gate only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from agents.btm_investment_readiness_agent import assess_meter_quality, bill_reconstruction_summary, evaluate_investment_readiness
from agents.btm_tariff_agent import TariffRates, build_tariff_calendar, load_starter_rates, reconstruct_annual_bill


def main() -> None:
    p = argparse.ArgumentParser(description="BTM investment readiness assessment")
    p.add_argument("--load", required=True, help="15-min load CSV")
    p.add_argument("--bills", help="CFE monthly bills CSV (month, total)")
    p.add_argument("--case-id", default="CASE_001")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--out", required=True, help="Output JSON path")
    args = p.parse_args()

    load_df = pd.read_csv(args.load)
    load_df["timestamp"] = pd.to_datetime(load_df["timestamp"])
    meter = assess_meter_quality(load_df)
    start = load_df["timestamp"].min().normalize()
    end = load_df["timestamp"].max().normalize() + pd.Timedelta(days=1)
    cal = build_tariff_calendar(start, end)
    rates = TariffRates.from_table(load_starter_rates())
    baseline = reconstruct_annual_bill(load_df, cal, rates)
    actual = pd.read_csv(args.bills) if args.bills else None
    recon = bill_reconstruction_summary(baseline, actual)
    result = evaluate_investment_readiness(
        {"cfe_bill_months": len(actual) if actual is not None else 0},
        meter, recon,
        {"uses_synthetic_data": args.synthetic, "tariff_confirmed": True},
    )
    result["case_id"] = args.case_id
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
