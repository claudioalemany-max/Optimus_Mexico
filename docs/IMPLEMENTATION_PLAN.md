# Optimus Mexico implementation plan

## Build order (completed)

1. Node Resolver Agent
2. CENACE PML Scraper Agent
3. PV 8760 Loader Agent + **PV synthesis** (MW AC / MWp / yield / degradation)
4. Dispatch Agent (wholesale price_rank + LP)
5. PPA / CFE mixed-development Agent
6. Capacity / 100 Critical Hours Agent
7. Optimizer Agent
8. Excel/Word/PDF/PowerPoint Printout Agent + investor report PDF
9. Behind-the-Meter (CFE): tariff calendar, bill engine, rule-based dispatch, investor layer
10. **BTM Investor Fix package**: revenue guard, LP bankable dispatch, investment readiness gate
11. **Front-of-meter project economics**: CAPEX / OPEX / IRR on merchant + PPA + capacity revenue
12. **BTM savings IRR**: unlevered and levered IRR on CFE bill savings with explicit CAPEX/OPEX

## Key design rules

- No node names or guessed node identities should be hard-coded. Resolve `Clave NodoP` through the official CENACE catalog before price scraping or market analysis.
- BTM mode (`BTM_CFE`) must not mix merchant/PML/PPA/CENACE capacity revenues with CFE bill savings.
- GO/REVISE/NO-GO investor recommendations require **INVESTMENT READY** data (synthetic sample data stays **DEMO**).
- PV production can come from an uploaded profile **or** system specs (MW AC, MWp, yield, degradation).

## Quality

- **71 automated pytest tests** covering wholesale dispatch, BTM tariff/bill/dispatch/investor, revenue guard, readiness gate, LP optimizer, PV synthesis and FOM economics.

## Next coding tasks

1. Harden CENACE PML endpoint handling after testing against live service.
2. Calibrate capacity price and critical-hour list against official CENACE publications.
3. Multi-year PPA cash-flow model with degradation on revenue and PV output.
4. BTM phase 2: CFE/CNE tariff scrapers, PDF bill parser, DIST/DIT tariffs.
5. Refresh presentation screenshots after major UI changes.
6. Integrate into Optimus AI.
