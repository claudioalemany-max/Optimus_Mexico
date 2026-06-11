"""Tests for the Behind-the-Meter module: tariff calendar, bill engine,
dispatch (SOC / no-export) and investor layer."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from agents.btm_tariff_agent import (
    INTERVAL_HOURS, TariffRates, build_tariff_calendar, compute_monthly_bill,
    gdmto_migration_check, load_starter_rates, mexico_official_holidays,
    reconstruct_annual_bill,
)
from agents.btm_dispatch_agent import BTMBessConfig, dispatch_btm
from agents.btm_investor_agent import (
    FinanceCase, RiskAssumptions, build_small_industry_investor_case,
    calculate_net_monthly_benefit, investor_recommendation, irr,
    monthly_payment, npv,
)


@pytest.fixture(scope="module")
def calendar_jan():
    return build_tariff_calendar("2026-01-01", "2026-02-01")


@pytest.fixture(scope="module")
def rates():
    return TariffRates.from_table(load_starter_rates())


# ---------------------------------------------------------------- calendar

def test_calendar_covers_every_interval(calendar_jan):
    assert len(calendar_jan) == 31 * 96
    assert calendar_jan["period"].notna().all()


def test_calendar_winter_weekday_periods(calendar_jan):
    # Wed 2026-01-07: base 00-06, intermedia 06-18, punta 18-22, intermedia 22-24
    day = calendar_jan[calendar_jan["timestamp"].dt.date == date(2026, 1, 7)]
    assert day["day_type"].eq("weekday").all()
    assert day[day["timestamp"].dt.hour < 6]["period"].eq("base").all()
    assert day[(day["timestamp"].dt.hour >= 18) & (day["timestamp"].dt.hour < 22)]["period"].eq("punta").all()
    assert day[day["timestamp"].dt.hour >= 22]["period"].eq("intermedia").all()


def test_calendar_holiday_treated_as_sunday(calendar_jan):
    # Jan 1 is an official holiday -> sunday_holiday rules, no punta.
    day = calendar_jan[calendar_jan["timestamp"].dt.date == date(2026, 1, 1)]
    assert day["day_type"].eq("sunday_holiday").all()
    assert not day["period"].eq("punta").any()


def test_summer_season_classification():
    cal = build_tariff_calendar("2026-07-01", "2026-07-02")
    assert cal["season"].eq("summer").all()


def test_official_holidays_2026():
    hols = mexico_official_holidays(2026)
    assert date(2026, 1, 1) in hols
    assert date(2026, 9, 16) in hols
    assert date(2026, 2, 2) in hols  # first Monday of February


# ---------------------------------------------------------------- bill engine

def test_monthly_bill_formulas(calendar_jan, rates):
    # Flat 100 kW load for January.
    df = calendar_jan.copy()
    df["load_kw"] = 100.0
    bill = compute_monthly_bill(df, rates)

    q = 100.0 * len(df) * INTERVAL_HOURS
    assert sum(bill.kwh_by_period.values()) == pytest.approx(q)
    assert bill.dmax_mensual_kw == 100
    assert bill.dmax_punta_kw == 100

    formula_kw = q / (24 * 31 * rates.factor_carga)
    expected_capacity_kw = min(100, formula_kw)
    assert bill.capacity_billing_kw == pytest.approx(expected_capacity_kw)
    assert bill.capacity_charge == pytest.approx(expected_capacity_kw * rates.capacity_kw)
    assert bill.fixed_charge == pytest.approx(rates.fixed_month)
    assert bill.total > 0


def test_peaky_load_pays_more_demand_than_flat(calendar_jan, rates):
    flat = calendar_jan.copy()
    flat["load_kw"] = 100.0
    flat_bill = compute_monthly_bill(flat, rates)

    peaky = calendar_jan.copy()
    peaky["load_kw"] = 50.0
    # One 500 kW spike during punta (Jan 7, 19:00).
    mask = (peaky["timestamp"] == pd.Timestamp("2026-01-07 19:00")).to_numpy()
    peaky.loc[mask, "load_kw"] = 500.0
    peaky_bill = compute_monthly_bill(peaky, rates)

    assert peaky_bill.dmax_punta_kw == 500
    # min() formula protects the peaky customer: capacity kW is capped by consumption formula.
    assert peaky_bill.capacity_billing_kw < 500


def test_gdmto_migration_flag():
    idx = pd.date_range("2026-01-01", periods=96, freq="15min")
    df = pd.DataFrame({"timestamp": idx, "load_kw": 60.0})
    df.loc[10, "load_kw"] = 120.0
    out = gdmto_migration_check(df)
    assert bool(out["gdmth_migration_flag"].iloc[0])


# ---------------------------------------------------------------- dispatch

@pytest.fixture(scope="module")
def small_dispatch(calendar_jan):
    rng = np.random.default_rng(1)
    df = calendar_jan.copy()
    hours = df["timestamp"].dt.hour
    df["load_kw"] = 200.0 + np.where((hours >= 8) & (hours < 20), 400.0, 0.0) + rng.normal(0, 10, len(df))
    df["pv_kw"] = np.clip(np.sin((hours - 6.5) / 13 * np.pi), 0, None) * 300.0
    bess = BTMBessConfig(power_kw=150, energy_kwh=300, rte=0.90, soc_min_pct=0.10)
    return dispatch_btm(df, bess, mode="pv_bess"), bess


def test_dispatch_no_export(small_dispatch):
    disp, _ = small_dispatch
    assert float(disp["grid_export_kw"].max()) == 0.0


def test_dispatch_soc_bounds(small_dispatch):
    disp, bess = small_dispatch
    assert disp["soc_kwh"].min() >= bess.soc_floor_kwh - 1e-6
    assert disp["soc_kwh"].max() <= bess.energy_kwh + 1e-6


def test_dispatch_reduces_peak(small_dispatch):
    disp, _ = small_dispatch
    assert disp["grid_import_kw"].max() < disp["load_kw"].max()


def test_dispatch_power_limits(small_dispatch):
    disp, bess = small_dispatch
    assert disp["discharge_kw"].max() <= bess.power_kw + 1e-6
    assert (disp["charge_pv_kw"] + disp["charge_grid_kw"]).max() <= bess.power_kw + 1e-6


def test_energy_balance(small_dispatch):
    disp, _ = small_dispatch
    lhs = disp["load_kw"]
    rhs = disp["pv_to_load_kw"] + disp["discharge_kw"] + disp["grid_import_kw"] - disp["charge_grid_kw"]
    assert np.allclose(lhs, rhs, atol=1e-6)


# ---------------------------------------------------------------- investor

def _bills(totals: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "month": range(1, len(totals) + 1),
        "total": totals,
        "energy_charge": [t * 0.5 for t in totals],
        "capacity_charge": [t * 0.3 for t in totals],
        "distribution_charge": [t * 0.15 for t in totals],
        "fixed_charge": [t * 0.05 for t in totals],
    })


def test_monthly_payment_cash_is_zero():
    assert monthly_payment(FinanceCase(capex_mxn=1e6, financing_type="cash_purchase")) == 0.0


def test_monthly_payment_loan_amortization():
    fin = FinanceCase(capex_mxn=1_000_000, financing_type="bank_loan", tenor_years=5, interest_rate=0.12)
    pay = monthly_payment(fin)
    assert pay == pytest.approx(22244.45, rel=1e-3)  # standard annuity


def test_net_monthly_benefit():
    assert calculate_net_monthly_benefit(100_000, 70_000, 10_000, 2_000, 1_000) == pytest.approx(17_000)


def test_investor_case_and_recommendation_go():
    base = _bills([1_000_000] * 12)
    opt = _bills([700_000] * 12)
    fin = FinanceCase(capex_mxn=4_000_000, financing_type="bank_loan", tenor_years=7, interest_rate=0.14)
    case = build_small_industry_investor_case(base, opt, fin)
    assert case["modeled_annual_savings_mxn"] == pytest.approx(3_600_000)
    assert case["bankable_annual_savings_mxn"] < case["modeled_annual_savings_mxn"]
    rec = investor_recommendation(case, fin)
    assert rec.startswith("GO")


def test_investor_recommendation_no_go_downside():
    base = _bills([100_000] * 12)
    opt = _bills([98_000] * 12)  # tiny savings
    fin = FinanceCase(capex_mxn=10_000_000, financing_type="bank_loan", tenor_years=7, interest_rate=0.14)
    case = build_small_industry_investor_case(base, opt, fin)
    rec = investor_recommendation(case, fin)
    assert rec.startswith("NO-GO") or rec.startswith("REVISE")


def test_npv_and_irr_basics():
    flows = [-1000.0, 500.0, 500.0, 500.0]
    assert npv(flows, 0.0) == pytest.approx(500.0)
    r = irr(flows)
    assert r is not None
    assert npv(flows, r) == pytest.approx(0.0, abs=1e-3)
    assert irr([-1000.0, -10.0]) is None  # no sign change


def test_investor_case_annual_and_lifetime_figures():
    base = _bills([1_000_000] * 12)
    opt = _bills([700_000] * 12)
    fin = FinanceCase(capex_mxn=4_000_000, financing_type="cash_purchase",
                      opex_mxn_per_year=100_000, insurance_mxn_per_year=20_000,
                      bess_life_years=20.0, discount_rate=0.10)
    case = build_small_industry_investor_case(base, opt, fin)

    # Annual figures are 12x the monthly ones.
    assert case["net_annual_benefit_base_mxn"] == pytest.approx(case["net_monthly_benefit_base_mxn"] * 12)
    assert case["annual_opex_insurance_mxn"] == pytest.approx(120_000)
    assert case["annual_financing_payment_mxn"] == 0.0  # cash purchase

    # Lifetime: 20 years of bankable savings net of O&M, minus CAPEX at t0.
    bankable = case["bankable_annual_savings_mxn"]
    expected_lifetime = -4_000_000 + 20 * (bankable - 120_000)
    assert case["lifetime_net_benefit_base_mxn"] == pytest.approx(expected_lifetime)
    assert case["bess_life_years"] == 20.0
    assert case["npv_base_mxn"] > 0
    assert case["project_irr_pct"] is not None and case["project_irr_pct"] > 0


def test_recommendation_no_go_when_payback_exceeds_bess_life():
    base = _bills([1_000_000] * 12)
    opt = _bills([990_000] * 12)  # 120k/yr savings
    fin = FinanceCase(capex_mxn=1_000_000, financing_type="cash_purchase",
                      warranty_years=30.0, bess_life_years=5.0)
    risk = RiskAssumptions(required_max_payback_years=50.0,
                           tariff_spread_haircut_downside=1.0, load_reduction_downside=1.0)
    case = build_small_industry_investor_case(base, opt, fin, risk)
    assert case["simple_payback_years"] > 5.0
    rec = investor_recommendation(case, fin, risk)
    assert rec == "NO-GO: payback exceeds useful BESS life"


def test_investor_recommendation_data_quality_gate():
    base = _bills([1_000_000] * 12)
    opt = _bills([700_000] * 12)
    fin = FinanceCase(capex_mxn=4_000_000)
    case = build_small_industry_investor_case(base, opt, fin)
    assert investor_recommendation(case, fin, data_quality_fail=True).startswith("NO-GO")
    assert investor_recommendation(case, fin, baseline_error_pct=5.0).startswith("REVISE")


# ---------------------------------------------------------------- end to end

def test_annual_reconstruction_smoke(rates):
    cal = build_tariff_calendar("2026-01-01", "2026-03-01")
    df = pd.DataFrame({"timestamp": cal["timestamp"], "load_kw": 150.0})
    bills = reconstruct_annual_bill(df, cal, rates)
    assert len(bills) == 2
    assert (bills["total"] > 0).all()
