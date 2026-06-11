# Optimus Mexico implementation plan

## Build order

1. Node Resolver Agent
2. CENACE PML Scraper Agent
3. PV 8760 Loader Agent
4. Dispatch Agent
5. PPA / CFE mixed-development Agent
6. Capacity / 100 Critical Hours Agent
7. Optimizer Agent
8. Excel/Word/PDF/PowerPoint Printout Agent

## Key design rule

No node names or guessed node identities should be hard-coded. The app must resolve `Clave NodoP` through the official CENACE catalog before price scraping or market analysis.
