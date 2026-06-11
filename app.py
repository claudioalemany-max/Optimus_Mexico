from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from agents.node_resolver_agent import extract_nodes_from_pml_pdf, extract_nodes_from_table, resolve_nodes, write_resolution_workbook
from agents.node_map_agent import build_node_map
from agents.pv_loader_agent import load_pv_8760, merge_pml_pv
from agents.pv_synthesis_agent import PVSystemSpec, annual_production_mwh, synthesize_pv_8760, synthesize_pv_15min
from ui.pv_system_panel import render_pv_system_panel
from agents.dispatch_agent import BESSConfig, run_dispatch
from agents.optimizer_agent import sweep_bess_durations
from agents.ppa_agent import PPAConfig, apply_ppa, ppa_summary
from agents.capacity_agent import CapacityConfig, capacity_summary_frame, compute_capacity_credit
from agents.printout_agent import ReportData, write_all_reports
from agents.fom_investor_agent import build_fom_investor_case, compute_project_capex_mxn, fom_economics_frame
from agents.btm_investor_agent import FinanceCase as FOMFinanceCase

st.set_page_config(page_title="Optimus Mexico", layout="wide")
st.title("Optimus Mexico — PV+BESS Optimizer")

DATA = Path("data")
OUT = Path("outputs")
for p in [DATA / "raw", DATA / "clean", OUT / "excel", OUT / "maps"]:
    p.mkdir(parents=True, exist_ok=True)

page = st.radio(
    "Module",
    ["How it works", "0. Node Resolver", "1. Front-of-Meter Dispatch + PPA + Capacity", "2. Behind-the-Meter (CFE)"],
    horizontal=True,
    label_visibility="collapsed",
)
st.divider()

DEFAULT_NODE_SOURCE = Path("outputs/pml/pml_clean.csv")
_CATALOG_CANDIDATES = [
    Path("data/catalogs/nodes_catalog_enriched.csv"),
    Path("data/catalogs/nodes_catalog1.csv"),
    Path("data/catalogs/nodes_catalog.csv"),
]
DEFAULT_CATALOG = next((p for p in _CATALOG_CANDIDATES if p.exists()), _CATALOG_CANDIDATES[0])
SAMPLE_PML = Path("data/sample/pml_8760_sample.csv")
SAMPLE_PV = Path("data/sample/pv_8760_sample.csv")

@st.cache_data(show_spinner=False)
def load_catalog_cached(path_str: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path_str, dtype=str).fillna("")


if page == "How it works":
    st.header("How Optimus Mexico works")
    st.info(
        "**Purpose:** Optimus Mexico estimates how much money a solar + battery project would make "
        "at any node of the Mexican electricity market by combining CENACE hourly prices with a PV "
        "profile, optimizing the battery dispatch, and reporting revenues from energy arbitrage, "
        "PPAs, and the capacity market."
    )
    st.write(
        "Optimus Mexico evaluates the economics of a PV + battery (BESS) project at any node "
        "of the Mexican wholesale electricity market (MEM). It combines CENACE market data, "
        "a solar production profile and a battery dispatch optimizer to estimate revenues "
        "from energy arbitrage, PPAs and the capacity market."
    )

    st.subheader("Pipeline flow")
    st.graphviz_chart(
        """
        digraph {
            rankdir=TB;
            node [shape=box, style="rounded,filled", fillcolor="#eef3fb", fontname="Helvetica", fontsize=11];
            edge [fontname="Helvetica", fontsize=9, color="#666666"];

            catalog   [label="CENACE node catalog\\n(~3,000 NodosP, enriched)"];
            resolver  [label="Module 0 — Node Resolver\\npick / match node keys", fillcolor="#dcebd2"];
            pml       [label="PML prices (8,760 h)\\nenergy + congestion + losses"];
            pv        [label="PV production profile\\n(8,760 h)"];
            merge     [label="Merge PML + PV\\nhourly revenue base"];
            dispatch  [label="BESS dispatch\\nprice-rank or linear optimization", fillcolor="#dcebd2"];
            sweep     [label="Optimizer sweep\\nbattery durations 1-6 h"];
            ppa       [label="PPA agent\\npro-rata / baseload / solar-only split"];
            capacity  [label="Capacity agent\\n100 critical hours credit"];
            reports   [label="Reports\\nExcel - Word - PDF - PowerPoint", fillcolor="#fbe8d2"];

            catalog -> resolver;
            resolver -> pml [label="node key"];
            pml -> merge;
            pv -> merge;
            merge -> dispatch;
            dispatch -> sweep;
            dispatch -> ppa;
            dispatch -> capacity;
            sweep -> reports;
            ppa -> reports;
            capacity -> reports;
        }
        """
    )

    st.subheader("Step by step")
    st.markdown(
        """
1. **Node Resolver (Module 0).** Pick one or more nodes from the enriched CENACE catalog
   (searchable dropdown with filters by zona de carga, estado and tipo), or extract node keys
   from a PML report (PDF/CSV/XLSX). Each node is resolved to its full context: sistema,
   zona de carga, voltage, tipo (generación/carga), gerencia regional, estado and municipio.

2. **Market data.** Hourly PML prices (Precio Marginal Local) for the chosen node — 8,760 hours
   of energy, congestion and loss components. Sample data is included; real data comes from
   CENACE's MDA/MTR reports.

3. **PV profile.** An 8,760-hour solar production profile for the project (from PVsyst,
   PVGIS or the included sample).

4. **BESS dispatch (Module 1).** The battery is dispatched against hourly prices using either:
   - *price_rank* — charge in the cheapest hours, discharge in the most expensive ones; or
   - *lp* — a daily linear-programming optimization (scipy) respecting power, energy,
     round-trip efficiency and reserve limits.

5. **Optimizer sweep.** The same dispatch is repeated for battery durations of 1–6 hours
   to find the configuration with the best revenue per installed MWh.

6. **PPA agent.** Energy is split between a PPA (fixed price) and merchant sales using
   pro-rata, baseload or solar-only allocation, so you can compare contract structures.

7. **Capacity agent.** Identifies the 100 critical hours (highest-price proxy), computes
   the accredited capacity (average MW delivered in those hours) and the resulting
   capacity-market revenue.

8. **Reports.** One click generates a full report bundle: Excel workbook (8760 dispatch,
   monthly, scenarios, PPA, capacity), plus Word, PDF and PowerPoint summaries with charts.

9. **Behind-the-Meter (Module 2).** A separate track for industrial customers under CFE
   basic-supply tariffs (GDMTH): reconstructs the monthly CFE bill from 15-minute load data
   (energy by period, capacity and distribution demand charges), optimizes BESS / PV+BESS
   dispatch for peak shaving and time-of-use shifting in no-export mode, and produces a
   small-industry investor case with financing, conservative haircuts, red flags and a
   GO / REVISE / NO-GO recommendation.
        """
    )

    st.subheader("Where the data lives")
    st.markdown(
        f"""
| Input | Default file | Status |
|---|---|---|
| Node catalog (enriched) | `{DEFAULT_CATALOG}` | {"found" if DEFAULT_CATALOG.exists() else "missing"} |
| PML node source | `{DEFAULT_NODE_SOURCE}` | {"found" if DEFAULT_NODE_SOURCE.exists() else "missing"} |
| Sample PML 8760 | `{SAMPLE_PML}` | {"found" if SAMPLE_PML.exists() else "missing"} |
| Sample PV 8760 | `{SAMPLE_PV}` | {"found" if SAMPLE_PV.exists() else "missing"} |

Outputs are written to `outputs/` (excel, reports, maps). The same pipeline can be run
headless with `python scripts/run_pipeline.py` — see the README for the full command.
        """
    )

elif page == "0. Node Resolver":
    st.header("0. Node Resolver")
    st.write("Browse and resolve nodes from the CENACE catalog. The existing workspace catalog is used automatically; uploads and downloads are only needed if it is missing or outdated.")

    # --- Catalog: existing file first, upload only as override ---
    catalog_source: Path | None = DEFAULT_CATALOG if DEFAULT_CATALOG.exists() else None
    with st.expander("Catalog source", expanded=catalog_source is None):
        if catalog_source:
            st.success(f"Using existing catalog: {catalog_source}")
        else:
            st.warning("No catalog found in the workspace. Upload one below, or run: python scripts/download_official_catalog.py && python scripts/enrich_node_catalog.py --xlsx <workbook>")
        catalog_file = st.file_uploader("Replace catalog (CSV/XLSX)", type=["csv", "xlsx", "xls"])
        if catalog_file:
            catalog_source = DATA / "raw" / catalog_file.name
            catalog_source.write_bytes(catalog_file.getbuffer())
            st.info(f"Using uploaded catalog: {catalog_file.name}")

    if catalog_source is None:
        st.stop()

    mode = st.radio(
        "Node source",
        ["Pick from catalog", "Existing PML file in workspace", "Upload PML PDF / node list"],
        horizontal=True,
    )

    nodes = None
    if mode == "Pick from catalog":
        cat = load_catalog_cached(str(catalog_source), catalog_source.stat().st_mtime)
        label_cols = [c for c in ["nombre", "zona_carga", "entidad_federativa", "municipio"] if c in cat.columns]

        def node_label(row) -> str:
            extra = " — ".join(str(row[c]) for c in label_cols if str(row[c]).strip())
            return f"{row['clave_nodo_norm']}" + (f"  ({extra})" if extra else "")

        f1, f2, f3 = st.columns(3)
        zonas = sorted(z for z in cat.get("zona_carga", pd.Series(dtype=str)).unique() if str(z).strip()) if "zona_carga" in cat.columns else []
        estados = sorted(e for e in cat.get("entidad_federativa", pd.Series(dtype=str)).unique() if str(e).strip()) if "entidad_federativa" in cat.columns else []
        tipos = sorted(t for t in cat.get("tipo", pd.Series(dtype=str)).unique() if str(t).strip()) if "tipo" in cat.columns else []
        zona_sel = f1.selectbox("Zona de carga", ["(all)"] + zonas) if zonas else "(all)"
        estado_sel = f2.selectbox("Entidad federativa", ["(all)"] + estados) if estados else "(all)"
        tipo_sel = f3.selectbox("Tipo", ["(all)"] + tipos) if tipos else "(all)"

        filtered = cat
        if zona_sel != "(all)":
            filtered = filtered[filtered["zona_carga"] == zona_sel]
        if estado_sel != "(all)":
            filtered = filtered[filtered["entidad_federativa"] == estado_sel]
        if tipo_sel != "(all)":
            filtered = filtered[filtered["tipo"] == tipo_sel]

        options = {node_label(row): row["clave_nodo_norm"] for _, row in filtered.iterrows()}
        selected = st.multiselect(f"Node(s) — {len(options)} available", list(options.keys()))
        if selected:
            keys = [options[s] for s in selected]
            nodes = pd.DataFrame({"clave_nodo_original": keys, "clave_nodo_norm": keys})
    elif mode == "Existing PML file in workspace":
        if DEFAULT_NODE_SOURCE.exists():
            st.info(f"Using existing node source: {DEFAULT_NODE_SOURCE}")
            nodes = extract_nodes_from_table(DEFAULT_NODE_SOURCE)
        else:
            st.warning(f"{DEFAULT_NODE_SOURCE} not found.")
    else:
        pml_file = st.file_uploader("Upload PML PDF or CSV/XLSX with node codes", type=["pdf", "csv", "xlsx", "xls"])
        if pml_file:
            node_source = DATA / "raw" / pml_file.name
            node_source.write_bytes(pml_file.getbuffer())
            nodes = extract_nodes_from_pml_pdf(node_source) if node_source.suffix.lower() == ".pdf" else extract_nodes_from_table(node_source)

    if nodes is not None and len(nodes):
        st.success(f"{len(nodes)} node key(s) selected.")
        nodes_path = OUT / "excel" / "nodes_extracted.csv"
        nodes.to_csv(nodes_path, index=False, encoding="utf-8-sig")

        resolved = resolve_nodes(nodes, catalog_source)
        m1, m2 = st.columns(2)
        m1.metric("Matched nodes", int((resolved["resolution_status"] == "matched").sum()))
        m2.metric("Unmatched nodes", int((resolved["resolution_status"] == "unmatched").sum()))
        show_cols = [c for c in ["clave_nodo_norm", "nombre", "sistema", "zona_de_carga", "zona_carga", "voltaje_kv", "tipo", "gerencia_regional", "entidad_federativa", "municipio", "resolution_status"] if c in resolved.columns]
        st.dataframe(resolved[show_cols] if show_cols else resolved, use_container_width=True)

        out_xlsx = OUT / "excel" / "node_resolution_report.xlsx"
        write_resolution_workbook(resolved, out_xlsx)
        d1, d2 = st.columns(2)
        d1.download_button("Download node resolution workbook", out_xlsx.read_bytes(), file_name="node_resolution_report.xlsx")
        d2.download_button("Download extracted nodes CSV", nodes_path.read_bytes(), file_name="nodes_extracted.csv")

        if {"lat", "lon"}.issubset(set(map(str.lower, resolved.columns))):
            map_path = OUT / "maps" / "node_map.html"
            try:
                build_node_map(out_xlsx, map_path)
                st.download_button("Download node map HTML", map_path.read_bytes(), file_name="node_map.html")
            except Exception as exc:
                st.warning(f"Map not created: {exc}")

elif page == "1. Front-of-Meter Dispatch + PPA + Capacity":
    st.header("1. Front-of-Meter Dispatch + PPA + Capacity")
    st.write("Upload clean PML and PV 8760 files, or start immediately with the sample data already in the workspace.")

    use_sample = st.checkbox(
        "Use sample data from workspace (no upload needed)",
        value=SAMPLE_PML.exists() and SAMPLE_PV.exists(),
        help=f"PML: {SAMPLE_PML} | PV: {SAMPLE_PV}. Synthetic full-year data for testing, generated by scripts/make_sample_data.py.",
    )
    pml_file = st.file_uploader("Upload clean PML CSV/XLSX", type=["csv", "xlsx", "xls"], key="pml")

    pv_source, pv_upload, pv_spec = render_pv_system_panel(
        "fom",
        upload_label="Upload PV 8760 CSV/XLSX",
        default_mw_ac=100.0,
        default_mwp=100.0,
        default_yield=1800.0,
        default_degradation=0.4,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    bess_mw = c1.number_input("BESS MW", value=50.0, min_value=0.0)
    bess_mwh = c2.number_input("BESS MWh", value=200.0, min_value=0.0)
    rte = c3.number_input("RTE", value=0.925, min_value=0.1, max_value=1.0)
    reserve = c4.number_input("Reserve fraction", value=0.10, min_value=0.0, max_value=0.9)
    engine = c5.selectbox("Dispatch engine", ["price_rank", "lp"], help="lp = daily linear optimization (requires scipy)")

    with st.expander("PPA / CFE mixed development (optional)"):
        use_ppa = st.checkbox("Apply PPA structure", value=False)
        p1, p2, p3, p4 = st.columns(4)
        ppa_price = p1.number_input("PPA price (MXN/MWh)", value=950.0, min_value=0.0)
        ppa_mode = p2.selectbox("Allocation", ["pro_rata", "baseload", "solar_only"])
        ppa_fraction = p3.number_input("PPA fraction", value=0.70, min_value=0.0, max_value=1.0)
        ppa_mw = p4.number_input("PPA block MW (baseload)", value=0.0, min_value=0.0)

    with st.expander("Capacity / 100 critical hours (optional)"):
        use_cap = st.checkbox("Compute capacity credit", value=False)
        k1, k2 = st.columns(2)
        cap_price = k1.number_input("Capacity price (MXN/MW-year)", value=1_450_000.0, min_value=0.0)
        n_crit = k2.number_input("Critical hours", value=100, min_value=1, max_value=8760)

    with st.expander("Project economics — CAPEX, OPEX and IRR", expanded=True):
        st.markdown("**CAPEX**")
        e1, e2, e3, e4 = st.columns(4)
        fom_pv_usd_kwp = e1.number_input("PV CAPEX (USD/kWp)", value=650.0, min_value=0.0, key="fom_pv_capex")
        fom_bess_usd_kwh = e2.number_input("BESS CAPEX (USD/kWh)", value=350.0, min_value=0.0, key="fom_bess_capex")
        fom_fx = e3.number_input("FX (MXN/USD)", value=18.5, min_value=1.0, key="fom_fx")
        fom_capex_pv_mwp = e4.number_input(
            "PV MWp for CAPEX (if no specs)", value=100.0, min_value=0.0, key="fom_pv_mwp",
            help="Used when PV comes from upload; otherwise taken from PV system specs.",
        )
        st.markdown("**OPEX, insurance and financing**")
        e5, e6, e7, e8 = st.columns(4)
        fom_opex_pct = e5.number_input("OPEX (% CAPEX/yr)", value=0.02, min_value=0.0, max_value=0.2, key="fom_opex")
        fom_ins_pct = e6.number_input("Insurance (% CAPEX/yr)", value=0.005, min_value=0.0, max_value=0.05, format="%.3f", key="fom_ins")
        fom_fin_type = e7.selectbox("Financing", ["bank_loan", "equipment_lease", "cash_purchase"], key="fom_fin")
        fom_tenor = e8.number_input("Financing tenor (years)", value=7, min_value=1, max_value=25, key="fom_tenor")
        e9, e10, e11, e12 = st.columns(4)
        fom_rate = e9.number_input("Interest rate", value=0.14, min_value=0.0, max_value=0.5, key="fom_rate")
        fom_life = e10.number_input("Project life (years)", value=20.0, min_value=1.0, max_value=40.0, key="fom_life")
        fom_discount = e11.number_input("Discount rate", value=0.10, min_value=0.0, max_value=0.5, key="fom_disc")
        fom_rev_haircut = e12.number_input("Revenue haircut", value=0.95, min_value=0.5, max_value=1.0, key="fom_haircut",
                                           help="Bankable fraction of modeled merchant/PPA/capacity revenue.")

    n1, n2 = st.columns([3, 1])
    project_name = n1.text_input("Project name", value="Optimus Mexico Project")
    investor_report = n2.checkbox("Investor report (PDF)", value=True, help="Polished executive-summary PDF with revenue and dispatch charts, aimed at investors.")

    pml_path: Path | None = None
    pv_path: Path | None = None
    if pml_file:
        pml_path = DATA / "raw" / pml_file.name
        pml_path.write_bytes(pml_file.getbuffer())
    elif use_sample and SAMPLE_PML.exists():
        pml_path = SAMPLE_PML
    if pv_source == "upload":
        if pv_upload:
            pv_path = DATA / "raw" / pv_upload.name
            pv_path.write_bytes(pv_upload.getbuffer())
        elif use_sample and SAMPLE_PV.exists():
            pv_path = SAMPLE_PV
    has_pv_input = pv_path is not None or (pv_source == "specs" and pv_spec is not None)

    if pml_path:
        pv_msg = f"PML source: {pml_path}"
        if pv_path:
            pv_msg += f" | PV file: {pv_path}"
        elif pv_spec:
            if pv_spec.mwp > 0 and pv_spec.yield_kwh_kwp_yr > 0:
                pv_msg += (
                    f" | PV synthesized: {pv_spec.mwp:,.1f} MWp, "
                    f"{pv_spec.yield_kwh_kwp_yr:,.0f} kWh/kWp/y, "
                    f"{annual_production_mwh(pv_spec):,.0f} MWh/yr (Y{pv_spec.operation_year})"
                )
            else:
                pv_msg += " | PV: none (BESS-only)"
        st.info(pv_msg)

    if pml_path and has_pv_input and st.button("Run analysis", type="primary"):
        with st.spinner("Running dispatch, scenarios and reports..."):
            pml = pd.read_excel(pml_path) if pml_path.suffix.lower() in [".xlsx", ".xls"] else pd.read_csv(pml_path)
            if pv_path:
                pv = load_pv_8760(pv_path)
            else:
                pml_dt = pd.DatetimeIndex(pd.to_datetime(
                    pml["datetime"] if "datetime" in pml.columns else pml.iloc[:, 0],
                    errors="coerce",
                ))
                pv = synthesize_pv_8760(pml_dt, pv_spec)
            merged = merge_pml_pv(pml, pv)

            dispatch = run_dispatch(merged, BESSConfig(mw=bess_mw, mwh=bess_mwh, rte=rte, reserve_fraction=reserve), engine=engine)
            scenarios = sweep_bess_durations(merged, bess_mw, [2, 4, 6, 8], engine=engine)

            ppa_table = None
            if use_ppa:
                dispatch = apply_ppa(dispatch, PPAConfig(ppa_price_mxn_mwh=ppa_price, mode=ppa_mode, ppa_fraction=ppa_fraction, ppa_mw=ppa_mw))
                ppa_table = ppa_summary(dispatch)

            capacity_table = None
            if use_cap:
                cap = compute_capacity_credit(dispatch, CapacityConfig(capacity_price_mxn_mw_year=cap_price, n_critical_hours=int(n_crit)))
                capacity_table = capacity_summary_frame(cap)

            report = ReportData(
                project_name=project_name,
                dispatch=dispatch,
                scenario_summary=scenarios,
                ppa_summary=ppa_table,
                capacity_summary=capacity_table,
                assumptions={
                    "BESS MW": bess_mw, "BESS MWh": bess_mwh, "RTE": rte,
                    "Reserve fraction": reserve, "Dispatch engine": engine,
                    **({
                        "PV MW AC": pv_spec.mw_ac, "PV MWp": pv_spec.mwp,
                        "PV yield (kWh/kWp/y)": pv_spec.yield_kwh_kwp_yr,
                        "PV degradation (%/y)": pv_spec.degradation_pct_yr,
                        "PV annual MWh (synthesized)": round(annual_production_mwh(pv_spec)),
                    } if pv_spec else {}),
                },
            )
            written = write_all_reports(report, OUT / "reports", investor=investor_report)
            total_rev = sum(report.revenue_streams().values())
            pv_mwp_capex = pv_spec.mwp if pv_spec else fom_capex_pv_mwp
            capex_parts = compute_project_capex_mxn(
                pv_mwp_capex, bess_mwh, fom_pv_usd_kwp, fom_bess_usd_kwh, fom_fx,
            )
            fom_finance = FOMFinanceCase(
                capex_mxn=capex_parts["total_capex_mxn"],
                financing_type=fom_fin_type,
                tenor_years=int(fom_tenor),
                interest_rate=fom_rate,
                opex_mxn_per_year=fom_opex_pct * capex_parts["total_capex_mxn"],
                insurance_mxn_per_year=fom_ins_pct * capex_parts["total_capex_mxn"],
                bess_life_years=fom_life,
                discount_rate=fom_discount,
            )
            fom_econ = build_fom_investor_case(total_rev, fom_finance, revenue_haircut=fom_rev_haircut)
            st.session_state["analysis"] = {
                "dispatch": dispatch, "scenarios": scenarios, "ppa_table": ppa_table,
                "capacity_table": capacity_table, "report": report,
                "written": {k: str(v) for k, v in written.items()},
                "fom_econ": fom_econ, "capex_parts": capex_parts,
            }

    results = st.session_state.get("analysis")
    if results:
        dispatch = results["dispatch"]
        report: ReportData = results["report"]

        # ---- headline metrics ----
        st.subheader("Results")
        streams = report.revenue_streams()
        total_rev = sum(streams.values())
        delivered = dispatch["pv_to_grid_mwh"].sum() + dispatch["bess_discharge_mwh"].sum()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total revenue (MXN)", f"{total_rev:,.0f}")
        m2.metric("Energy delivered (MWh)", f"{delivered:,.0f}")
        m3.metric("Capture price (MXN/MWh)", f"{(total_rev / delivered if delivered else 0):,.0f}")
        m4.metric("Avg PML (MXN/MWh)", f"{dispatch['pml'].mean():,.0f}")

        fom_econ = results.get("fom_econ")
        if fom_econ:
            st.subheader("Project economics")
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("Total CAPEX (MXN)", f"{fom_econ['capex_mxn']:,.0f}")
            c2.metric("OPEX (MXN/yr)", f"{fom_econ['annual_opex_mxn']:,.0f}")
            c3.metric("Insurance (MXN/yr)", f"{fom_econ['annual_insurance_mxn']:,.0f}")
            irr_u = fom_econ.get("project_irr_unlevered_pct")
            irr_l = fom_econ.get("project_irr_levered_pct")
            c4.metric("Project IRR — unlevered", f"{irr_u:.1f}%" if irr_u is not None else "n/a")
            c5.metric("Project IRR — levered", f"{irr_l:.1f}%" if irr_l is not None else "n/a")
            c6.metric(f"NPV ({fom_econ['project_life_years']:.0f}y, MXN)", f"{fom_econ['npv_mxn']:,.0f}")
            st.dataframe(fom_economics_frame(fom_econ), use_container_width=True, hide_index=True)

        tab_dispatch, tab_revenue, tab_tables = st.tabs(["Dispatch charts", "Revenue charts", "Tables"])

        with tab_dispatch:
            import altair as alt

            st.markdown("**Average-day dispatch profile** — mean hourly energy flows with the average PML overlaid.")
            avg = report.average_day()
            flow_cols = [c for c in ["pv_to_grid_mwh", "pv_to_bess_mwh", "bess_discharge_mwh"] if c in avg.columns]
            flow_names = {"pv_to_grid_mwh": "PV to grid", "pv_to_bess_mwh": "PV to BESS", "bess_discharge_mwh": "BESS discharge"}
            long = avg.melt(id_vars=["hour"], value_vars=flow_cols, var_name="flow", value_name="mwh")
            long["flow"] = long["flow"].map(flow_names)
            bars = alt.Chart(long).mark_bar().encode(
                x=alt.X("hour:O", title="Hour of day"),
                y=alt.Y("mwh:Q", title="Average MWh"),
                color=alt.Color("flow:N", title="", scale=alt.Scale(
                    domain=["PV to grid", "PV to BESS", "BESS discharge"],
                    range=["#f5b942", "#7fb3d5", "#27ae60"])),
                tooltip=["hour", "flow", alt.Tooltip("mwh:Q", format=",.2f")],
            )
            pml_line = alt.Chart(avg).mark_line(color="#d62728", point=True).encode(
                x=alt.X("hour:O"), y=alt.Y("pml:Q", title="Avg PML (MXN/MWh)"),
                tooltip=[alt.Tooltip("pml:Q", format=",.0f")],
            )
            st.altair_chart(alt.layer(bars, pml_line).resolve_scale(y="independent").properties(height=380), use_container_width=True)

            st.markdown("**Single-day detail** — pick a date to see hourly flows, state of charge and prices.")
            d = dispatch.copy()
            d["date"] = pd.to_datetime(d["datetime"]).dt.date
            dates = sorted(d["date"].unique())
            sel_date = st.selectbox("Day", dates, index=min(len(dates) - 1, 181))
            day = d[d["date"] == sel_date].copy()
            day["hour"] = pd.to_datetime(day["datetime"]).dt.hour
            day_long = day.melt(id_vars=["hour"], value_vars=flow_cols, var_name="flow", value_name="mwh")
            day_long["flow"] = day_long["flow"].map(flow_names)
            day_bars = alt.Chart(day_long).mark_bar().encode(
                x=alt.X("hour:O", title="Hour"), y=alt.Y("mwh:Q", title="MWh"),
                color=alt.Color("flow:N", title="", scale=alt.Scale(
                    domain=["PV to grid", "PV to BESS", "BESS discharge"],
                    range=["#f5b942", "#7fb3d5", "#27ae60"])),
                tooltip=["hour", "flow", alt.Tooltip("mwh:Q", format=",.2f")],
            )
            layers = [day_bars]
            if "soc_mwh" in day.columns:
                layers.append(alt.Chart(day).mark_line(color="#8e44ad", strokeDash=[4, 3]).encode(
                    x=alt.X("hour:O"), y=alt.Y("soc_mwh:Q", title="SOC (MWh)"),
                    tooltip=[alt.Tooltip("soc_mwh:Q", format=",.1f")]))
            layers.append(alt.Chart(day).mark_line(color="#d62728").encode(
                x=alt.X("hour:O"), y=alt.Y("pml:Q", title="PML (MXN/MWh)"),
                tooltip=[alt.Tooltip("pml:Q", format=",.0f")]))
            st.altair_chart(alt.layer(*layers).resolve_scale(y="independent").properties(height=380), use_container_width=True)

        with tab_revenue:
            import altair as alt

            st.markdown("**Annual revenue by stream**")
            stream_df = pd.DataFrame({"stream": list(streams.keys()), "mxn": list(streams.values())})
            stream_df["mxn_m"] = stream_df["mxn"] / 1e6
            rev_bar = alt.Chart(stream_df).mark_bar().encode(
                x=alt.X("stream:N", title="", sort="-y"),
                y=alt.Y("mxn_m:Q", title="MXN millions"),
                color=alt.Color("stream:N", legend=None, scale=alt.Scale(
                    domain=["PPA", "Merchant", "Capacity"],
                    range=["#1f77b4", "#f5b942", "#27ae60"])),
                tooltip=["stream", alt.Tooltip("mxn:Q", format=",.0f", title="MXN")],
            ).properties(height=320)
            st.altair_chart(rev_bar, use_container_width=True)

            st.markdown("**Monthly revenue** — stacked by stream, with average PML overlaid.")
            mrev = dispatch.copy()
            mrev["month"] = pd.to_datetime(mrev["datetime"]).dt.month
            rev_cols = [c for c in ["ppa_revenue", "merchant_revenue"] if c in mrev.columns]
            rev_names = {"ppa_revenue": "PPA", "merchant_revenue": "Merchant"}
            monthly_rev = mrev.groupby("month", as_index=False).agg({**{c: "sum" for c in rev_cols}, "pml": "mean"})
            mlong = monthly_rev.melt(id_vars=["month"], value_vars=rev_cols, var_name="stream", value_name="mxn")
            mlong["stream"] = mlong["stream"].map(rev_names)
            mlong["mxn_m"] = mlong["mxn"] / 1e6
            month_bars = alt.Chart(mlong).mark_bar().encode(
                x=alt.X("month:O", title="Month"),
                y=alt.Y("mxn_m:Q", title="Revenue (MXN millions)"),
                color=alt.Color("stream:N", title="", scale=alt.Scale(
                    domain=["PPA", "Merchant"], range=["#1f77b4", "#f5b942"])),
                tooltip=["month", "stream", alt.Tooltip("mxn:Q", format=",.0f", title="MXN")],
            )
            month_pml = alt.Chart(monthly_rev).mark_line(color="#d62728", point=True).encode(
                x=alt.X("month:O"), y=alt.Y("pml:Q", title="Avg PML (MXN/MWh)"),
                tooltip=[alt.Tooltip("pml:Q", format=",.0f")],
            )
            st.altair_chart(alt.layer(month_bars, month_pml).resolve_scale(y="independent").properties(height=380), use_container_width=True)

        with tab_tables:
            st.subheader("Scenario comparison")
            st.dataframe(results["scenarios"], use_container_width=True)
            if results["ppa_table"] is not None:
                st.subheader("PPA vs merchant")
                st.dataframe(results["ppa_table"], use_container_width=True)
            if results["capacity_table"] is not None:
                st.subheader("Capacity credit (critical hours)")
                st.dataframe(results["capacity_table"], use_container_width=True)
            st.subheader("Dispatch sample (first 100 hours)")
            st.dataframe(dispatch.head(100), use_container_width=True)

        st.subheader("Downloads")
        labels = {"xlsx": "Excel workbook", "docx": "Word report", "pdf": "PDF report", "pptx": "PowerPoint", "investor_pdf": "Investor report (PDF)"}
        cols = st.columns(len(results["written"]))
        for col, (fmt, path) in zip(cols, results["written"].items()):
            p = Path(path)
            if p.exists():
                col.download_button(labels.get(fmt, fmt.upper()), p.read_bytes(), file_name=p.name, key=f"dl_{fmt}")

else:
    from agents.btm_tariff_agent import (
        TariffRates, build_tariff_calendar, bill_totals,
        gdmto_migration_check, load_starter_rates, reconstruct_annual_bill,
    )
    from agents.btm_dispatch_agent import BTMBessConfig, dispatch_btm, dispatch_summary
    from agents.btm_lp_optimizer_agent import dispatch_btm_lp, optimize_btm_lp_dispatch
    from agents.btm_revenue_guard import (
        ALLOWED_BTM_VALUE_STREAMS, PROJECT_MODE_BTM, enforce_btm_revenue_scope, savings_by_btm_stream,
    )
    from agents.btm_investment_readiness_agent import (
        STATUS_DEMO, STATUS_INVESTMENT_READY, assess_meter_quality,
        bill_reconstruction_summary, evaluate_investment_readiness, status_badge_color,
    )
    from agents.btm_investor_agent import (
        FinanceCase, RiskAssumptions, build_small_industry_investor_case,
        detect_small_industry_red_flags, investor_dashboard_frame,
        investor_recommendation, write_investor_package,
    )

    st.session_state.setdefault("project_mode", PROJECT_MODE_BTM)
    guard = enforce_btm_revenue_scope(PROJECT_MODE_BTM, ALLOWED_BTM_VALUE_STREAMS)
    st.header("2. Behind-the-Meter (CFE)")
    st.caption("BTM-only mode: CFE bill savings and PV self-consumption — merchant/PML/PPA/CENACE capacity revenues are excluded.")
    if not guard["is_valid"]:
        st.warning(guard["message"])

    btm_sub = st.radio(
        "BTM workflow",
        ["A. Screening Case", "B. Bankable Investor Case", "C. Customer Proposal Case", "D. Technical Dispatch Detail"],
        horizontal=True,
        label_visibility="collapsed",
    )

    BTM_LOAD = Path("data/sample/btm_load_15min_sample.csv")
    BTM_PV = Path("data/sample/btm_pv_15min_sample.csv")

    use_btm_sample = st.checkbox(
        "Use sample data from workspace (DEMO mode — no investor recommendation)",
        value=BTM_LOAD.exists(),
        help=f"Load: {BTM_LOAD} | PV: {BTM_PV}. Synthetic data for education and screening only.",
    )
    u1, u2 = st.columns(2)
    load_file = u1.file_uploader("15-minute load CSV (timestamp, load_kw)", type=["csv"], key="btm_load")
    bill_file = u2.file_uploader("CFE monthly bills CSV (month, total) — for reconciliation", type=["csv"], key="btm_bills")

    t1, t2, t3, t4 = st.columns(4)
    tariff = t1.selectbox("Tariff", ["GDMTH"])
    division = t2.text_input("CFE division", value="Valle de Mexico Sur")
    mode = t3.selectbox("Mode", ["pv_bess", "bess_only"], format_func=lambda m: "PV + BESS (no export)" if m == "pv_bess" else "BESS only")
    tariff_confirmed = t4.checkbox("Tariff & division confirmed", value=not use_btm_sample)

    btm_pv_source = "specs"
    btm_pv_spec: PVSystemSpec | None = None
    btm_pv_upload = None
    if mode == "pv_bess":
        btm_pv_source, btm_pv_upload, btm_pv_spec = render_pv_system_panel(
            "btm",
            upload_label="15-minute PV CSV (timestamp, pv_kw)",
            upload_types=["csv"],
            default_mw_ac=0.5,
            default_mwp=0.5,
            default_yield=1800.0,
            default_degradation=0.4,
        )

    default_engine = {
        "A. Screening Case": "Rule-based fast screening",
        "B. Bankable Investor Case": "LP bankable optimization",
        "C. Customer Proposal Case": "LP bankable optimization",
        "D. Technical Dispatch Detail": "Compare both engines",
    }[btm_sub]
    engine_options = ["Rule-based fast screening", "LP bankable optimization", "Compare both engines"]
    dispatch_engine = st.selectbox(
        "Dispatch engine",
        engine_options,
        index=engine_options.index(default_engine),
        help="LP is the bankable engine (Fix 2). Rule-based is for fast screening. Compare shows both.",
    )

    b1, b2, b3, b4, b5 = st.columns(5)
    bess_kw = b1.number_input("BESS power (kW)", value=300.0, min_value=0.0)
    bess_kwh = b2.number_input("BESS energy (kWh)", value=600.0, min_value=0.0)
    btm_rte = b3.number_input("Round-trip efficiency", value=0.90, min_value=0.5, max_value=1.0)
    soc_min = b4.number_input("Min SOC fraction", value=0.10, min_value=0.0, max_value=0.5)
    grid_charge = b5.checkbox("Allow grid charging (base hours)", value=False,
                             help="Default off for bankable investor case per Fix 2 spec.")

    with st.expander("Financing and investor assumptions", expanded=btm_sub.startswith("B.")):
        st.markdown("**CAPEX and financing**")
        f1, f2, f3, f4 = st.columns(4)
        capex_usd_kwh = f1.number_input("BESS CAPEX (USD/kWh)", value=350.0, min_value=0.0)
        fx = f2.number_input("FX (MXN/USD)", value=18.5, min_value=1.0)
        fin_type = f3.selectbox("Financing", ["bank_loan", "equipment_lease", "cash_purchase"])
        tenor = f4.number_input("Tenor (years)", value=7, min_value=1, max_value=20)
        pv_usd_kwp = 0.0
        if mode == "pv_bess":
            pv_usd_kwp = st.number_input("PV CAPEX (USD/kWp)", value=650.0, min_value=0.0, key="btm_pv_capex")

        st.markdown("**OPEX and insurance**")
        f5, f6, f7, f8 = st.columns(4)
        rate = f5.number_input("Interest rate", value=0.14, min_value=0.0, max_value=0.5)
        opex_pct = f6.number_input("OPEX (% CAPEX/yr)", value=0.02, min_value=0.0, max_value=0.2,
                                   help="Annual operation & maintenance as a fraction of CAPEX (augmentation, monitoring, preventive maintenance).")
        insurance_pct = f7.number_input("Insurance (% CAPEX/yr)", value=0.005, min_value=0.0, max_value=0.05, format="%.3f")
        warranty = f8.number_input("Warranty (years)", value=10.0, min_value=1.0)

        st.markdown("**Life and investor thresholds**")
        f9, f10, f11 = st.columns(3)
        bess_life = f9.number_input("BESS life (years)", value=20.0, min_value=1.0, max_value=30.0,
                                    help="Useful life of the battery; the analysis horizon for NPV, IRR and lifetime benefit.")
        discount_rate = f10.number_input("Discount rate", value=0.10, min_value=0.0, max_value=0.5)
        max_payback = f11.number_input("Max acceptable payback (years)", value=7.0, min_value=1.0)

        _pv_mwp = btm_pv_spec.mwp if btm_pv_spec and mode == "pv_bess" else 0.0
        _capex_parts = compute_project_capex_mxn(_pv_mwp, bess_kwh / 1000.0, pv_usd_kwp, capex_usd_kwh, fx)
        _capex_preview = _capex_parts["total_capex_mxn"]
        st.caption(
            f"CAPEX: MXN {_capex_preview:,.0f} "
            f"(PV {_capex_parts['pv_capex_mxn']:,.0f} + BESS {_capex_parts['bess_capex_mxn']:,.0f}) | "
            f"OPEX: MXN {opex_pct * _capex_preview:,.0f}/yr | "
            f"Insurance: MXN {insurance_pct * _capex_preview:,.0f}/yr | Horizon: {bess_life:.0f} years"
        )

    load_path: Path | None = None
    pv_path: Path | None = None
    if load_file:
        load_path = DATA / "raw" / load_file.name
        load_path.write_bytes(load_file.getbuffer())
    elif use_btm_sample and BTM_LOAD.exists():
        load_path = BTM_LOAD
    if mode == "pv_bess" and btm_pv_source == "upload":
        if btm_pv_upload:
            pv_path = DATA / "raw" / btm_pv_upload.name
            pv_path.write_bytes(btm_pv_upload.getbuffer())
        elif use_btm_sample and BTM_PV.exists():
            pv_path = BTM_PV

    if load_path:
        src = f"Load source: {load_path}"
        if mode == "pv_bess":
            if pv_path:
                src += f" | PV file: {pv_path}"
            elif btm_pv_spec:
                if btm_pv_spec.mwp > 0 and btm_pv_spec.yield_kwh_kwp_yr > 0:
                    src += (
                        f" | PV synthesized: {btm_pv_spec.mwp:,.2f} MWp, "
                        f"{annual_production_mwh(btm_pv_spec):,.0f} MWh/yr"
                    )
                else:
                    src += " | PV: none (BESS-only dispatch)"
        st.info(src)

    if load_path and st.button("Run behind-the-meter analysis", type="primary"):
        with st.spinner("Reconstructing bill, optimizing dispatch and assessing investment readiness..."):
            load_df = pd.read_csv(load_path)
            load_df["timestamp"] = pd.to_datetime(load_df["timestamp"])
            uses_synthetic = use_btm_sample and load_file is None
            meter_quality = assess_meter_quality(load_df)

            start = load_df["timestamp"].min().normalize()
            end = load_df["timestamp"].max().normalize() + pd.Timedelta(days=1)
            calendar = build_tariff_calendar(start, end, tariff=tariff, division=division)
            rates = TariffRates.from_table(load_starter_rates(), tariff=tariff, division=division)
            baseline_bills = reconstruct_annual_bill(load_df, calendar, rates)

            actual_bills: pd.DataFrame | None = None
            if bill_file:
                actual_bills = pd.read_csv(bill_file)
                if "total" not in actual_bills.columns and "total_mxn" in actual_bills.columns:
                    actual_bills = actual_bills.rename(columns={"total_mxn": "total"})
            bill_recon = bill_reconstruction_summary(baseline_bills, actual_bills)

            project_config = {
                "uses_synthetic_data": uses_synthetic,
                "tariff_confirmed": tariff_confirmed,
                "mode": mode,
                "min_bill_months": 12,
                "min_load_months": 12,
                "max_missing_interval_pct": 1.0,
                "max_bill_reconstruction_error_pct": 3.0,
            }
            customer_files = {
                "cfe_bill_months": len(actual_bills) if actual_bills is not None else 0,
                "has_pv_profile": mode != "pv_bess" or pv_path is not None or btm_pv_spec is not None,
            }
            readiness = evaluate_investment_readiness(
                customer_files, meter_quality, bill_recon, project_config,
            )

            merged = load_df.merge(calendar, on="timestamp", how="inner")
            pv_df = None
            if mode == "pv_bess":
                if btm_pv_spec is not None:
                    pv_df = synthesize_pv_15min(
                        pd.DatetimeIndex(pd.to_datetime(load_df["timestamp"], errors="coerce")),
                        btm_pv_spec,
                    )
                elif pv_path:
                    pv_df = pd.read_csv(pv_path)
                    pv_df["timestamp"] = pd.to_datetime(pv_df["timestamp"])
                merged = merged.merge(pv_df, on="timestamp", how="left") if pv_df is not None else merged

            bess_cfg = BTMBessConfig(
                power_kw=bess_kw, energy_kwh=bess_kwh, rte=btm_rte,
                soc_min_pct=soc_min, allow_grid_charging=grid_charge,
            )

            engines_run: dict = {}
            if dispatch_engine in ("Rule-based fast screening", "Compare both engines"):
                rule_disp = dispatch_btm(merged, bess_cfg, mode=mode)
                rule_opt = reconstruct_annual_bill(
                    rule_disp[["timestamp", "grid_import_kw"]], calendar, rates, load_col="grid_import_kw",
                )
                engines_run["rule_based"] = {
                    "dispatched": rule_disp, "opt_bills": rule_opt,
                    "summary": dispatch_summary(rule_disp), "solver_status": "n/a",
                    "fallback_used": False, "label": "Rule-based fast screening",
                }

            if dispatch_engine in ("LP bankable optimization", "Compare both engines"):
                lp_result = optimize_btm_lp_dispatch(
                    load_df, pv_df, calendar, rates, bess_cfg, mode=mode, baseline_bills=baseline_bills,
                )
                engines_run["lp"] = {
                    "dispatched": lp_result.dispatch_15min,
                    "opt_bills": lp_result.optimized_bill,
                    "summary": dispatch_summary(lp_result.dispatch_15min),
                    "solver_status": lp_result.solver_status,
                    "fallback_used": lp_result.fallback_used,
                    "warnings": lp_result.warnings,
                    "label": "LP bankable optimization",
                }

            if dispatch_engine == "Compare both engines":
                primary_key = "lp" if "lp" in engines_run and not engines_run["lp"]["fallback_used"] else "rule_based"
            elif dispatch_engine == "LP bankable optimization":
                primary_key = "lp" if "lp" in engines_run else "rule_based"
            else:
                primary_key = "rule_based"

            dispatched = engines_run[primary_key]["dispatched"]
            opt_bills = engines_run[primary_key]["opt_bills"]
            engine_label = engines_run[primary_key]["label"]
            solver_status = engines_run[primary_key]["solver_status"]

            capex_parts = compute_project_capex_mxn(
                btm_pv_spec.mwp if btm_pv_spec and mode == "pv_bess" else 0.0,
                bess_kwh / 1000.0, pv_usd_kwp, capex_usd_kwh, fx,
            )
            capex_mxn = capex_parts["total_capex_mxn"]
            finance = FinanceCase(
                capex_mxn=capex_mxn, financing_type=fin_type, tenor_years=int(tenor),
                interest_rate=rate, opex_mxn_per_year=opex_pct * capex_mxn,
                insurance_mxn_per_year=insurance_pct * capex_mxn,
                warranty_years=warranty, bess_life_years=bess_life,
                discount_rate=discount_rate,
            )
            risk = RiskAssumptions(required_max_payback_years=max_payback)
            case = build_small_industry_investor_case(baseline_bills, opt_bills, finance, risk)
            flags = detect_small_industry_red_flags(dispatched, baseline_bills, opt_bills, case, finance, risk)

            btm_streams = savings_by_btm_stream(baseline_bills, opt_bills)
            summary = engines_run[primary_key]["summary"]
            if summary.get("pv_self_consumed_mwh", 0) > 0:
                kwh_cols = [c for c in baseline_bills.columns if c.startswith("kwh_")]
                total_kwh = float(baseline_bills[kwh_cols].sum().sum()) if kwh_cols else 1.0
                avg_rate = float(baseline_bills["energy_charge"].sum() / max(total_kwh, 1))
                btm_streams["pv_self_consumption_value"] = summary["pv_self_consumed_mwh"] * 1000 * avg_rate

            rec = investor_recommendation(
                case, finance, risk, red_flags=flags,
                readiness_status=readiness["status"],
            )
            watermark = ""
            if readiness["status"] != STATUS_INVESTMENT_READY:
                watermark = f"{readiness['status']} — NOT FOR INVESTOR USE"

            case["readiness_status"] = readiness["status"]
            case["dispatch_engine"] = engine_label
            case["solver_status"] = solver_status
            case["btm_value_streams"] = btm_streams
            case["report_watermark"] = watermark
            case["confidence_score"] = readiness["confidence_score"]

            pkg_path = OUT / "reports" / "BTM_Investor_Package.xlsx"
            write_investor_package(
                pkg_path, case, rec, baseline_bills, opt_bills, flags,
                assumptions={
                    "Case status": readiness["status"],
                    "Dispatch engine": engine_label,
                    "Solver status": solver_status,
                    "Tariff": tariff, "Division": division, "Mode": mode,
                    "BESS kW": bess_kw, "BESS kWh": bess_kwh, "RTE": btm_rte,
                    "Grid charging": grid_charge, "CAPEX (MXN)": round(capex_mxn),
                    "OPEX (MXN/yr)": round(opex_pct * capex_mxn),
                    "Insurance (MXN/yr)": round(insurance_pct * capex_mxn),
                    "Financing": fin_type, "Tenor (years)": tenor, "Rate": rate,
                    "BESS life (years)": bess_life, "Discount rate": discount_rate,
                    "Warranty (years)": warranty,
                    "Synthetic data": uses_synthetic,
                    **({
                        "PV MW AC": btm_pv_spec.mw_ac, "PV MWp": btm_pv_spec.mwp,
                        "PV yield (kWh/kWp/y)": btm_pv_spec.yield_kwh_kwp_yr,
                        "PV degradation (%/y)": btm_pv_spec.degradation_pct_yr,
                        "PV annual MWh (synthesized)": round(annual_production_mwh(btm_pv_spec)),
                    } if btm_pv_spec else {}),
                },
            )
            st.session_state["btm"] = {
                "baseline_bills": baseline_bills, "opt_bills": opt_bills,
                "dispatched": dispatched, "case": case, "flags": flags, "rec": rec,
                "summary": summary, "pkg": str(pkg_path),
                "readiness": readiness, "bill_recon": bill_recon,
                "meter_quality": meter_quality, "engines_run": engines_run,
                "primary_engine": primary_key, "btm_sub": btm_sub,
            }
            st.session_state["btm_submodule"] = btm_sub

    btm = st.session_state.get("btm")
    if btm:
        import altair as alt

        readiness = btm.get("readiness")
        if not readiness:
            readiness = {
                "status": STATUS_DEMO, "blocking_issues": ["Re-run analysis for readiness assessment"],
                "warnings": [],
            }
        case, rec = btm["case"], btm["rec"]
        status = readiness["status"]
        badge_color = status_badge_color(status)
        st.markdown(
            f'<span style="background:{badge_color};color:white;padding:6px 14px;'
            f'border-radius:6px;font-weight:600;">Case status: {status}</span>',
            unsafe_allow_html=True,
        )
        if readiness["blocking_issues"]:
            st.warning("**Data gaps:** " + " | ".join(readiness["blocking_issues"]))
        if readiness["warnings"]:
            st.info("**Warnings:** " + " | ".join(readiness["warnings"]))

        if status == STATUS_INVESTMENT_READY:
            if rec.startswith("GO"):
                st.success(f"**{rec}**")
            elif rec.startswith("REVISE"):
                st.warning(f"**{rec}**")
            else:
                st.error(f"**{rec}**")
        else:
            st.info(f"**Investor recommendation blocked** — {rec}")

        if case.get("report_watermark"):
            st.caption(case["report_watermark"])

        st.subheader("Project economics — CFE savings")
        ec1, ec2, ec3, ec4, ec5, ec6 = st.columns(6)
        ec1.metric("Total CAPEX (MXN)", f"{case.get('capex_mxn', 0):,.0f}")
        ec2.metric("OPEX (MXN/yr)", f"{case.get('annual_opex_mxn', 0):,.0f}")
        ec3.metric("Insurance (MXN/yr)", f"{case.get('annual_insurance_mxn', 0):,.0f}")
        sav_irr_u = case.get("savings_irr_unlevered_pct")
        sav_irr_l = case.get("savings_irr_levered_pct")
        ec4.metric("IRR on CFE savings — unlevered", f"{sav_irr_u:.1f}%" if sav_irr_u is not None else "n/a",
                   help="Modeled annual bill savings minus OPEX/insurance vs total CAPEX.")
        ec5.metric("IRR on CFE savings — levered", f"{sav_irr_l:.1f}%" if sav_irr_l is not None else "n/a",
                   help="Bankable savings net of financing, OPEX and insurance.")
        ec6.metric("Simple payback (years)", f"{case['simple_payback_years']:.1f}")

        show_investor = btm_sub.startswith("B.") or btm_sub.startswith("C.")
        show_technical = btm_sub.startswith("D.") or btm_sub == "A. Screening Case"

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Annual CFE bill before (MXN)", f"{case['baseline_annual_bill_mxn']:,.0f}")
        k2.metric("Annual CFE bill after (MXN)", f"{case['optimized_annual_bill_mxn']:,.0f}",
                  delta=f"-{case['modeled_annual_savings_mxn']:,.0f}", delta_color="inverse")
        k3.metric("Annual savings (MXN)", f"{case['modeled_annual_savings_mxn']:,.0f}")
        if show_investor:
            k4.metric("Net annual benefit — base (MXN)", f"{case['net_annual_benefit_base_mxn']:,.0f}",
                      help="Bankable savings minus financing payments, O&M and insurance.")
        else:
            k4.metric("Dispatch engine", case.get("dispatch_engine", "n/a"))

        if show_investor:
            irr_txt = f"{case['project_irr_pct']:.1f}%" if case.get("project_irr_pct") is not None else "n/a"
            k5, k6, k7, k8 = st.columns(4)
            k5.metric("Net annual benefit — base (MXN)", f"{case['net_annual_benefit_base_mxn']:,.0f}")
            k6.metric(f"NPV over {case['bess_life_years']:.0f}y BESS life (MXN)", f"{case['npv_base_mxn']:,.0f}")
            k7.metric("Net monthly benefit — base (MXN)", f"{case['net_monthly_benefit_base_mxn']:,.0f}")
            k8.metric("Bankable annual savings (MXN)", f"{case['bankable_annual_savings_mxn']:,.0f}")

        s = btm["summary"]
        k9, k10, k11, k12 = st.columns(4)
        k9.metric("Peak before (kW)", f"{s['peak_before_kw']:,.0f}")
        k10.metric("Peak after (kW)", f"{s['peak_after_kw']:,.0f}",
                  delta=f"-{s['peak_before_kw'] - s['peak_after_kw']:,.0f} kW", delta_color="inverse")
        k11.metric("PV self-consumed (MWh)", f"{s['pv_self_consumed_mwh']:,.0f}")
        k12.metric("Max grid export (kW)", f"{s['max_export_kw']:,.2f}")

        if case.get("btm_value_streams"):
            st.markdown("**BTM value streams (MXN/yr)**")
            st.dataframe(
                pd.DataFrame(list(case["btm_value_streams"].items()), columns=["stream", "mxn"]),
                use_container_width=True, hide_index=True,
            )

        tab_names = ["Bill before vs after", "Daily dispatch"]
        if show_investor:
            tab_names.append("Investor dashboard")
        if btm.get("bill_recon", {}).get("has_actual_bills"):
            tab_names.append("Bill reconciliation")
        if show_technical and len(btm.get("engines_run", {})) > 1:
            tab_names.append("Engine comparison")
        tabs = st.tabs(tab_names)
        tab_idx = 0

        with tabs[tab_idx]:
            tab_idx += 1
            base_b, opt_b = btm["baseline_bills"], btm["opt_bills"]
            comp = pd.concat([base_b.assign(case="Baseline"), opt_b.assign(case="Optimized")])
            comp_long = comp.melt(
                id_vars=["month", "case"],
                value_vars=["energy_charge", "capacity_charge", "distribution_charge", "fixed_charge"],
                var_name="component", value_name="mxn")
            comp_long["component"] = comp_long["component"].str.replace("_charge", "")
            chart = alt.Chart(comp_long).mark_bar().encode(
                x=alt.X("case:N", title="", axis=alt.Axis(labelAngle=0)),
                y=alt.Y("mxn:Q", title="MXN"),
                color=alt.Color("component:N", title=""),
                column=alt.Column("month:O", title="Month"),
                tooltip=["month", "case", "component", alt.Tooltip("mxn:Q", format=",.0f")],
            ).properties(height=300)
            st.altair_chart(chart)

            sav = pd.DataFrame({
                "component": ["energy", "capacity", "distribution"],
                "mxn": [
                    base_b["energy_charge"].sum() - opt_b["energy_charge"].sum(),
                    base_b["capacity_charge"].sum() - opt_b["capacity_charge"].sum(),
                    base_b["distribution_charge"].sum() - opt_b["distribution_charge"].sum(),
                ],
            })
            st.markdown("**Annual savings by tariff component**")
            st.altair_chart(alt.Chart(sav).mark_bar().encode(
                x=alt.X("component:N", title="", sort="-y"),
                y=alt.Y("mxn:Q", title="Savings (MXN)"),
                color=alt.Color("component:N", legend=None),
                tooltip=["component", alt.Tooltip("mxn:Q", format=",.0f")],
            ).properties(height=280), use_container_width=True)

        with tabs[tab_idx]:
            tab_idx += 1
            d = btm["dispatched"].copy()
            d["date"] = pd.to_datetime(d["timestamp"]).dt.date
            dates = sorted(d["date"].unique())
            sel = st.selectbox("Day", dates, index=min(len(dates) - 1, 195), key="btm_day")
            day = d[d["date"] == sel].copy()
            day["hour"] = pd.to_datetime(day["timestamp"]).dt.hour + pd.to_datetime(day["timestamp"]).dt.minute / 60
            line_load = alt.Chart(day).mark_line(color="#444444").encode(
                x=alt.X("hour:Q", title="Hour"), y=alt.Y("load_kw:Q", title="kW"))
            line_import = alt.Chart(day).mark_area(color="#1f77b4", opacity=0.35).encode(
                x="hour:Q", y=alt.Y("grid_import_kw:Q"))
            line_pv = alt.Chart(day).mark_area(color="#f5b942", opacity=0.4).encode(
                x="hour:Q", y=alt.Y("pv_to_load_kw:Q"))
            line_soc = alt.Chart(day).mark_line(color="#8e44ad", strokeDash=[4, 3]).encode(
                x="hour:Q", y=alt.Y("soc_kwh:Q", title="SOC (kWh)"))
            cap_rule = alt.Chart(day).mark_rule(color="#d62728", strokeDash=[6, 3]).encode(
                y=alt.Y("import_cap_kw:Q"))
            st.altair_chart(
                alt.layer(line_import, line_pv, line_load, cap_rule, line_soc).resolve_scale(y="independent").properties(height=400),
                use_container_width=True)
            st.caption(
                f"Engine: {case.get('dispatch_engine', 'n/a')} | "
                "Grey = gross load | blue = grid import | yellow = PV to load | red dashed = import cap | purple = SOC"
            )

        if show_investor:
            with tabs[tab_idx]:
                tab_idx += 1
                st.dataframe(investor_dashboard_frame(case, rec), use_container_width=True, hide_index=True)
                st.markdown("**Red flags**")
                if btm["flags"]:
                    st.dataframe(pd.DataFrame(btm["flags"]), use_container_width=True, hide_index=True)
                else:
                    st.success("No red flags detected.")
                haircuts = pd.DataFrame(list(case["haircuts"].items()), columns=["haircut", "value"])
                st.markdown("**Haircuts applied (investor case)**")
                st.dataframe(haircuts, use_container_width=True, hide_index=True)

        if btm.get("bill_recon", {}).get("has_actual_bills"):
            with tabs[tab_idx]:
                tab_idx += 1
                br = btm["bill_recon"]
                st.metric("Annual reconstruction error", f"{br['annual_error_pct']:.1f}%")
                st.metric("Months failed (>3% error)", br["months_failed"])
                if br.get("monthly_errors"):
                    st.dataframe(pd.DataFrame(br["monthly_errors"]), use_container_width=True, hide_index=True)

        if show_technical and len(btm.get("engines_run", {})) > 1:
            with tabs[tab_idx]:
                rows = []
                for eng in btm["engines_run"].values():
                    base_t = btm["baseline_bills"]["total"].sum()
                    opt_t = eng["opt_bills"]["total"].sum()
                    rows.append({
                        "engine": eng["label"],
                        "annual_savings_mxn": base_t - opt_t,
                        "peak_after_kw": eng["summary"]["peak_after_kw"],
                        "solver_status": eng["solver_status"],
                        "fallback": eng.get("fallback_used", False),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        pkg = Path(btm["pkg"])
        if pkg.exists():
            dl_label = (
                "Download investor package (Excel)"
                if status == STATUS_INVESTMENT_READY
                else "Download screening report (Excel — watermarked)"
            )
            st.download_button(dl_label, pkg.read_bytes(), file_name=pkg.name, key="dl_btm_pkg")
