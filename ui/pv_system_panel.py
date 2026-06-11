"""Shared Streamlit panel: upload PV profile or enter MW AC / MWp / yield / degradation."""
from __future__ import annotations

import streamlit as st

from agents.pv_synthesis_agent import PVSystemSpec, annual_production_summary


def render_pv_system_panel(
    key: str,
    *,
    upload_label: str = "Upload PV profile CSV/XLSX",
    upload_types: list[str] | None = None,
    show_upload: bool = True,
    default_mw_ac: float = 100.0,
    default_mwp: float = 100.0,
    default_yield: float = 1800.0,
    default_degradation: float = 0.4,
) -> tuple[str, object | None, PVSystemSpec | None]:
    """Returns (source, uploaded_file, pv_spec).

    source is ``upload`` or ``specs``. Exactly one of file or spec is set when active.
    """
    upload_types = upload_types or ["csv", "xlsx", "xls"]
    options = ["Enter system specs (MW AC / MWp / yield)"]
    if show_upload:
        options.append("Upload production profile")

    source = st.radio(
        "PV data source",
        options,
        horizontal=True,
        key=f"{key}_pv_source",
    )
    uploaded = None
    spec: PVSystemSpec | None = None

    if "Upload" in source:
        uploaded = st.file_uploader(upload_label, type=upload_types, key=f"{key}_pv_file")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        mw_ac = c1.number_input("MW AC", value=default_mw_ac, min_value=0.0, key=f"{key}_mw_ac")
        mwp = c2.number_input("MWp (DC)", value=default_mwp, min_value=0.0, key=f"{key}_mwp")
        yield_kwh = c3.number_input(
            "Yield (kWh/kWp/year)", value=default_yield, min_value=0.0, key=f"{key}_yield",
        )
        degradation = c4.number_input(
            "Degradation (%/year)", value=default_degradation, min_value=0.0, max_value=5.0,
            format="%.2f", key=f"{key}_deg",
        )
        op_year = c5.number_input(
            "Operation year", value=1, min_value=1, max_value=40, key=f"{key}_op_year",
            help="Year 1 uses full yield; year 2+ applies cumulative degradation.",
        )
        spec = PVSystemSpec(
            mw_ac=mw_ac, mwp=mwp, yield_kwh_kwp_yr=yield_kwh,
            degradation_pct_yr=degradation, operation_year=int(op_year),
        )
        if mwp > 0 and yield_kwh > 0:
            summary = annual_production_summary(spec)
            st.caption(
                f"Estimated annual production (Year {int(op_year)}): "
                f"**{summary['annual_mwh']:,.0f} MWh** "
                f"({summary['effective_yield_kwh_kwp']:,.0f} kWh/kWp effective | "
                f"CF ~{summary['capacity_factor_pct']:.1f}% | "
                f"DC/AC {summary['dc_ac_ratio']:.2f})"
            )
            if mw_ac > 0 and mwp > mw_ac:
                st.caption(f"AC clipping at **{mw_ac:,.1f} MW** — production capped at inverter limit.")
        else:
            st.caption("**No PV production** — BESS-only analysis (set MWp or yield > 0 to model solar).")

    return ("upload" if "Upload" in source else "specs"), uploaded, spec
