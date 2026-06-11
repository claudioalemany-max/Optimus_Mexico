"""Front-of-meter project economics: CAPEX, OPEX and IRR on merchant/PPA/capacity revenue."""
from __future__ import annotations

import pandas as pd

from agents.btm_investor_agent import FinanceCase, _lifetime_cash_flows, irr, monthly_payment, npv


def compute_project_capex_mxn(
    pv_mwp: float,
    bess_mwh: float,
    pv_usd_kwp: float,
    bess_usd_kwh: float,
    fx: float,
) -> dict[str, float]:
    pv_mxn = max(pv_mwp, 0.0) * 1000.0 * max(pv_usd_kwp, 0.0) * fx
    bess_mxn = max(bess_mwh, 0.0) * 1000.0 * max(bess_usd_kwh, 0.0) * fx
    return {
        "pv_capex_mxn": pv_mxn,
        "bess_capex_mxn": bess_mxn,
        "total_capex_mxn": pv_mxn + bess_mxn,
    }


def build_fom_investor_case(
    annual_revenue_mxn: float,
    finance: FinanceCase,
    revenue_haircut: float = 0.95,
) -> dict:
    """IRR/NPV on front-of-meter revenue after OPEX (and financing if not cash)."""
    years = max(int(round(finance.bess_life_years)), 1)
    opex_ins = finance.opex_mxn_per_year + finance.insurance_mxn_per_year
    bankable_revenue = annual_revenue_mxn * revenue_haircut

    flows_levered = _lifetime_cash_flows(bankable_revenue, finance)
    unlevered_flows = [-finance.capex_mxn] + [
        annual_revenue_mxn - opex_ins for _ in range(years)
    ]
    levered_irr = irr(flows_levered)
    unlevered_irr = irr(unlevered_flows)

    fin_annual = monthly_payment(finance) * 12
    net_annual = bankable_revenue - opex_ins - fin_annual
    payback = (
        finance.capex_mxn / (bankable_revenue - opex_ins)
        if bankable_revenue > opex_ins else float("inf")
    )

    return {
        "capex_mxn": finance.capex_mxn,
        "annual_opex_mxn": finance.opex_mxn_per_year,
        "annual_insurance_mxn": finance.insurance_mxn_per_year,
        "annual_opex_insurance_mxn": opex_ins,
        "annual_revenue_mxn": annual_revenue_mxn,
        "bankable_annual_revenue_mxn": bankable_revenue,
        "net_annual_cashflow_mxn": net_annual,
        "annual_financing_mxn": fin_annual,
        "simple_payback_years": payback,
        "project_life_years": finance.bess_life_years,
        "npv_mxn": npv(flows_levered, finance.discount_rate),
        "project_irr_unlevered_pct": unlevered_irr * 100 if unlevered_irr is not None else None,
        "project_irr_levered_pct": levered_irr * 100 if levered_irr is not None else None,
        "revenue_haircut": revenue_haircut,
    }


def fom_economics_frame(case: dict) -> pd.DataFrame:
    irr_u = (
        f"{case['project_irr_unlevered_pct']:.1f}%"
        if case.get("project_irr_unlevered_pct") is not None else "n/a"
    )
    irr_l = (
        f"{case['project_irr_levered_pct']:.1f}%"
        if case.get("project_irr_levered_pct") is not None else "n/a"
    )
    rows = [
        ("Total CAPEX (MXN)", f"{case['capex_mxn']:,.0f}"),
        ("Annual OPEX (MXN)", f"{case['annual_opex_mxn']:,.0f}"),
        ("Annual insurance (MXN)", f"{case['annual_insurance_mxn']:,.0f}"),
        ("Annual revenue (MXN)", f"{case['annual_revenue_mxn']:,.0f}"),
        ("Bankable annual revenue (MXN)", f"{case['bankable_annual_revenue_mxn']:,.0f}"),
        ("Annual financing (MXN)", f"{case['annual_financing_mxn']:,.0f}"),
        ("Net annual cash flow — base (MXN)", f"{case['net_annual_cashflow_mxn']:,.0f}"),
        ("Simple payback (years)", f"{case['simple_payback_years']:.1f}"),
        (f"NPV over {case['project_life_years']:.0f}y (MXN)", f"{case['npv_mxn']:,.0f}"),
        ("Project IRR — unlevered", irr_u),
        ("Project IRR — levered", irr_l),
    ]
    return pd.DataFrame(rows, columns=["metric", "value"]).astype({"metric": "string", "value": "string"})
