import numpy as np
import pandas as pd
import pytest

from agents.dispatch_agent import BESSConfig, dispatch_lp, dispatch_price_rank, run_dispatch


def make_two_day_frame() -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=48, freq="h")
    hours = idx.hour.to_numpy()
    pml = np.where(hours >= 18, 2000.0, np.where((hours >= 9) & (hours <= 15), 300.0, 800.0))
    pv = np.where((hours >= 7) & (hours <= 18), 40.0, 0.0)
    return pd.DataFrame({"datetime": idx, "pml": pml, "pv_mwh": pv})


def test_price_rank_energy_balance():
    df = make_two_day_frame()
    bess = BESSConfig(mw=20, mwh=80)
    out = dispatch_price_rank(df, bess)
    # PV is either sold or charged, never lost or duplicated
    assert np.allclose(out["pv_to_grid_mwh"] + out["pv_to_bess_mwh"], out["pv_mwh"])
    assert (out["soc_mwh"] <= bess.mwh + 1e-6).all()
    assert (out["soc_mwh"] >= 0).all()
    assert out["bess_discharge_mwh"].sum() > 0


def test_lp_dispatch_beats_or_matches_price_rank():
    pytest.importorskip("scipy")
    df = make_two_day_frame()
    bess = BESSConfig(mw=20, mwh=80)
    simple = dispatch_price_rank(df, bess)
    lp = dispatch_lp(df, bess)
    assert lp["merchant_revenue"].sum() >= simple["merchant_revenue"].sum() - 1e-6


def test_lp_respects_limits():
    pytest.importorskip("scipy")
    df = make_two_day_frame()
    bess = BESSConfig(mw=20, mwh=80, reserve_fraction=0.1)
    out = dispatch_lp(df, bess)
    assert (out["bess_discharge_mwh"] <= bess.mw + 1e-6).all()
    assert (out["soc_mwh"] >= bess.mwh * bess.reserve_fraction - 1e-6).all()
    assert (out["soc_mwh"] <= bess.mwh + 1e-6).all()
    # PV-only charging by default
    assert (out["grid_to_bess_mwh"] <= 1e-6).all()


def test_run_dispatch_engine_switch():
    df = make_two_day_frame()
    bess = BESSConfig(mw=20, mwh=80)
    out = run_dispatch(df, bess, engine="price_rank")
    assert "merchant_revenue" in out.columns
