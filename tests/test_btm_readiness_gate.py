"""Fix 3 — investment readiness gate tests."""
import pandas as pd

from agents.btm_investment_readiness_agent import (
    STATUS_DEMO,
    STATUS_INVESTMENT_READY,
    STATUS_REVISE,
    STATUS_SCREENING,
    assess_meter_quality,
    bill_reconstruction_summary,
    evaluate_investment_readiness,
)
from agents.btm_investor_agent import FinanceCase, build_small_industry_investor_case, investor_recommendation


def _bills(totals):
    return pd.DataFrame({"month": range(1, len(totals) + 1), "total": totals,
                         "energy_charge": totals, "capacity_charge": [0] * len(totals),
                         "distribution_charge": [0] * len(totals), "fixed_charge": [0] * len(totals)})


def test_synthetic_data_returns_demo():
    result = evaluate_investment_readiness(
        {"cfe_bill_months": 12},
        {"months_15min": 12, "missing_interval_pct": 0},
        {"has_actual_bills": True, "annual_error_pct": 1.0, "months_failed": 0},
        {"uses_synthetic_data": True, "tariff_confirmed": True},
    )
    assert result["status"] == STATUS_DEMO
    assert not result["investor_recommendation_allowed"]


def test_insufficient_bills_returns_screening():
    result = evaluate_investment_readiness(
        {"cfe_bill_months": 6},
        {"months_15min": 12, "missing_interval_pct": 0},
        {"has_actual_bills": False},
        {"uses_synthetic_data": False, "tariff_confirmed": True},
    )
    assert result["status"] == STATUS_SCREENING


def test_bill_error_above_threshold_returns_revise():
    result = evaluate_investment_readiness(
        {"cfe_bill_months": 12},
        {"months_15min": 12, "missing_interval_pct": 0},
        {"has_actual_bills": True, "annual_error_pct": 5.0, "months_failed": 0},
        {"uses_synthetic_data": False, "tariff_confirmed": True},
    )
    assert result["status"] == STATUS_REVISE


def test_investment_ready_when_gates_pass():
    result = evaluate_investment_readiness(
        {"cfe_bill_months": 12, "has_pv_profile": True},
        {"months_15min": 12, "missing_interval_pct": 0.5},
        {"has_actual_bills": True, "annual_error_pct": 2.0, "months_failed": 0},
        {"uses_synthetic_data": False, "tariff_confirmed": True, "mode": "pv_bess"},
    )
    assert result["status"] == STATUS_INVESTMENT_READY
    assert result["investor_recommendation_allowed"]


def test_investor_recommendation_blocked_unless_investment_ready():
    base = _bills([1_000_000] * 12)
    opt = _bills([700_000] * 12)
    fin = FinanceCase(capex_mxn=4_000_000)
    case = build_small_industry_investor_case(base, opt, fin)
    blocked = investor_recommendation(case, fin, readiness_status=STATUS_DEMO)
    assert blocked.startswith("BLOCKED")
    allowed = investor_recommendation(case, fin, readiness_status=STATUS_INVESTMENT_READY)
    assert allowed.startswith("GO") or allowed.startswith("REVISE") or allowed.startswith("NO-GO")


def test_bill_reconstruction_summary_without_actuals():
    base = _bills([100.0] * 12)
    summary = bill_reconstruction_summary(base, None)
    assert summary["has_actual_bills"] is False
    assert summary["annual_error_pct"] is None


def test_assess_meter_quality_counts_months():
    ts = pd.date_range("2026-01-01", periods=96 * 31 * 2, freq="15min")
    df = pd.DataFrame({"timestamp": ts, "load_kw": 100.0})
    q = assess_meter_quality(df)
    assert q["months_15min"] >= 2
