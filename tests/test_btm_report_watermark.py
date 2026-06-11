"""Report watermark for non-investment-ready cases."""
from pathlib import Path

import pandas as pd

from agents.btm_investment_readiness_agent import STATUS_DEMO, STATUS_SCREENING
from agents.btm_investor_agent import FinanceCase, build_small_industry_investor_case, write_investor_package


def _bills(totals):
    return pd.DataFrame({
        "month": range(1, len(totals) + 1), "total": totals,
        "energy_charge": totals, "capacity_charge": [0] * len(totals),
        "distribution_charge": [0] * len(totals), "fixed_charge": [0] * len(totals),
    })


def test_screening_report_has_notice_sheet(tmp_path):
    base, opt = _bills([1e6] * 12), _bills([8e5] * 12)
    fin = FinanceCase(capex_mxn=3e6)
    case = build_small_industry_investor_case(base, opt, fin)
    case["report_watermark"] = f"{STATUS_DEMO} — NOT FOR INVESTOR USE"
    case["readiness_status"] = STATUS_DEMO
    out = tmp_path / "pkg.xlsx"
    write_investor_package(out, case, "BLOCKED: DEMO", base, opt, [], assumptions={"status": STATUS_DEMO})
    xl = pd.ExcelFile(out)
    assert "Notice" in xl.sheet_names
    notice = pd.read_excel(out, sheet_name="Notice")
    assert "NOT FOR INVESTOR" in notice.iloc[0, 0]


def test_demo_watermark_text():
    for status in (STATUS_DEMO, STATUS_SCREENING):
        wm = f"{status} — NOT FOR INVESTOR USE"
        assert "NOT FOR INVESTOR USE" in wm
