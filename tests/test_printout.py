import numpy as np
import pandas as pd
import pytest

from agents.printout_agent import ReportData, write_all_reports, write_full_workbook


def make_report() -> ReportData:
    idx = pd.date_range("2025-01-01", periods=72, freq="h")
    rng = np.random.default_rng(3)
    dispatch = pd.DataFrame({
        "datetime": idx,
        "pml": rng.uniform(400, 2000, len(idx)),
        "pv_mwh": rng.uniform(0, 60, len(idx)),
        "pv_to_grid_mwh": rng.uniform(0, 50, len(idx)),
        "pv_to_bess_mwh": rng.uniform(0, 10, len(idx)),
        "bess_discharge_mwh": rng.uniform(0, 20, len(idx)),
    })
    dispatch["merchant_revenue"] = (dispatch["pv_to_grid_mwh"] + dispatch["bess_discharge_mwh"]) * dispatch["pml"]
    scenarios = pd.DataFrame({"duration_h": [2, 4], "merchant_revenue": [1e6, 2e6]})
    return ReportData(
        project_name="Test Project",
        dispatch=dispatch,
        scenario_summary=scenarios,
        assumptions={"BESS MW": 50},
    )


def test_full_workbook(tmp_path):
    path = write_full_workbook(make_report(), tmp_path / "report.xlsx")
    assert path.exists()
    sheets = pd.ExcelFile(path).sheet_names
    assert {"Summary", "Dispatch_8760", "Monthly_Summary", "Scenario_Comparison", "Assumptions"} <= set(sheets)


def test_write_all_reports(tmp_path):
    written = write_all_reports(make_report(), tmp_path, basename="TestReport")
    assert "xlsx" in written
    for fmt, path in written.items():
        assert path.exists(), f"{fmt} report missing"
        assert path.stat().st_size > 0


def test_word_pdf_ppt_if_available(tmp_path):
    pytest.importorskip("docx")
    pytest.importorskip("reportlab")
    pytest.importorskip("pptx")
    written = write_all_reports(make_report(), tmp_path, basename="FullReport")
    assert {"xlsx", "docx", "pdf", "pptx"} <= set(written)
