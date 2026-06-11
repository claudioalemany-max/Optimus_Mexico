import numpy as np
import pandas as pd
import pytest

from agents.ppa_agent import PPAConfig, apply_ppa, compare_structures, ppa_summary


def make_dispatch() -> pd.DataFrame:
    idx = pd.date_range("2025-06-01", periods=24, freq="h")
    return pd.DataFrame({
        "datetime": idx,
        "pml": np.linspace(500, 1500, 24),
        "pv_to_grid_mwh": np.where((idx.hour >= 8) & (idx.hour <= 17), 30.0, 0.0),
        "bess_discharge_mwh": np.where(idx.hour >= 19, 20.0, 0.0),
    })


def test_pro_rata_split():
    d = make_dispatch()
    out = apply_ppa(d, PPAConfig(ppa_price_mxn_mwh=1000, mode="pro_rata", ppa_fraction=0.6))
    delivered = d["pv_to_grid_mwh"] + d["bess_discharge_mwh"]
    assert np.allclose(out["ppa_mwh"], delivered * 0.6)
    assert np.allclose(out["ppa_mwh"] + out["merchant_mwh"], delivered)
    assert np.allclose(out["ppa_revenue"], out["ppa_mwh"] * 1000)


def test_baseload_split():
    d = make_dispatch()
    out = apply_ppa(d, PPAConfig(ppa_price_mxn_mwh=1000, mode="baseload", ppa_mw=25.0))
    assert (out["ppa_mwh"] <= 25.0 + 1e-9).all()
    delivered = d["pv_to_grid_mwh"] + d["bess_discharge_mwh"]
    assert np.allclose(out["ppa_mwh"] + out["merchant_mwh"], delivered)


def test_solar_only_split():
    d = make_dispatch()
    out = apply_ppa(d, PPAConfig(ppa_price_mxn_mwh=1000, mode="solar_only"))
    assert np.allclose(out["ppa_mwh"], d["pv_to_grid_mwh"])
    assert np.allclose(out["merchant_mwh"], d["bess_discharge_mwh"])


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        apply_ppa(make_dispatch(), PPAConfig(ppa_price_mxn_mwh=1000, mode="bogus"))


def test_summary_and_compare():
    d = make_dispatch()
    out = apply_ppa(d, PPAConfig(ppa_price_mxn_mwh=1000))
    summary = ppa_summary(out)
    assert len(summary) == 1
    assert summary.loc[0, "total_energy_revenue"] == pytest.approx(out["total_energy_revenue"].sum())

    comparison = compare_structures(d, {
        "70% pro rata": PPAConfig(ppa_price_mxn_mwh=1000, ppa_fraction=0.7),
        "solar only": PPAConfig(ppa_price_mxn_mwh=1000, mode="solar_only"),
    })
    assert len(comparison) == 2
    assert "total_energy_revenue" in comparison.columns
