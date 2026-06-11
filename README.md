# Optimus Mexico

PV+BESS optimization program for Mexico. This starter codebase is organized as agents/modules so we can add the full dispatch, PPA, capacity and printout logic step by step.

## Current build status

Included now:

1. **Node Resolver Agent**
   - Extracts `Clave del nodo` values from CENACE PML PDFs or CSV/XLSX files.
   - Normalizes codes like `01ACO230` to `01ACO-230`.
   - Resolves node codes against a downloaded CENACE `Catálogo de NodosP`.
   - Creates a resolution workbook.
   - Creates an HTML map if lat/lon coordinates are available.

2. **PML Scraper Agent**
   - Downloads PML data for one private node key.
   - Uses `CENACE_NODE_ID` environment variable or `--node-id`.
   - Anonymizes filenames by default.

3. **PV Loader Agent**
   - Loads PV 8760 data from CSV/XLSX.
   - **PV synthesis** (`agents/pv_synthesis_agent.py`): enter MW AC, MWp, yield
     (kWh/kWp/year) and degradation (%/year) to generate an hourly or 15-minute
     production profile scaled to the computed annual MWh (with AC clipping).

4. **Dispatch Agent**
   - `price_rank` engine: simple, auditable daily price-rank dispatch.
   - `lp` engine: daily linear-programming dispatch (scipy/HiGHS) with SOC
     carry-over, efficiency, reserve floor and daily cycle limit.

5. **Optimizer Agent**
   - Sweeps BESS durations 2h, 4h, 6h, 8h with either engine and ranks
     scenarios by merchant revenue.

6. **PPA / CFE Mixed-Development Agent** (`agents/ppa_agent.py`)
   - Splits delivered energy into PPA and merchant volumes.
   - Modes: `pro_rata`, `baseload` (contracted MW block), `solar_only`.
   - Annual escalation, capture-price and structure comparison.

7. **Capacity / 100 Critical Hours Agent** (`agents/capacity_agent.py`)
   - Identifies the 100 critical hours (official CENACE list, or
     highest-PML proxy for forward-looking studies).
   - Computes accredited capacity (avg MW delivered in those hours) and
     capacity revenue at a given MXN/MW-year price.

8. **Printout Agent**
   - Excel workbook (summary, assumptions, 8760 dispatch, monthly, scenarios, PPA, capacity).
   - Word, PDF and PowerPoint reports with the same content plus a monthly chart.
   - Optional **investor report**: a polished executive-summary PDF with revenue-by-stream
     and average-day dispatch charts, headline metrics, assumptions and a disclaimer
     (`write_investor_report`, or the "Investor report (PDF)" checkbox in the app).

The app's Dispatch module also renders interactive charts after each run:
average-day dispatch profile, single-day detail (flows + SOC + PML),
annual revenue by stream, and monthly revenue stacked by PPA/merchant.

9. **Behind-the-Meter (CFE) module** — implements the
   `Optimus_Mexico_Behind_TheMeter` developer spec (v1.1 with investor layer):
   - `agents/btm_tariff_agent.py` — 15-minute tariff calendar for GDMTH
     (season / day type / base-intermedia-punta periods, LFT Art. 74 holidays),
     CNE 2026 starter rates and factor de carga, and full CFE bill
     reconstruction (energy by period, Dmaxpunta/Dmaxmensual,
     capacity and distribution billing kW with the min(formula, measured) rule).
     Includes the GDMTO→GDMTH migration check (peak ≥ 100 kW).
   - `agents/btm_dispatch_agent.py` — rule-based BESS / PV+BESS dispatch at
     15-minute resolution in no-export mode: PV self-consumption first,
     monthly peak-shaving import cap found by binary search, punta TOU
     shifting, and base-hour grid charging.
   - `agents/btm_investor_agent.py` — Small Industrial Investor Layer:
     financing payment (cash/lease/loan), explicit OPEX and insurance
     (entered as % of CAPEX per year), bankable savings with confidence
     haircuts, base/downside/upside cases, monthly AND annual benefit
     figures, lifetime economics over a configurable BESS life (default
     20 years: NPV at the chosen discount rate, unlevered project IRR,
     lifetime net benefit), red-flag detection and the deterministic
     GO / REVISE / NO-GO recommendation (including payback-vs-BESS-life
     and negative-NPV gates), plus the Excel investor evidence package.
   - Reference data in `data/reference/` (tariff periods, starter rates,
     factor de carga) and sample 15-minute load/PV data via
     `python scripts/make_btm_sample_data.py`.
   - In the app: module "2. Behind-the-Meter (CFE)" with bill before/after
     charts, savings by tariff component, daily dispatch viewer (load, grid
     import, PV, import cap, SOC) and the investor dashboard.

10. **BTM Investor Fix package** (from `Optimus_Mexico_BTM_Investor_Fixes_Developer_Ready.docx`):
   - **Fix 1 — BTM revenue guard** (`agents/btm_revenue_guard.py`): when
     `project_mode = BTM_CFE`, merchant/PML/PPA/CENACE capacity streams are
     excluded; only CFE bill savings and PV self-consumption count.
   - **Fix 2 — LP bankable dispatch** (`agents/btm_lp_optimizer_agent.py`):
     daily LP at 15-minute resolution with monthly import-cap sweep, post-bill
     CFE recalculation, and safe fallback to rule-based dispatch if the solver
     fails. Selectable in the app: rule-based screening, LP bankable, or compare both.
   - **Fix 3 — Investment readiness gate** (`agents/btm_investment_readiness_agent.py`):
     every case is labeled **DEMO | SCREENING | REVISE | INVESTMENT READY**.
     GO/REVISE/NO-GO is blocked unless status is INVESTMENT READY; sample data
     always runs in DEMO mode.
   - BTM sub-workflows in the app: **A. Screening Case**, **B. Bankable Investor
     Case**, **C. Customer Proposal Case**, **D. Technical Dispatch Detail**.
   - CLI helpers: `scripts/run_btm_readiness.py`, `scripts/run_btm_lp.py`,
     `scripts/make_btm_investor_package.py`.

11. **Front-of-meter project economics** (`agents/fom_investor_agent.py`):
   - CAPEX from PV (USD/kWp) + BESS (USD/kWh), OPEX and insurance (% CAPEX/yr),
     financing (cash / loan / lease), project life and discount rate.
   - **Project IRR** (unlevered and levered) on merchant + PPA + capacity revenue
     after OPEX; NPV and payback shown in the app after each wholesale run.

12. **BTM savings IRR** — shown for all BTM workflows:
   - **IRR on CFE savings** (unlevered): modeled bill savings minus OPEX/insurance vs CAPEX.
   - **IRR on CFE savings** (levered): bankable savings net of financing payments.
   - CAPEX includes PV + BESS when in PV+BESS mode.

The app navigation is a horizontal menu at the top of the page:
How it works | 0. Node Resolver | 1. Front-of-Meter Dispatch + PPA + Capacity | 2. Behind-the-Meter (CFE).

Both PV-enabled modules accept **uploaded production profiles** or **PV system specs**
(MW AC, MWp, yield kWh/kWp/year, degradation %/year). Set MWp = 0 for BESS-only cases.

## Install

```bash
cd Optimus_Mexico
python -m venv .venv
.venv\Scripts\activate   # Windows PowerShell/CMD equivalent may vary
pip install -r requirements.txt
```

## Run Streamlit app

Easiest on Windows: double-click `run_app.bat`. It creates the `.venv`,
installs dependencies on first run, and starts the app.

Or manually:

```bash
.venv\Scripts\python.exe -m streamlit run app.py
```

In the **Node Resolver** module the existing workspace catalog
(`data/catalogs/nodes_catalog_enriched.csv`) is used automatically — no upload
or download is required. Pick nodes from the searchable dropdown (filter by
zona de carga, entidad federativa or tipo), or switch the node source to an
existing PML file in the workspace or a fresh upload. Uploading a replacement
catalog is only needed if the workspace catalog is missing or outdated.

## Run the full pipeline from CLI

Generate sample test data (synthetic, for pipeline testing only), then run
the end-to-end analysis:

```bash
python scripts/make_sample_data.py
python scripts/run_pipeline.py --pml data/sample/pml_8760_sample.csv --pv data/sample/pv_8760_sample.csv --bess-mw 50 --bess-mwh 200 --engine lp --ppa-price 950 --ppa-fraction 0.7 --capacity-price 1450000 --out outputs/reports
```

Outputs land in `outputs/reports/` as `.xlsx`, `.docx`, `.pdf` and `.pptx`.

## Run tests

```bash
pytest tests -q
```

Currently **71 tests** (wholesale dispatch, BTM tariff/bill/dispatch/investor, BTM fix
package, PV synthesis, FOM economics).

## Module 0: Extract nodes from a PML PDF

```bash
python scripts/extract_nodes_from_pml_pdf.py --pdf "PreciosMargLocales SIN MTR_Expost Dia 2026-05-14 v2026 05 17_08 13 25.pdf" --out outputs/excel/nodes_extracted.csv
```

## Node catalog: download + enrich

Get the latest official `Catálogo NodosP Sistema Eléctrico Nacional` workbook
and build the enriched catalog (zona de carga, tipo, gerencia, entidad,
municipio per node):

```bash
python scripts/download_official_catalog.py
python scripts/enrich_node_catalog.py --xlsx "data/catalogs/Catalogo_NodosP_SEN_v2026-05-20.xlsx"
```

This writes `data/catalogs/nodes_catalog_enriched.csv/.xlsx`, which the app
uses automatically as the default catalog. Note: the CENACE Nodos2.aspx
grid pagination currently returns server errors, so the official workbook is
the reliable source for per-node details.

## Resolve nodes against CENACE catalog

Download the official CENACE `Catálogo de NodosP` as Excel/CSV and run:

```bash
python scripts/resolve_nodes.py --nodes outputs/excel/nodes_extracted.csv --catalog "Catalogo NodosP Sistema Electrico Nacional.xlsx" --out outputs/excel/node_resolution_report.xlsx
```

## Build map

Requires `lat` and `lon` columns in the resolved node workbook. If exact coordinates are not available, join municipality centroids first.

```bash
python scripts/build_node_map.py --resolved outputs/excel/node_resolution_report.xlsx --out outputs/maps/node_map.html
```

## Download PML data

Do not hard-code or publish node names. Set the selected node privately:

```bash
set CENACE_NODE_ID=YOUR_NODE_CODE_HERE
python scripts/download_pml.py --system SIN --market MTR --start 2026-01-01 --end 2026-01-31 --out outputs/excel/pml
```

PowerShell:

```powershell
$env:CENACE_NODE_ID="YOUR_NODE_CODE_HERE"
python scripts/download_pml.py --system SIN --market MTR --start 2026-01-01 --end 2026-01-31 --out outputs/excel/pml
```

## Behind-the-Meter sample data

```bash
python scripts/make_btm_sample_data.py
```

Generates `data/sample/btm_load_15min_sample.csv` (synthetic two-shift
industrial plant) and `btm_pv_15min_sample.csv` (500 kWp PV), one full year
at 15-minute resolution. The app's Behind-the-Meter module uses them by
default so no upload is needed.

## Presentation

```bash
python scripts/make_presentation.py
```

Builds `docs/Optimus_Mexico_Presentation.pptx` — an overview deck with the
system purpose, pipeline flow, one slide per module (with app screenshots
when available in `docs/presentation_assets/`), sample results and the
technology stack.

## Folder structure

```text
Optimus_Mexico/
├── app.py                      # Streamlit app (horizontal menu, 4 pages)
├── requirements.txt
├── run_app.bat                 # Windows launcher (creates venv, installs, runs)
├── config/
├── agents/
│   ├── node_catalog_agent.py
│   ├── node_resolver_agent.py
│   ├── node_map_agent.py
│   ├── pml_scraper_agent.py
│   ├── pv_loader_agent.py
│   ├── pv_synthesis_agent.py     # MW AC / MWp / yield → hourly or 15-min profile
│   ├── dispatch_agent.py         # price_rank + LP wholesale dispatch
│   ├── optimizer_agent.py
│   ├── ppa_agent.py
│   ├── capacity_agent.py
│   ├── printout_agent.py         # Excel/Word/PDF/PPT + investor report
│   ├── fom_investor_agent.py     # FOM CAPEX/OPEX/IRR on revenue
│   ├── btm_tariff_agent.py       # CFE tariff calendar + bill reconstruction
│   ├── btm_dispatch_agent.py     # 15-min BESS/PV+BESS no-export dispatch
│   ├── btm_lp_optimizer_agent.py # LP bankable BTM dispatch
│   ├── btm_revenue_guard.py      # BTM-only value streams
│   ├── btm_investment_readiness_agent.py  # DEMO/SCREENING/REVISE/INVESTMENT READY
│   └── btm_investor_agent.py     # BTM savings IRR, NPV, GO/REVISE/NO-GO
├── ui/
│   └── pv_system_panel.py        # shared Streamlit PV specs / upload panel
├── core/
│   └── node_utils.py
├── scripts/
├── tests/                        # pytest suite (71 tests)
├── data/
│   ├── catalogs/               # CENACE node catalogs (incl. enriched)
│   ├── reference/              # tariff periods, starter rates, factor de carga
│   └── sample/                 # synthetic PML/PV 8760 + BTM 15-min data
├── docs/                       # specs, extracted notes, presentation
└── outputs/                    # generated reports (git-ignored)
```

## Next coding tasks

1. Harden CENACE PML endpoint handling after testing against live service.
2. Calibrate capacity price and critical-hour list against official CENACE publications.
3. Multi-year PPA and PV degradation in cash-flow models.
4. BTM phase 2: CFE/CNE tariff scrapers, PDF bill parser, DIST/DIT tariffs.
5. Integrate into Optimus AI.
