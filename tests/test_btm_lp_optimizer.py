"""Fix 2 — BTM LP optimizer tests."""
from pathlib import Path

import pandas as pd
import pytest

from agents.btm_dispatch_agent import BTMBessConfig, dispatch_btm
from agents.btm_lp_optimizer_agent import dispatch_btm_lp, optimize_btm_lp_dispatch
from agents.btm_tariff_agent import TariffRates, build_tariff_calendar, load_starter_rates, reconstruct_annual_bill


@pytest.fixture(scope="module")
def sample_merged():
    load_path = Path("data/sample/btm_load_15min_sample.csv")
    pv_path = Path("data/sample/btm_pv_15min_sample.csv")
    if not load_path.exists():
        pytest.skip("sample load data missing")
    load_df = pd.read_csv(load_path)
    load_df["timestamp"] = pd.to_datetime(load_df["timestamp"])
    start = load_df["timestamp"].min().normalize()
    end = load_df["timestamp"].max().normalize() + pd.Timedelta(days=1)
    cal = build_tariff_calendar(start, end)
    merged = load_df.merge(cal, on="timestamp", how="inner")
    if pv_path.exists():
        pv = pd.read_csv(pv_path)
        pv["timestamp"] = pd.to_datetime(pv["timestamp"])
        merged = merged.merge(pv, on="timestamp", how="left")
    return merged, cal


def test_lp_runs_full_year_no_soc_violation(sample_merged):
    merged, _ = sample_merged
    bess = BTMBessConfig(power_kw=300, energy_kwh=600, rte=0.9, soc_min_pct=0.1, allow_grid_charging=False)
    rates = TariffRates.from_table(load_starter_rates())
    result = dispatch_btm_lp(merged, bess, rates, mode="pv_bess")
    d = result.dispatch_15min
    assert len(d) == len(merged)
    assert (d["soc_kwh"] >= bess.soc_floor_kwh - 1e-3).all()
    assert (d["soc_kwh"] <= bess.energy_kwh + 1e-3).all()


def test_lp_no_export_zero_grid_export(sample_merged):
    merged, _ = sample_merged
    bess = BTMBessConfig(power_kw=300, energy_kwh=600)
    rates = TariffRates.from_table(load_starter_rates())
    result = dispatch_btm_lp(merged, bess, rates, mode="pv_bess")
    assert result.dispatch_15min["grid_export_kw"].max() == pytest.approx(0.0, abs=1e-6)


def test_lp_savings_via_same_bill_engine(sample_merged):
    merged, cal = sample_merged
    load_df = merged[["timestamp", "load_kw"]].copy()
    pv_df = merged[["timestamp", "pv_kw"]].copy() if "pv_kw" in merged.columns else None
    bess = BTMBessConfig(power_kw=300, energy_kwh=600, allow_grid_charging=False)
    rates = TariffRates.from_table(load_starter_rates())
    baseline = reconstruct_annual_bill(load_df, cal, rates)
    lp = optimize_btm_lp_dispatch(load_df, pv_df, cal, rates, bess, mode="pv_bess", baseline_bills=baseline)
    assert lp.optimized_bill is not None
    assert lp.savings_by_component["total"] >= 0


def test_lp_reports_solver_status(sample_merged):
    merged, _ = sample_merged
    bess = BTMBessConfig(power_kw=200, energy_kwh=400)
    rates = TariffRates.from_table(load_starter_rates())
    result = dispatch_btm_lp(merged, bess, rates)
    assert result.solver_status in ("success", "fallback_rule_based")


def test_lp_produces_dispatch_when_compare_to_rule(sample_merged):
    merged, cal = sample_merged
    bess = BTMBessConfig(power_kw=300, energy_kwh=600)
    rates = TariffRates.from_table(load_starter_rates())
    rule = dispatch_btm(merged, bess, mode="pv_bess")
    lp = dispatch_btm_lp(merged, bess, rates, mode="pv_bess").dispatch_15min
    assert len(rule) == len(lp)
