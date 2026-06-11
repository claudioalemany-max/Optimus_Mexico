"""Investor output gating — GO/REVISE/NO-GO requires INVESTMENT READY."""
import pandas as pd

from agents.btm_investment_readiness_agent import STATUS_DEMO, STATUS_INVESTMENT_READY
from agents.btm_investor_agent import FinanceCase, build_small_industry_investor_case, investor_recommendation


def _bills(totals):
    return pd.DataFrame({
        "month": range(1, len(totals) + 1), "total": totals,
        "energy_charge": totals, "capacity_charge": [0] * len(totals),
        "distribution_charge": [0] * len(totals), "fixed_charge": [0] * len(totals),
    })


def test_go_cannot_run_on_demo():
    case = build_small_industry_investor_case(_bills([1e6] * 12), _bills([7e5] * 12), FinanceCase(capex_mxn=4e6))
    rec = investor_recommendation(case, FinanceCase(capex_mxn=4e6), readiness_status=STATUS_DEMO)
    assert "BLOCKED" in rec
    assert "GO" not in rec.split(":")[0]


def test_go_allowed_when_investment_ready():
    fin = FinanceCase(capex_mxn=4_000_000, financing_type="bank_loan", tenor_years=7, interest_rate=0.14)
    case = build_small_industry_investor_case(_bills([1_000_000] * 12), _bills([700_000] * 12), fin)
    rec = investor_recommendation(case, fin, readiness_status=STATUS_INVESTMENT_READY)
    assert rec.startswith("GO")
