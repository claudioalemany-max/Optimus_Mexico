"""Small Industrial Investor Layer (BTM spec section 21).

Sits on top of the tariff + dispatch engines and answers one question:
will the BESS reduce monthly cash outflow after financing, and is the
risk acceptable under conservative assumptions?

Provides:
- financing payment calculation (cash / lease / loan)
- bankable savings with confidence haircuts
- base / downside / upside investor cases
- red-flag detection for small industrial customers
- deterministic GO / REVISE / NO-GO recommendation
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class FinanceCase:
    capex_mxn: float
    financing_type: str = "cash_purchase"   # cash_purchase | equipment_lease | bank_loan
    tenor_years: int = 7
    interest_rate: float = 0.14
    opex_mxn_per_year: float = 0.0
    insurance_mxn_per_year: float = 0.0
    warranty_years: float = 10.0
    residual_value_mxn: float = 0.0
    bess_life_years: float = 20.0           # analysis horizon = useful BESS life
    discount_rate: float = 0.10


@dataclass
class RiskAssumptions:
    savings_confidence_haircut: float = 0.90
    bess_availability_haircut: float = 0.95
    collection_or_payment_haircut: float = 0.95
    tariff_spread_haircut_downside: float = 0.80
    load_reduction_downside: float = 0.80
    upside_uplift: float = 1.10
    required_min_net_monthly_benefit_mxn: float = 0.0
    required_max_payback_years: float = 7.0


def monthly_payment(finance: FinanceCase) -> float:
    """Monthly financing outflow. Zero for a cash purchase."""
    if finance.financing_type == "cash_purchase" or finance.capex_mxn <= 0:
        return 0.0
    n = max(int(finance.tenor_years * 12), 1)
    r = finance.interest_rate / 12
    principal = finance.capex_mxn - finance.residual_value_mxn
    if r <= 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def _lifetime_cash_flows(annual_savings: float, finance: FinanceCase) -> list[float]:
    """Yearly net cash flows over the BESS life.

    Year 0 carries the CAPEX for a cash purchase; financed structures pay
    the annuity during the tenor instead. O&M and insurance run every year.
    """
    years = max(int(round(finance.bess_life_years)), 1)
    pay_annual = monthly_payment(finance) * 12
    flows = [-finance.capex_mxn if finance.financing_type == "cash_purchase" else 0.0]
    for y in range(1, years + 1):
        cf = annual_savings - finance.opex_mxn_per_year - finance.insurance_mxn_per_year
        if finance.financing_type != "cash_purchase" and y <= finance.tenor_years:
            cf -= pay_annual
        flows.append(cf)
    return flows


def npv(flows: list[float], rate: float) -> float:
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(flows))


def irr(flows: list[float], lo: float = -0.99, hi: float = 5.0) -> float | None:
    """IRR by bisection on the unlevered flow vector. None if no sign change."""
    f_lo, f_hi = npv(flows, lo), npv(flows, hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        f_mid = npv(flows, mid)
        if abs(f_mid) < 1e-6:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def calculate_net_monthly_benefit(
    baseline_monthly_bill_mxn: float,
    optimized_monthly_bill_mxn: float,
    lease_or_debt_payment_mxn: float,
    opex_mxn: float,
    insurance_mxn: float = 0.0,
) -> float:
    """Net benefit = CFE savings - financing payment - O&M - insurance."""
    savings = baseline_monthly_bill_mxn - optimized_monthly_bill_mxn
    return savings - lease_or_debt_payment_mxn - opex_mxn - insurance_mxn


def build_small_industry_investor_case(
    baseline_bills: pd.DataFrame,
    optimized_bills: pd.DataFrame,
    finance: FinanceCase,
    risk: RiskAssumptions | None = None,
) -> dict:
    """Base/downside/upside net monthly benefit, payback and recommendation inputs.

    `baseline_bills` / `optimized_bills`: monthly bill frames from the tariff
    engine (must contain a `total` column).
    """
    risk = risk or RiskAssumptions()
    baseline_annual = float(baseline_bills["total"].sum())
    optimized_annual = float(optimized_bills["total"].sum())
    modeled_savings = baseline_annual - optimized_annual

    bankable = (
        modeled_savings
        * risk.savings_confidence_haircut
        * risk.bess_availability_haircut
        * risk.collection_or_payment_haircut
    )
    downside = bankable * risk.tariff_spread_haircut_downside * risk.load_reduction_downside
    upside = modeled_savings * risk.upside_uplift

    pay = monthly_payment(finance)
    monthly_costs = (finance.opex_mxn_per_year + finance.insurance_mxn_per_year) / 12

    def net_monthly(annual_savings: float) -> float:
        return annual_savings / 12 - pay - monthly_costs

    monthly_savings = baseline_bills["total"].to_numpy() - optimized_bills["total"].to_numpy()
    simple_payback = (finance.capex_mxn / modeled_savings) if modeled_savings > 0 else float("inf")

    # Lifetime economics over the useful BESS life (bankable savings, flat).
    flows_base = _lifetime_cash_flows(bankable, finance)
    flows_downside = _lifetime_cash_flows(downside, finance)
    # Unlevered IRR: CAPEX upfront regardless of financing, modeled savings net of O&M.
    unlevered = [-finance.capex_mxn] + [
        modeled_savings - finance.opex_mxn_per_year - finance.insurance_mxn_per_year
    ] * max(int(round(finance.bess_life_years)), 1)
    project_irr = irr(unlevered)
    levered_irr = irr(flows_base)

    return {
        "baseline_annual_bill_mxn": baseline_annual,
        "optimized_annual_bill_mxn": optimized_annual,
        "modeled_annual_savings_mxn": modeled_savings,
        "bankable_annual_savings_mxn": bankable,
        "capex_mxn": finance.capex_mxn,
        "annual_opex_mxn": finance.opex_mxn_per_year,
        "annual_insurance_mxn": finance.insurance_mxn_per_year,
        "monthly_financing_payment_mxn": pay,
        "monthly_opex_insurance_mxn": monthly_costs,
        "annual_financing_payment_mxn": pay * 12,
        "annual_opex_insurance_mxn": finance.opex_mxn_per_year + finance.insurance_mxn_per_year,
        "net_monthly_benefit_base_mxn": net_monthly(bankable),
        "net_monthly_benefit_downside_mxn": net_monthly(downside),
        "net_monthly_benefit_upside_mxn": net_monthly(upside),
        "net_annual_benefit_base_mxn": net_monthly(bankable) * 12,
        "net_annual_benefit_downside_mxn": net_monthly(downside) * 12,
        "net_annual_benefit_upside_mxn": net_monthly(upside) * 12,
        "worst_month_savings_mxn": float(monthly_savings.min()) if len(monthly_savings) else 0.0,
        "simple_payback_years": simple_payback,
        "bess_life_years": finance.bess_life_years,
        "npv_base_mxn": npv(flows_base, finance.discount_rate),
        "npv_downside_mxn": npv(flows_downside, finance.discount_rate),
        "lifetime_net_benefit_base_mxn": sum(flows_base),
        "project_irr_pct": project_irr * 100 if project_irr is not None else None,
        "savings_irr_unlevered_pct": project_irr * 100 if project_irr is not None else None,
        "savings_irr_levered_pct": levered_irr * 100 if levered_irr is not None else None,
        "haircuts": {
            "savings_confidence": risk.savings_confidence_haircut,
            "bess_availability": risk.bess_availability_haircut,
            "collection_or_payment": risk.collection_or_payment_haircut,
            "downside_tariff_spread": risk.tariff_spread_haircut_downside,
            "downside_load_reduction": risk.load_reduction_downside,
            "upside_uplift": risk.upside_uplift,
        },
    }


def detect_small_industry_red_flags(
    dispatched: pd.DataFrame,
    baseline_bills: pd.DataFrame,
    optimized_bills: pd.DataFrame,
    case: dict,
    finance: FinanceCase,
    risk: RiskAssumptions | None = None,
) -> list[dict]:
    """Warning flags to show before investment approval (spec 21.5)."""
    risk = risk or RiskAssumptions()
    flags: list[dict] = []

    def flag(name: str, detail: str, action: str):
        flags.append({"red_flag": name, "detail": detail, "recommended_action": action})

    # Rare/random demand peaks: top 5 intervals vs 95th percentile.
    load = pd.to_numeric(dispatched["load_kw"], errors="coerce").fillna(0)
    if len(load) > 100:
        top5 = load.nlargest(5).mean()
        p95 = load.quantile(0.95)
        if p95 > 0 and top5 / p95 > 1.3:
            flag("Rare/random demand peaks",
                 f"Top-5 peak intervals average {top5:,.0f} kW vs 95th percentile {p95:,.0f} kW.",
                 "Use smaller BESS or apply forecast/EMS confidence haircut.")

    # Flat load profile.
    avg_load = float(load.mean())
    peak = float(load.max())
    if avg_load > 0 and peak / avg_load < 1.3:
        flag("Flat load profile",
             f"Peak/average ratio is {peak / avg_load:.2f}; demand-charge reduction potential is limited.",
             "Do not oversize BESS; evaluate PV self-consumption instead.")

    # Low demand-charge impact.
    demand_savings = float(
        (baseline_bills["capacity_charge"].sum() + baseline_bills["distribution_charge"].sum())
        - (optimized_bills["capacity_charge"].sum() + optimized_bills["distribution_charge"].sum())
    )
    total_savings = case["modeled_annual_savings_mxn"]
    if total_savings > 0 and demand_savings / total_savings < 0.30:
        flag("Low demand charge impact",
             f"Capacity+distribution savings are {demand_savings / total_savings:.0%} of total savings.",
             "Avoid the project unless energy shifting / PV savings are strong.")

    # Downside case negative.
    if case["net_monthly_benefit_downside_mxn"] < 0:
        flag("Negative downside case",
             f"Downside net monthly benefit is MXN {case['net_monthly_benefit_downside_mxn']:,.0f}.",
             "Resize BESS or restructure financing before approval.")

    # Payback beyond warranty.
    if case["simple_payback_years"] > finance.warranty_years:
        flag("Payback beyond warranty",
             f"Simple payback {case['simple_payback_years']:.1f}y exceeds warranty {finance.warranty_years:.0f}y.",
             "Reject or reduce CAPEX/size.")

    # No-export compliance.
    if "grid_export_kw" in dispatched.columns and float(dispatched["grid_export_kw"].max()) > 1e-6:
        flag("No-export compliance risk",
             "Dispatch produced grid export in no-export mode.",
             "Fix the no-export controller before approval.")

    return flags


def investor_recommendation(case: dict, finance: FinanceCase,
                            risk: RiskAssumptions | None = None,
                            red_flags: list[dict] | None = None,
                            baseline_error_pct: float = 0.0,
                            data_quality_fail: bool = False,
                            readiness_status: str | None = None) -> str:
    """Deterministic GO / REVISE / NO-GO gate (spec 21.6 + Fix 3)."""
    from agents.btm_investment_readiness_agent import STATUS_INVESTMENT_READY

    if readiness_status and readiness_status != STATUS_INVESTMENT_READY:
        return f"BLOCKED: case status is {readiness_status} — investor recommendation requires INVESTMENT READY data"
    risk = risk or RiskAssumptions()
    if data_quality_fail:
        return "NO-GO: insufficient data quality"
    if baseline_error_pct > 3:
        return "REVISE: baseline bill must be reconciled before investment approval"
    if case["net_monthly_benefit_downside_mxn"] < 0:
        return "NO-GO: downside case has negative monthly cash benefit"
    if case["simple_payback_years"] > risk.required_max_payback_years:
        return "REVISE: resize BESS or improve commercial structure"
    if case["simple_payback_years"] > finance.warranty_years:
        return "NO-GO: payback exceeds battery warranty period"
    if case["simple_payback_years"] > finance.bess_life_years:
        return "NO-GO: payback exceeds useful BESS life"
    if case.get("npv_base_mxn", 0.0) < 0:
        return "REVISE: NPV over BESS life is negative at the required discount rate"
    if case["net_monthly_benefit_base_mxn"] < risk.required_min_net_monthly_benefit_mxn:
        return "REVISE: net monthly benefit below required minimum"
    return "GO: proceed to quote, site survey, and contract negotiation"


def write_investor_package(
    out_path,
    case: dict,
    recommendation: str,
    baseline_bills: pd.DataFrame,
    optimized_bills: pd.DataFrame,
    red_flags: list[dict],
    assumptions: dict | None = None,
):
    """Investor evidence package (spec 21.8) as one Excel workbook."""
    from pathlib import Path

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    monthly = pd.DataFrame({
        "month": baseline_bills["month"],
        "baseline_bill_mxn": baseline_bills["total"],
        "optimized_bill_mxn": optimized_bills["total"],
    })
    monthly["gross_savings_mxn"] = monthly["baseline_bill_mxn"] - monthly["optimized_bill_mxn"]
    monthly["net_benefit_mxn"] = (
        monthly["gross_savings_mxn"]
        - case["monthly_financing_payment_mxn"]
        - case["monthly_opex_insurance_mxn"]
    )
    watermark = case.get("report_watermark", "")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        if watermark:
            pd.DataFrame([{"notice": watermark}]).to_excel(writer, sheet_name="Notice", index=False)
        investor_dashboard_frame(case, recommendation).to_excel(writer, sheet_name="Dashboard", index=False)
        monthly.to_excel(writer, sheet_name="Monthly_Cash_Benefit", index=False)
        baseline_bills.to_excel(writer, sheet_name="Baseline_Bills", index=False)
        optimized_bills.to_excel(writer, sheet_name="Optimized_Bills", index=False)
        (pd.DataFrame(red_flags) if red_flags else pd.DataFrame([{"red_flag": "none", "detail": "-", "recommended_action": "-"}])
         ).to_excel(writer, sheet_name="Red_Flags", index=False)
        haircuts = pd.DataFrame(list(case["haircuts"].items()), columns=["haircut", "value"])
        haircuts.to_excel(writer, sheet_name="Haircuts", index=False)
        if assumptions:
            pd.DataFrame(list(assumptions.items()), columns=["assumption", "value"]).to_excel(
                writer, sheet_name="Assumptions", index=False)
    return out_path


def investor_dashboard_frame(case: dict, recommendation: str) -> pd.DataFrame:
    irr_txt = f"{case['project_irr_pct']:.1f}%" if case.get("project_irr_pct") is not None else "n/a"
    rows = [
        ("Baseline annual CFE bill (MXN)", f"{case['baseline_annual_bill_mxn']:,.0f}"),
        ("Optimized annual CFE bill (MXN)", f"{case['optimized_annual_bill_mxn']:,.0f}"),
        ("Modeled annual savings (MXN)", f"{case['modeled_annual_savings_mxn']:,.0f}"),
        ("Bankable annual savings (MXN)", f"{case['bankable_annual_savings_mxn']:,.0f}"),
        ("Annual financing payment (MXN)", f"{case['annual_financing_payment_mxn']:,.0f}"),
        ("Annual O&M + insurance (MXN)", f"{case['annual_opex_insurance_mxn']:,.0f}"),
        ("Net annual benefit — base (MXN)", f"{case['net_annual_benefit_base_mxn']:,.0f}"),
        ("Net annual benefit — downside (MXN)", f"{case['net_annual_benefit_downside_mxn']:,.0f}"),
        ("Net annual benefit — upside (MXN)", f"{case['net_annual_benefit_upside_mxn']:,.0f}"),
        ("Net monthly benefit — base (MXN)", f"{case['net_monthly_benefit_base_mxn']:,.0f}"),
        ("Net monthly benefit — downside (MXN)", f"{case['net_monthly_benefit_downside_mxn']:,.0f}"),
        ("Net monthly benefit — upside (MXN)", f"{case['net_monthly_benefit_upside_mxn']:,.0f}"),
        ("Worst-month savings (MXN)", f"{case['worst_month_savings_mxn']:,.0f}"),
        ("Simple payback (years)", f"{case['simple_payback_years']:.1f}"),
        ("BESS life (years)", f"{case['bess_life_years']:.0f}"),
        (f"NPV over BESS life — base (MXN)", f"{case['npv_base_mxn']:,.0f}"),
        (f"NPV over BESS life — downside (MXN)", f"{case['npv_downside_mxn']:,.0f}"),
        ("Lifetime net benefit — base (MXN)", f"{case['lifetime_net_benefit_base_mxn']:,.0f}"),
        ("Project IRR (unlevered)", irr_txt),
        ("IRR on CFE savings — unlevered", irr_txt),
    ]
    if case.get("savings_irr_levered_pct") is not None:
        rows.append(("IRR on CFE savings — levered", f"{case['savings_irr_levered_pct']:.1f}%"))
    if case.get("capex_mxn") is not None:
        rows.insert(0, ("Total CAPEX (MXN)", f"{case['capex_mxn']:,.0f}"))
        rows.insert(1, ("Annual OPEX (MXN)", f"{case.get('annual_opex_mxn', 0):,.0f}"))
        rows.insert(2, ("Annual insurance (MXN)", f"{case.get('annual_insurance_mxn', 0):,.0f}"))
    rows.extend([
        ("Recommendation", recommendation),
    ])
    if case.get("readiness_status"):
        rows.insert(0, ("Case status", case["readiness_status"]))
    if case.get("dispatch_engine"):
        rows.insert(1 if case.get("readiness_status") else 0, ("Dispatch engine", case["dispatch_engine"]))
    if case.get("btm_value_streams"):
        for stream, val in case["btm_value_streams"].items():
            rows.append((f"BTM stream — {stream}", f"{val:,.0f}"))
    df = pd.DataFrame(rows, columns=["metric", "value"])
    return df.astype({"metric": "string", "value": "string"})
