"""Tests for PV profile synthesis from system specs."""
import pandas as pd
import pytest

from agents.pv_synthesis_agent import (
    PVSystemSpec,
    annual_production_mwh,
    synthesize_pv_15min,
    synthesize_pv_8760,
)


def test_annual_production_formula():
    spec = PVSystemSpec(mw_ac=100, mwp=100, yield_kwh_kwp_yr=1800, degradation_pct_yr=0.4, operation_year=1)
    # 100 MWp = 100,000 kWp * 1800 kWh/kWp = 180,000,000 kWh = 180,000 MWh
    assert annual_production_mwh(spec) == pytest.approx(180_000.0)


def test_degradation_reduces_year_two():
    y1 = PVSystemSpec(mw_ac=10, mwp=10, yield_kwh_kwp_yr=1600, degradation_pct_yr=0.4, operation_year=1)
    y2 = PVSystemSpec(mw_ac=10, mwp=10, yield_kwh_kwp_yr=1600, degradation_pct_yr=0.4, operation_year=2)
    assert annual_production_mwh(y2) == pytest.approx(annual_production_mwh(y1) * 0.996)


def test_synthesized_8760_matches_annual_target():
    spec = PVSystemSpec(mw_ac=50, mwp=60, yield_kwh_kwp_yr=1700, degradation_pct_yr=0.4)
    idx = pd.date_range("2026-01-01", "2026-12-31 23:00", freq="h")
    idx = idx[~((idx.month == 2) & (idx.day == 29))]
    df = synthesize_pv_8760(idx, spec)
    assert len(df) == len(idx)
    assert df["pv_mwh"].sum() == pytest.approx(annual_production_mwh(spec), rel=0.02)


def test_ac_clipping_limits_hourly_output():
    spec = PVSystemSpec(mw_ac=10, mwp=100, yield_kwh_kwp_yr=1800, degradation_pct_yr=0.4)
    idx = pd.date_range("2026-06-01", periods=24, freq="h")
    df = synthesize_pv_8760(idx, spec)
    assert df["pv_mwh"].max() <= 10.0 + 1e-6


def test_zero_mwp_produces_flat_zeros():
    spec = PVSystemSpec(mw_ac=100, mwp=0, yield_kwh_kwp_yr=1800)
    assert annual_production_mwh(spec) == 0.0
    idx = pd.date_range("2026-06-01", periods=24, freq="h")
    df = synthesize_pv_8760(idx, spec)
    assert df["pv_mwh"].sum() == 0.0


def test_synthesized_8760_accepts_series_timestamps():
    spec = PVSystemSpec(mw_ac=50, mwp=50, yield_kwh_kwp_yr=1800)
    idx = pd.date_range("2026-01-01", periods=8760, freq="h")
    series = pd.Series(idx)  # same path as app.py pml_dt
    df = synthesize_pv_8760(series, spec)
    assert len(df) == 8760
    assert df["pv_mwh"].sum() > 0


def test_synthesized_15min_produces_kw():
    spec = PVSystemSpec(mw_ac=0.5, mwp=0.5, yield_kwh_kwp_yr=1800, degradation_pct_yr=0.4)
    idx = pd.date_range("2026-01-01", periods=96 * 7, freq="15min")
    df = synthesize_pv_15min(idx, spec)
    assert "pv_kw" in df.columns
    assert df["pv_kw"].max() <= 500.0 + 1e-3
    energy_mwh = (df["pv_kw"] * 0.25 / 1000.0).sum()
    assert energy_mwh > 0
