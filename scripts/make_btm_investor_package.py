#!/usr/bin/env python3
"""Generate BTM investor evidence package from saved dispatch outputs."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from agents.btm_investor_agent import FinanceCase, build_small_industry_investor_case, detect_small_industry_red_flags, investor_recommendation, write_investor_package


def main() -> None:
    p = argparse.ArgumentParser(description="Build BTM investor Excel package")
    p.add_argument("--case-id", default="CASE_001")
    p.add_argument("--baseline-bill", required=True)
    p.add_argument("--optimized-bill", required=True)
    p.add_argument("--dispatch", required=True)
    p.add_argument("--capex-mxn", type=float, default=3_000_000)
    p.add_argument("--readiness-status", default="DEMO")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    baseline = pd.read_csv(args.baseline_bill)
    optimized = pd.read_csv(args.optimized_bill)
    dispatched = pd.read_csv(args.dispatch)
    dispatched["timestamp"] = pd.to_datetime(dispatched["timestamp"])

    fin = FinanceCase(capex_mxn=args.capex_mxn)
    case = build_small_industry_investor_case(baseline, optimized, fin)
    case["readiness_status"] = args.readiness_status
    if args.readiness_status != "INVESTMENT_READY":
        case["report_watermark"] = f"{args.readiness_status} — NOT FOR INVESTOR USE"
    flags = detect_small_industry_red_flags(dispatched, baseline, optimized, case, fin)
    rec = investor_recommendation(case, fin, red_flags=flags, readiness_status=args.readiness_status)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_investor_package(out, case, rec, baseline, optimized, flags, assumptions={"case_id": args.case_id})
    print(f"Wrote {out} | recommendation: {rec}")


if __name__ == "__main__":
    main()
