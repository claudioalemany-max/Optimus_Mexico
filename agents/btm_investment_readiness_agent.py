"""Investment readiness gate — Fix 3.

Labels every BTM case DEMO | SCREENING | REVISE | INVESTMENT_READY and
blocks GO/REVISE/NO-GO unless INVESTMENT_READY.
"""
from __future__ import annotations

import pandas as pd

STATUS_DEMO = "DEMO"
STATUS_SCREENING = "SCREENING"
STATUS_REVISE = "REVISE"
STATUS_INVESTMENT_READY = "INVESTMENT_READY"

ALLOWED_ACTIONS = {
    STATUS_DEMO: ["view_demo", "export_demo_report"],
    STATUS_SCREENING: ["indicative_savings", "data_request"],
    STATUS_REVISE: ["qa_report", "data_request"],
    STATUS_INVESTMENT_READY: ["go_revise_nogo", "investor_report", "evidence_package"],
}


def assess_meter_quality(load_df: pd.DataFrame) -> dict:
    df = load_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    expected = pd.date_range(df["timestamp"].min(), df["timestamp"].max(), freq="15min")
    n_expected = len(expected)
    n_actual = len(df)
    dupes = int(df["timestamp"].duplicated().sum())
    missing_pct = max(0.0, (1 - n_actual / n_expected) * 100) if n_expected else 100.0
    months = int(df["timestamp"].dt.to_period("M").nunique())
    negatives = int((pd.to_numeric(df.get("load_kw", 0), errors="coerce") < 0).sum())
    return {
        "months_15min": months,
        "intervals": n_actual,
        "expected_intervals": n_expected,
        "missing_interval_pct": round(missing_pct, 3),
        "duplicate_timestamps": dupes,
        "negative_load_count": negatives,
    }


def bill_reconstruction_summary(baseline_bills: pd.DataFrame, actual_bills: pd.DataFrame | None = None) -> dict:
    """Reconciliation metrics. Without uploaded CFE bills, annual_error_pct is None."""
    if actual_bills is None or actual_bills.empty:
        return {
            "annual_error_pct": None,
            "months_failed": 0,
            "monthly_errors": [],
            "has_actual_bills": False,
        }
    merged = baseline_bills.merge(actual_bills, on="month", how="inner", suffixes=("_recon", "_actual"))
    if merged.empty:
        return {"annual_error_pct": 100.0, "months_failed": 12, "monthly_errors": [], "has_actual_bills": True}
    merged["error_pct"] = (
        (merged["total_recon"] - merged["total_actual"]).abs()
        / merged["total_actual"].replace(0, pd.NA) * 100
    ).astype(float)
    failed = int((merged["error_pct"] > 3).sum())
    annual_err = float(
        (merged["total_recon"].sum() - merged["total_actual"].sum())
        / merged["total_actual"].sum() * 100
    ) if merged["total_actual"].sum() else 100.0
    return {
        "annual_error_pct": abs(annual_err),
        "months_failed": failed,
        "monthly_errors": merged[["month", "error_pct"]].to_dict("records"),
        "has_actual_bills": True,
    }


def evaluate_investment_readiness(
    customer_files: dict,
    meter_quality: dict,
    bill_reconstruction: dict,
    project_config: dict,
) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    if project_config.get("uses_synthetic_data", False):
        return _result(STATUS_DEMO, ["Synthetic sample data in use"], warnings, 0.0)

    if customer_files.get("cfe_bill_months", 0) < project_config.get("min_bill_months", 12):
        issues.append("Upload 12 monthly CFE bills for investment-ready status")
    if meter_quality.get("months_15min", 0) < project_config.get("min_load_months", 12):
        issues.append("Upload 12 months of 15-minute load data")
    if meter_quality.get("missing_interval_pct", 100) > project_config.get("max_missing_interval_pct", 1.0):
        issues.append("15-minute meter data has more than 1% missing intervals")
    if meter_quality.get("duplicate_timestamps", 0) > 0:
        issues.append("Remove duplicate timestamps in load data")
    if meter_quality.get("negative_load_count", 0) > 0:
        warnings.append("Negative load values detected — review meter data")
    if not project_config.get("tariff_confirmed", False):
        issues.append("Confirm CFE tariff and division")
    if project_config.get("mode") == "pv_bess" and not customer_files.get("has_pv_profile", False):
        issues.append("Upload PV profile for PV+BESS case")

    if issues:
        return _result(STATUS_SCREENING, issues, warnings, 0.35)

    if not bill_reconstruction.get("has_actual_bills", False):
        warnings.append("No CFE bills uploaded — bill reconciliation not verified")
        return _result(STATUS_SCREENING, ["Upload CFE bills to reconcile baseline reconstruction"], warnings, 0.55)

    annual_err = bill_reconstruction.get("annual_error_pct")
    if annual_err is not None and annual_err > project_config.get("max_bill_reconstruction_error_pct", 3.0):
        return _result(
            STATUS_REVISE,
            [f"Baseline bill reconstruction error {annual_err:.1f}% exceeds 3% threshold"],
            warnings, 0.65,
        )
    if bill_reconstruction.get("months_failed", 0) > 2:
        return _result(STATUS_REVISE, ["Too many monthly reconstruction failures (>2 months >3% error)"], warnings, 0.65)

    confidence = 0.85 if not warnings else 0.75
    return _result(STATUS_INVESTMENT_READY, [], warnings, confidence)


def _result(status: str, blocking: list[str], warnings: list[str], confidence: float) -> dict:
    return {
        "status": status,
        "blocking_issues": blocking,
        "warnings": warnings,
        "allowed_actions": ALLOWED_ACTIONS.get(status, []),
        "required_next_files": blocking,
        "confidence_score": confidence,
        "investor_recommendation_allowed": status == STATUS_INVESTMENT_READY,
    }


def status_badge_color(status: str) -> str:
    return {
        STATUS_DEMO: "#6c757d",
        STATUS_SCREENING: "#fd7e14",
        STATUS_REVISE: "#dc3545",
        STATUS_INVESTMENT_READY: "#198754",
    }.get(status, "#6c757d")
