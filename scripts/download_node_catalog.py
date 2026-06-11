# scripts/download_node_catalog.py
"""
Optimus_Mexico - CENACE Node Catalog Scraper

Working approach:
- Scrapes https://www.cenace.gob.mx/Nodos2.aspx
- Extracts node codes from the HTML/filter/dropdown content
- Builds a clean node catalog for the PML scraper

Outputs:
- data/catalogs/nodes_catalog.csv
- data/catalogs/nodes_catalog.xlsx

Run:
    python scripts\\download_node_catalog.py
"""

from __future__ import annotations

import argparse
import io
import re
import unicodedata
from pathlib import Path

import pandas as pd
import requests


CENACE_NODOS2_URL = "https://www.cenace.gob.mx/Nodos2.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Referer": "https://www.cenace.gob.mx/Paginas/SIM/NodosP.aspx",
}


def strip_accents(value: str) -> str:
    value = "" if value is None else str(value)
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_node_code(value) -> str:
    if value is None or pd.isna(value):
        return ""

    text = str(value).strip().upper()
    text = strip_accents(text)
    text = text.replace(" ", "")
    text = text.replace("–", "-").replace("—", "-")
    return text


def download_html() -> str:
    print(f"Downloading CENACE node page: {CENACE_NODOS2_URL}", flush=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    response = session.get(CENACE_NODOS2_URL, timeout=120)
    print(f"HTTP status: {response.status_code}", flush=True)
    response.raise_for_status()

    Path("outputs/debug").mkdir(parents=True, exist_ok=True)
    Path("outputs/debug/nodos2_page.html").write_text(
        response.text,
        encoding="utf-8",
    )

    return response.text


def extract_nodes_from_text(text: str) -> list[str]:
    """
    Extract CENACE node codes from any page/table/filter text.

    Handles:
    - 01AAN-85
    - 01ACO-230
    - 08CHB-34.5
    - compact variants such as 03MRA1115, left as-is for now
    """
    text = strip_accents(text.upper())

    # Main official PML/node format with voltage.
    pattern_full = re.compile(r"\b\d{2}[A-Z0-9]{3,8}-\d{2,3}(?:\.\d+)?\b")

    nodes = pattern_full.findall(text)

    # Keep unique sorted.
    nodes = sorted(set(normalize_node_code(x) for x in nodes if x))

    return nodes


def extract_nodes_from_html_tables(html: str) -> list[str]:
    """
    CENACE Nodos2.aspx returns the node list inside a filter/dropdown table,
    not as clean row-by-row records. This function reads all HTML tables,
    converts them to text, and extracts node codes.
    """
    print("Reading HTML tables...", flush=True)

    tables = pd.read_html(io.StringIO(html))
    print(f"HTML tables found: {len(tables)}", flush=True)

    all_text_parts: list[str] = []

    Path("outputs/debug").mkdir(parents=True, exist_ok=True)

    for i, table in enumerate(tables):
        csv_path = Path(f"outputs/debug/nodos2_table_{i}.csv")
        table.to_csv(csv_path, index=False, encoding="utf-8-sig")

        table_text = " ".join(
            table.astype(str).fillna("").values.ravel().tolist()
        )
        all_text_parts.append(table_text)

        print(f"Table {i}: shape={table.shape}, saved={csv_path}", flush=True)

    combined_text = "\n".join(all_text_parts)

    nodes = extract_nodes_from_text(combined_text)

    print(f"Node codes extracted from tables: {len(nodes)}", flush=True)

    return nodes


def parse_node_components(node_code: str) -> dict:
    node_code = normalize_node_code(node_code)

    voltage = ""
    node_prefix = ""
    node_base = ""

    match = re.match(
        r"^(?P<prefix>\d{2})(?P<base>[A-Z0-9]{3,8})-(?P<voltage>\d{2,3}(?:\.\d+)?)$",
        node_code,
    )

    if match:
        node_prefix = match.group("prefix")
        node_base = match.group("base")
        voltage = match.group("voltage")

    return {
        "clave_nodo_p": node_code,
        "clave_nodo_norm": node_code,
        "node_prefix": node_prefix,
        "node_base": node_base,
        "sistema": "",
        "num_zc": "",
        "zona_carga": "",
        "voltaje_kv": voltage,
        "tipo": "",
        "gerencia_regional": "",
        "entidad_federativa": "",
        "municipio": "",
        "lat": "",
        "lon": "",
    }


def build_catalog(nodes: list[str]) -> pd.DataFrame:
    rows = [parse_node_components(node) for node in nodes]
    df = pd.DataFrame(rows)

    if df.empty:
        return pd.DataFrame(
            columns=[
                "clave_nodo_p",
                "clave_nodo_norm",
                "node_prefix",
                "node_base",
                "sistema",
                "num_zc",
                "zona_carga",
                "voltaje_kv",
                "tipo",
                "gerencia_regional",
                "entidad_federativa",
                "municipio",
                "lat",
                "lon",
            ]
        )

    df = df.drop_duplicates(subset=["clave_nodo_norm"])
    df = df.sort_values("clave_nodo_norm").reset_index(drop=True)

    return df


def export_catalog(df: pd.DataFrame, out_csv: Path, out_xlsx: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)

    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    summary = pd.DataFrame(
        [
            {"metric": "total_nodes", "value": len(df)},
            {"metric": "unique_nodes", "value": df["clave_nodo_norm"].nunique()},
            {
                "metric": "nodes_with_voltage",
                "value": int((df["voltaje_kv"].astype(str).str.len() > 0).sum()),
            },
            {
                "metric": "nodes_with_zone",
                "value": int((df["zona_carga"].astype(str).str.len() > 0).sum()),
            },
            {
                "metric": "nodes_with_coordinates",
                "value": int((df["lat"].astype(str).str.len() > 0).sum()),
            },
        ]
    )

    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="nodes_catalog", index=False)
        summary.to_excel(writer, sheet_name="summary", index=False)


def main() -> None:
    print("STARTING CENACE node catalog scraper", flush=True)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-csv",
        default="data/catalogs/nodes_catalog.csv",
    )
    parser.add_argument(
        "--out-xlsx",
        default="data/catalogs/nodes_catalog.xlsx",
    )
    args = parser.parse_args()

    html = download_html()
    nodes = extract_nodes_from_html_tables(html)
    catalog = build_catalog(nodes)

    export_catalog(
        catalog,
        Path(args.out_csv),
        Path(args.out_xlsx),
    )

    print("")
    print("Done.", flush=True)
    print(f"Nodes extracted: {len(catalog)}", flush=True)
    print(f"CSV: {args.out_csv}", flush=True)
    print(f"Excel: {args.out_xlsx}", flush=True)
    print("")
    print("First 20 nodes:", flush=True)
    print(catalog.head(20).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()