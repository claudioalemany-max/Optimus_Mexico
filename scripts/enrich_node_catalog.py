"""Enrich the CENACE node catalog with per-node details.

The Nodos2.aspx page shows a paginated GridView (control id "CC") with one
row per NodoP: SISTEMA, NUM ZC, ZONA DE CARGA, CLAVE NODO P, VOLTAJE, TIPO,
GERENCIA REGIONAL. The existing catalog (nodes_catalog1.csv) only has the
node keys from the page dropdown, so those detail columns are empty.

This script walks every page of the grid via ASP.NET __doPostBack requests,
collects all rows, and merges them into the catalog.

Outputs:
- data/catalogs/nodes_catalog_enriched.csv
- data/catalogs/nodes_catalog_enriched.xlsx

Run:
    python scripts/enrich_node_catalog.py
Offline mode (re-parse saved HTML pages only):
    python scripts/enrich_node_catalog.py --offline
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.node_utils import normalize_node_code  # noqa: E402

CENACE_NODOS2_URL = "https://www.cenace.gob.mx/Nodos2.aspx"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Referer": "https://www.cenace.gob.mx/Paginas/SIM/NodosP.aspx",
}

NODE_CODE_RE = re.compile(r"^\d{2}[A-Z0-9]{3,8}-?\d{2,3}(?:\.\d+)?$")
HIDDEN_INPUT_RE = re.compile(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', re.I)
SELECT_RE = re.compile(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', re.S | re.I)
SELECTED_OPTION_RE = re.compile(r'<option[^>]*selected="selected"[^>]*value="([^"]*)"', re.I)
FIRST_OPTION_RE = re.compile(r'<option[^>]*value="([^"]*)"', re.I)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")

GRID_COLUMNS = ["sistema", "num_zc", "zona_carga", "clave_nodo_p", "voltaje_kv", "tipo", "gerencia_regional"]
DEBUG_DIR = ROOT / "outputs" / "debug" / "nodos2_pages"


def parse_form_fields(html: str) -> dict[str, str]:
    """Collect the full ASP.NET form state: hidden inputs plus the selected
    value of every dropdown. Buttons are excluded so no click is simulated."""
    fields: dict[str, str] = {}
    for name, value in HIDDEN_INPUT_RE.findall(html):
        if name.startswith("Button"):
            continue
        fields[name] = value
    for name, body in SELECT_RE.findall(html):
        selected = SELECTED_OPTION_RE.search(body)
        if selected:
            fields[name] = selected.group(1)
        else:
            first = FIRST_OPTION_RE.search(body)
            fields[name] = first.group(1) if first else ""
    return fields


def parse_grid_rows(html: str) -> list[dict[str, str]]:
    """Extract node rows from a Nodos2 page. A node row has 7 cells and the
    4th cell is a Clave NodoP."""
    rows = []
    for row_html in ROW_RE.findall(html):
        cells = [TAG_RE.sub("", c).replace("&nbsp;", " ").strip() for c in CELL_RE.findall(row_html)]
        if len(cells) != len(GRID_COLUMNS):
            continue
        code = normalize_node_code(cells[3])
        if not NODE_CODE_RE.match(cells[3].strip().upper()) and not NODE_CODE_RE.match(code):
            continue
        record = dict(zip(GRID_COLUMNS, cells))
        record["clave_nodo_norm"] = code
        rows.append(record)
    return rows


def parse_max_page(html: str) -> int:
    pages = [int(m) for m in re.findall(r"__doPostBack\(&#39;CC&#39;,&#39;Page\$(\d+)&#39;\)", html)]
    pages += [int(m) for m in re.findall(r"__doPostBack\('CC','Page\$(\d+)'\)", html)]
    return max(pages) if pages else 1


def scrape_all_pages(pause_s: float = 0.4, max_pages: int = 500) -> pd.DataFrame:
    session = requests.Session()
    session.headers.update(HEADERS)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    resp = session.get(CENACE_NODOS2_URL, timeout=120)
    resp.raise_for_status()
    html = resp.text
    (DEBUG_DIR / "page_001.html").write_text(html, encoding="utf-8")

    all_rows = parse_grid_rows(html)
    seen_keys = {r["clave_nodo_norm"] for r in all_rows}
    print(f"Page 1: {len(all_rows)} rows", flush=True)

    page = 2
    stale_pages = 0
    while page <= max_pages:
        data = parse_form_fields(html)
        data["__EVENTTARGET"] = "CC"
        data["__EVENTARGUMENT"] = f"Page${page}"
        data["__LASTFOCUS"] = ""
        try:
            resp = session.post(CENACE_NODOS2_URL, data=data, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"Page {page}: request failed ({exc}); stopping.", flush=True)
            break
        html = resp.text
        (DEBUG_DIR / f"page_{page:03d}.html").write_text(html, encoding="utf-8")

        rows = parse_grid_rows(html)
        new = [r for r in rows if r["clave_nodo_norm"] not in seen_keys]
        print(f"Page {page}: {len(rows)} rows ({len(new)} new), max page seen: {parse_max_page(html)}", flush=True)

        if not rows:
            break
        all_rows.extend(new)
        seen_keys.update(r["clave_nodo_norm"] for r in new)

        # The pager loops back / repeats when past the last page
        stale_pages = stale_pages + 1 if not new else 0
        if stale_pages >= 3:
            break
        if page >= parse_max_page(html) and not new:
            break

        page += 1
        time.sleep(pause_s)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["clave_nodo_norm"], keep="first")
    return df


def parse_saved_pages() -> pd.DataFrame:
    """Offline mode: re-parse previously downloaded HTML pages."""
    rows: list[dict[str, str]] = []
    sources = sorted(DEBUG_DIR.glob("page_*.html")) if DEBUG_DIR.exists() else []
    legacy = ROOT / "outputs" / "debug" / "nodos2_page.html"
    if legacy.exists():
        sources.append(legacy)
    for path in sources:
        rows.extend(parse_grid_rows(path.read_text(encoding="utf-8")))
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["clave_nodo_norm"], keep="first")
    return df


def details_from_official_xlsx(xlsx_path: Path) -> pd.DataFrame:
    """Parse the official 'Catálogo NodosP Sistema Eléctrico Nacional' workbook.

    The sheet has a two-row header; the real column names are on row 2.
    """
    raw = pd.read_excel(xlsx_path, sheet_name=0, header=1)
    raw.columns = [str(c).strip().upper() for c in raw.columns]

    def col(name_part: str, exclude: str | None = None) -> pd.Series:
        for c in raw.columns:
            if name_part in c and (exclude is None or exclude not in c):
                return raw[c]
        return pd.Series([""] * len(raw))

    def norm_flag(series: pd.Series) -> pd.Series:
        return series.astype(str).str.strip().str.lower().isin(["directamente modelada", "indirectamente modelada"])

    carga = norm_flag(col("DIRECTAMENTE MODELADA")) | norm_flag(col("INDIRECTAMENTE MODELADA"))
    # Generation columns appear after the load columns; locate them positionally
    gen_cols = [c for c in raw.columns if "MODELADA" in c]
    gen = pd.Series([False] * len(raw))
    if len(gen_cols) >= 4:
        gen = norm_flag(raw[gen_cols[2]]) | norm_flag(raw[gen_cols[3]])
        carga = norm_flag(raw[gen_cols[0]]) | norm_flag(raw[gen_cols[1]])
    tipo = pd.Series(
        ["GENERACION Y CARGA" if g and c else "GENERACION" if g else "CARGA" if c else "" for g, c in zip(gen, carga)]
    )

    out = pd.DataFrame({
        "clave_nodo_p": col("CLAVE").astype(str).str.strip().str.upper(),
        "nombre": col("NOMBRE").astype(str).str.strip(),
        "sistema": col("SISTEMA").astype(str).str.strip(),
        "centro_control_regional": col("CENTRO DE CONTROL").astype(str).str.strip(),
        "zona_carga": col("ZONA DE CARGA").astype(str).str.strip(),
        "voltaje_kv": col("NIVEL DE TENSI").astype(str).str.strip(),
        "tipo": tipo,
        "zona_operacion_transmision": col("ZONA DE OPERACI").astype(str).str.strip(),
        "gerencia_regional": col("GERENCIA REGIONAL").astype(str).str.strip(),
        "zona_distribucion": col("ZONA DE DISTRIBUCI").astype(str).str.strip(),
        "gerencia_divisional": col("GERENCIA DIVISIONAL").astype(str).str.strip(),
        "clave_entidad_inegi": col("CLAVE DE ENTIDAD").astype(str).str.strip(),
        "entidad_federativa": col("ENTIDAD FEDERATIVA", exclude="CLAVE").astype(str).str.strip(),
        "clave_municipio_inegi": col("CLAVE DE MUNICIPIO").astype(str).str.strip(),
        "municipio": col("MUNICIPIO", exclude="CLAVE").astype(str).str.strip(),
        "region_transmision": col("REGION DE TRANSMISION").astype(str).str.strip(),
    })
    out = out[out["clave_nodo_p"].str.len() > 0]
    out = out[out["clave_nodo_p"].str.lower() != "nan"]
    out["clave_nodo_norm"] = out["clave_nodo_p"].map(normalize_node_code)
    return out.drop_duplicates(subset=["clave_nodo_norm"], keep="first")


def build_from_official(xlsx_path: Path, base_catalog_path: Path) -> pd.DataFrame:
    """Authoritative build: official workbook rows, plus any node keys from the
    base catalog that the workbook doesn't list."""
    official = details_from_official_xlsx(xlsx_path)
    base = pd.read_csv(base_catalog_path, dtype=str).fillna("")
    if "clave_nodo_norm" not in base.columns:
        base["clave_nodo_norm"] = base["clave_nodo_p"].map(normalize_node_code)
    missing = base[~base["clave_nodo_norm"].isin(set(official["clave_nodo_norm"]))]
    if len(missing):
        print(f"Keeping {len(missing)} base-catalog keys not present in official workbook.")
        official = pd.concat([official, missing], ignore_index=True)
    return official.sort_values("clave_nodo_norm").reset_index(drop=True)


def merge_into_catalog(catalog_path: Path, details: pd.DataFrame) -> pd.DataFrame:
    catalog = pd.read_csv(catalog_path, dtype=str).fillna("")
    if "clave_nodo_norm" not in catalog.columns:
        catalog["clave_nodo_norm"] = catalog["clave_nodo_p"].map(normalize_node_code)

    if details.empty:
        print("No detail rows scraped; catalog left unchanged.")
        return catalog

    detail_cols = ["sistema", "num_zc", "zona_carga", "voltaje_kv", "tipo", "gerencia_regional"]
    dd = details.set_index("clave_nodo_norm")[detail_cols]

    merged = catalog.set_index("clave_nodo_norm")
    for col in detail_cols:
        if col not in merged.columns:
            merged[col] = ""
        incoming = dd[col].reindex(merged.index)
        merged[col] = merged[col].astype(str).replace("nan", "")
        merged[col] = incoming.fillna(merged[col]).where(incoming.notna() & (incoming != ""), merged[col])
    merged = merged.reset_index()

    # Nodes seen in the grid but missing from the catalog get appended
    extra_keys = set(dd.index) - set(catalog["clave_nodo_norm"])
    if extra_keys:
        extras = details[details["clave_nodo_norm"].isin(extra_keys)].copy()
        extras["clave_nodo_p"] = extras["clave_nodo_norm"]
        merged = pd.concat([merged, extras], ignore_index=True)
        print(f"Appended {len(extras)} grid nodes missing from the catalog.")
    return merged.sort_values("clave_nodo_norm").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=None, help="Existing catalog CSV (defaults to first nodes_catalog*.csv found)")
    parser.add_argument("--xlsx", default=None, help="Official Catálogo NodosP workbook; if given, builds the enriched catalog from it (preferred)")
    parser.add_argument("--offline", action="store_true", help="Only re-parse saved HTML pages, no network")
    parser.add_argument("--out-csv", default=str(ROOT / "data" / "catalogs" / "nodes_catalog_enriched.csv"))
    parser.add_argument("--out-xlsx", default=str(ROOT / "data" / "catalogs" / "nodes_catalog_enriched.xlsx"))
    parser.add_argument("--pause", type=float, default=0.4)
    args = parser.parse_args()

    if args.catalog:
        catalog_path = Path(args.catalog)
    else:
        candidates = sorted((ROOT / "data" / "catalogs").glob("nodes_catalog*.csv"))
        candidates = [c for c in candidates if "enriched" not in c.name]
        if not candidates:
            raise FileNotFoundError("No nodes_catalog*.csv found in data/catalogs")
        catalog_path = candidates[0]
    print(f"Base catalog: {catalog_path}")

    if args.xlsx:
        enriched = build_from_official(Path(args.xlsx), catalog_path)
    else:
        details = parse_saved_pages() if args.offline else scrape_all_pages(pause_s=args.pause)
        print(f"Detail rows collected: {len(details)}")
        enriched = merge_into_catalog(catalog_path, details)
    filled = int((enriched["zona_carga"].astype(str).str.len() > 0).sum())
    print(f"Catalog rows: {len(enriched)}, rows with zona_carga: {filled}")

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(out_csv, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(args.out_xlsx, engine="openpyxl") as writer:
        enriched.to_excel(writer, sheet_name="nodes_catalog", index=False)
        summary = pd.DataFrame([
            {"metric": "total_nodes", "value": len(enriched)},
            {"metric": "with_zona_carga", "value": filled},
            {"metric": "with_sistema", "value": int((enriched["sistema"].astype(str).str.len() > 0).sum())},
            {"metric": "with_tipo", "value": int((enriched["tipo"].astype(str).str.len() > 0).sum())},
            {"metric": "with_gerencia", "value": int((enriched["gerencia_regional"].astype(str).str.len() > 0).sum())},
        ])
        summary.to_excel(writer, sheet_name="summary", index=False)
    print(f"Wrote {out_csv}")
    print(f"Wrote {args.out_xlsx}")


if __name__ == "__main__":
    main()
