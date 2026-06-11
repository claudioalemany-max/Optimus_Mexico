"""End-to-end Optimus Mexico pipeline.

PML 8760 + PV 8760  ->  dispatch  ->  duration sweep  ->  PPA split
->  capacity credit (100 critical hours)  ->  Excel/Word/PDF/PPT reports.

Example (with sample data):
    python scripts/make_sample_data.py
    python scripts/run_pipeline.py ^
        --pml data/sample/pml_8760_sample.csv ^
        --pv data/sample/pv_8760_sample.csv ^
        --bess-mw 50 --bess-mwh 200 --engine lp ^
        --ppa-price 950 --ppa-fraction 0.7 ^
        --capacity-price 1450000 ^
        --out outputs/reports
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.capacity_agent import CapacityConfig, capacity_summary_frame, compute_capacity_credit  # noqa: E402
from agents.dispatch_agent import BESSConfig, run_dispatch  # noqa: E402
from agents.optimizer_agent import best_configuration, sweep_bess_durations  # noqa: E402
from agents.ppa_agent import PPAConfig, apply_ppa, ppa_summary  # noqa: E402
from agents.printout_agent import ReportData, write_all_reports  # noqa: E402
from agents.pv_loader_agent import load_pv_8760, merge_pml_pv  # noqa: E402


def load_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    return pd.read_excel(path) if path.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pml", required=True, help="Clean PML CSV/XLSX with datetime + pml columns")
    parser.add_argument("--pv", required=True, help="PV 8760 CSV/XLSX")
    parser.add_argument("--project-name", default="Optimus Mexico Project")
    parser.add_argument("--bess-mw", type=float, default=50.0)
    parser.add_argument("--bess-mwh", type=float, default=200.0)
    parser.add_argument("--rte", type=float, default=0.925)
    parser.add_argument("--reserve", type=float, default=0.10)
    parser.add_argument("--engine", choices=["price_rank", "lp"], default="price_rank")
    parser.add_argument("--durations", type=int, nargs="+", default=[2, 4, 6, 8])
    parser.add_argument("--ppa-price", type=float, default=None, help="PPA price MXN/MWh; omit to skip PPA module")
    parser.add_argument("--ppa-mode", choices=["pro_rata", "baseload", "solar_only"], default="pro_rata")
    parser.add_argument("--ppa-fraction", type=float, default=0.7)
    parser.add_argument("--ppa-mw", type=float, default=0.0)
    parser.add_argument("--capacity-price", type=float, default=None, help="Capacity price MXN/MW-year; omit to skip capacity module")
    parser.add_argument("--critical-hours", type=int, default=100)
    parser.add_argument("--out", default="outputs/reports")
    args = parser.parse_args()

    print("1/6 Loading inputs...")
    pml = load_table(args.pml)
    pv = load_pv_8760(args.pv)
    merged = merge_pml_pv(pml, pv)
    print(f"    {len(merged)} hourly rows, avg PML {merged['pml'].mean():,.2f} MXN/MWh")

    print(f"2/6 Running dispatch (engine={args.engine})...")
    bess = BESSConfig(mw=args.bess_mw, mwh=args.bess_mwh, rte=args.rte, reserve_fraction=args.reserve)
    dispatch = run_dispatch(merged, bess, engine=args.engine)
    print(f"    Merchant revenue: {dispatch['merchant_revenue'].sum():,.0f} MXN")

    print(f"3/6 Sweeping BESS durations {args.durations}...")
    scenarios = sweep_bess_durations(merged, args.bess_mw, args.durations, engine=args.engine)
    best = best_configuration(scenarios)
    print(f"    Best duration: {best['duration_h']:.0f}h -> {best['merchant_revenue']:,.0f} MXN")

    ppa_table = None
    if args.ppa_price is not None:
        print("4/6 Applying PPA structure...")
        ppa_cfg = PPAConfig(ppa_price_mxn_mwh=args.ppa_price, mode=args.ppa_mode, ppa_fraction=args.ppa_fraction, ppa_mw=args.ppa_mw)
        priced = apply_ppa(dispatch, ppa_cfg)
        ppa_table = ppa_summary(priced)
        dispatch = priced
        print(f"    Total energy revenue: {priced['total_energy_revenue'].sum():,.0f} MXN")
    else:
        print("4/6 PPA module skipped (no --ppa-price)")

    capacity_table = None
    if args.capacity_price is not None:
        print("5/6 Computing capacity credit...")
        cap_cfg = CapacityConfig(capacity_price_mxn_mw_year=args.capacity_price, n_critical_hours=args.critical_hours)
        cap = compute_capacity_credit(dispatch, cap_cfg)
        capacity_table = capacity_summary_frame(cap)
        print(f"    Accredited: {cap['accredited_capacity_mw']:.2f} MW -> {cap['capacity_revenue_mxn']:,.0f} MXN/year")
    else:
        print("5/6 Capacity module skipped (no --capacity-price)")

    print("6/6 Writing reports...")
    assumptions = {
        "BESS MW": args.bess_mw,
        "BESS MWh": args.bess_mwh,
        "RTE": args.rte,
        "Reserve fraction": args.reserve,
        "Dispatch engine": args.engine,
        "PPA price (MXN/MWh)": args.ppa_price if args.ppa_price is not None else "n/a",
        "PPA mode": args.ppa_mode if args.ppa_price is not None else "n/a",
        "Capacity price (MXN/MW-yr)": args.capacity_price if args.capacity_price is not None else "n/a",
        "Critical hours": args.critical_hours,
    }
    report = ReportData(
        project_name=args.project_name,
        dispatch=dispatch,
        scenario_summary=scenarios,
        ppa_summary=ppa_table,
        capacity_summary=capacity_table,
        assumptions=assumptions,
    )
    written = write_all_reports(report, args.out)
    for fmt, path in written.items():
        print(f"    {fmt}: {path}")
    print("Done.")


if __name__ == "__main__":
    main()
