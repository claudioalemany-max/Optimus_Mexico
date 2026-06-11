"""Fix 1 — BTM revenue scope guard tests."""
from agents.btm_revenue_guard import (
    ALLOWED_BTM_VALUE_STREAMS,
    DISABLED_IN_BTM,
    PROJECT_MODE_BTM,
    enforce_btm_revenue_scope,
    savings_by_btm_stream,
)
import pandas as pd


def test_btm_mode_disables_merchant_streams():
    selected = ALLOWED_BTM_VALUE_STREAMS + DISABLED_IN_BTM
    result = enforce_btm_revenue_scope(PROJECT_MODE_BTM, selected)
    assert not result["is_valid"]
    assert set(result["disabled_streams"]) == set(DISABLED_IN_BTM)
    assert result["allowed_streams"] == ALLOWED_BTM_VALUE_STREAMS


def test_btm_mode_ok_with_allowed_only():
    result = enforce_btm_revenue_scope(PROJECT_MODE_BTM, ALLOWED_BTM_VALUE_STREAMS)
    assert result["is_valid"]
    assert result["disabled_streams"] == []


def test_api_user_merchant_streams_removed_with_warning():
    result = enforce_btm_revenue_scope(PROJECT_MODE_BTM, ["pml_merchant_revenue", "cfe_energy_savings"])
    assert "pml_merchant_revenue" in result["disabled_streams"]
    assert result["allowed_streams"] == ["cfe_energy_savings"]
    assert "excludes merchant" in result["message"]


def test_savings_by_btm_stream_maps_bill_components():
    base = pd.DataFrame({
        "energy_charge": [100.0, 100.0],
        "capacity_charge": [50.0, 50.0],
        "distribution_charge": [30.0, 30.0],
    })
    opt = pd.DataFrame({
        "energy_charge": [80.0, 80.0],
        "capacity_charge": [40.0, 40.0],
        "distribution_charge": [25.0, 25.0],
    })
    streams = savings_by_btm_stream(base, opt)
    assert streams["cfe_energy_savings"] == 40.0
    assert streams["cfe_capacity_savings"] == 20.0
    assert streams["cfe_distribution_savings"] == 10.0
