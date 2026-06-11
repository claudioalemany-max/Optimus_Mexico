import numpy as np
import pandas as pd
import pytest

from agents.capacity_agent import CapacityConfig, capacity_summary_frame, compute_capacity_credit, identify_critical_hours


def make_dispatch(n_hours: int = 500) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    idx = pd.date_range("2025-01-01", periods=n_hours, freq="h")
    return pd.DataFrame({
        "datetime": idx,
        "pml": rng.uniform(300, 3000, n_hours),
        "pv_to_grid_mwh": rng.uniform(0, 50, n_hours),
        "bess_discharge_mwh": rng.uniform(0, 20, n_hours),
    })


def test_identify_critical_hours_pml():
    d = make_dispatch()
    crit = identify_critical_hours(d, n_hours=100)
    assert len(crit) == 100
    assert crit["pml"].min() >= d["pml"].nlargest(101).iloc[-1] - 1e-9
    assert list(crit["critical_rank"]) == list(range(1, 101))


def test_identify_critical_hours_provided():
    d = make_dispatch()
    d["is_critical_hour"] = False
    d.loc[d.index[:60], "is_critical_hour"] = True
    crit = identify_critical_hours(d, n_hours=100, criterion="provided")
    assert len(crit) == 60


def test_compute_capacity_credit():
    d = make_dispatch()
    cfg = CapacityConfig(capacity_price_mxn_mw_year=1_000_000, n_critical_hours=100)
    result = compute_capacity_credit(d, cfg)
    crit = result["critical_hours"]
    expected_mw = (crit["pv_to_grid_mwh"] + crit["bess_discharge_mwh"]).mean()
    assert result["accredited_capacity_mw"] == pytest.approx(expected_mw, abs=1e-3)
    assert result["capacity_revenue_mxn"] == pytest.approx(expected_mw * 1_000_000, rel=1e-4)
    summary = capacity_summary_frame(result)
    assert len(summary) == 5


def test_missing_columns_raise():
    with pytest.raises(ValueError):
        compute_capacity_credit(pd.DataFrame({"datetime": [], "pml": []}), CapacityConfig(capacity_price_mxn_mw_year=1))
