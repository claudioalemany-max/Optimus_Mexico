"""Tests for front-of-meter project economics."""
import pytest

from agents.btm_investor_agent import FinanceCase
from agents.fom_investor_agent import build_fom_investor_case, compute_project_capex_mxn


def test_compute_project_capex():
    parts = compute_project_capex_mxn(100, 200, 650, 350, 18.5)
    assert parts["pv_capex_mxn"] == pytest.approx(100 * 1000 * 650 * 18.5)
    assert parts["bess_capex_mxn"] == pytest.approx(200 * 1000 * 350 * 18.5)
    assert parts["total_capex_mxn"] == parts["pv_capex_mxn"] + parts["bess_capex_mxn"]


def test_fom_irr_positive_revenue():
    fin = FinanceCase(capex_mxn=50_000_000, financing_type="cash_purchase", bess_life_years=20,
                      opex_mxn_per_year=500_000, insurance_mxn_per_year=100_000, discount_rate=0.10)
    case = build_fom_investor_case(15_000_000, fin, revenue_haircut=0.95)
    assert case["project_irr_unlevered_pct"] is not None
    assert case["project_irr_unlevered_pct"] > 0
    assert case["npv_mxn"] > 0
