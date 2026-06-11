"""BTM revenue scope guard — Fix 1.

When project_mode = BTM_CFE, merchant/PML/PPA/CENACE capacity streams are
disabled. Only CFE bill savings and PV self-consumption value streams apply.
"""
from __future__ import annotations

ALLOWED_BTM_VALUE_STREAMS = [
    "cfe_energy_savings",
    "cfe_capacity_savings",
    "cfe_distribution_savings",
    "pv_self_consumption_value",
    "backup_value_optional",
]

DISABLED_IN_BTM = [
    "pml_merchant_revenue",
    "ppa_revenue",
    "cenace_capacity_revenue",
    "ancillary_services_revenue",
]

PROJECT_MODE_BTM = "BTM_CFE"


def enforce_btm_revenue_scope(project_mode: str, selected_streams: list[str]) -> dict:
    disabled = []
    allowed = list(selected_streams)
    if project_mode == PROJECT_MODE_BTM:
        disabled = [s for s in selected_streams if s in DISABLED_IN_BTM]
        allowed = [s for s in selected_streams if s in ALLOWED_BTM_VALUE_STREAMS]
    return {
        "allowed_streams": allowed,
        "disabled_streams": disabled,
        "is_valid": len(disabled) == 0,
        "message": (
            "BTM_CFE mode excludes merchant/PPA/CENACE capacity revenues."
            if disabled else "OK"
        ),
    }


def savings_by_btm_stream(baseline_bills, optimized_bills) -> dict[str, float]:
    """Map bill components to allowed BTM value streams (MXN/year)."""
    base, opt = baseline_bills, optimized_bills
    return {
        "cfe_energy_savings": float(base["energy_charge"].sum() - opt["energy_charge"].sum()),
        "cfe_capacity_savings": float(base["capacity_charge"].sum() - opt["capacity_charge"].sum()),
        "cfe_distribution_savings": float(base["distribution_charge"].sum() - opt["distribution_charge"].sum()),
        "pv_self_consumption_value": 0.0,  # filled by caller when PV profile available
        "backup_value_optional": 0.0,
    }
