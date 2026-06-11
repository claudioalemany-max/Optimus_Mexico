from __future__ import annotations

from pathlib import Path
import fitz
import pandas as pd
from core.node_utils import extract_codes_from_text, normalize_node_code
from agents.node_catalog_agent import load_catalog


def extract_nodes_from_pml_pdf(pdf_path: str | Path) -> pd.DataFrame:
    """Extract unique Clave NodoP values from a CENACE PML PDF.

    This does not infer names. It only extracts node keys from the report.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    doc = fitz.open(pdf_path)
    codes: set[str] = set()
    for page in doc:
        codes.update(extract_codes_from_text(page.get_text()))
    return pd.DataFrame({"clave_nodo_original": sorted(codes)}).assign(
        clave_nodo_norm=lambda d: d["clave_nodo_original"].map(normalize_node_code)
    )


def extract_nodes_from_table(path: str | Path) -> pd.DataFrame:
    """Extract node keys from a CSV/XLSX table containing a node column."""
    path = Path(path)
    if path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    lower = {str(c).lower(): c for c in df.columns}
    candidates = [
        lower[c] for c in lower
        if c in ["clave_nodo", "clave_nodo_p", "clave_del_nodo", "node", "node_code", "node_id"]
        or ("clave" in c and "nodo" in c)
    ]
    if not candidates:
        candidates = [df.columns[0]]
    out = df[[candidates[0]]].rename(columns={candidates[0]: "clave_nodo_original"}).dropna()
    out["clave_nodo_norm"] = out["clave_nodo_original"].map(normalize_node_code)
    return out.drop_duplicates("clave_nodo_norm").sort_values("clave_nodo_norm")


def resolve_nodes(nodes: pd.DataFrame, catalog_path: str | Path) -> pd.DataFrame:
    catalog = load_catalog(catalog_path)
    if "clave_nodo_norm" not in nodes.columns:
        nodes = nodes.copy()
        first = nodes.columns[0]
        nodes["clave_nodo_original"] = nodes[first]
        nodes["clave_nodo_norm"] = nodes[first].map(normalize_node_code)

    resolved = nodes.merge(catalog, on="clave_nodo_norm", how="left", suffixes=("", "_catalog"))
    resolved["resolution_status"] = resolved["clave_nodo_p"].notna().map({True: "matched", False: "unmatched"})
    return resolved


def write_resolution_workbook(resolved: pd.DataFrame, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        resolved.to_excel(writer, sheet_name="Resolved_Nodes", index=False)
        resolved[resolved["resolution_status"] == "unmatched"].to_excel(writer, sheet_name="Unmatched", index=False)
        summary = resolved.groupby("resolution_status", dropna=False).size().reset_index(name="count")
        summary.to_excel(writer, sheet_name="Summary", index=False)
    return out_path
