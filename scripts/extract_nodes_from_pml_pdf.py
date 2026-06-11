# scripts/extract_nodes_from_pml_pdf.py
"""
Optimus_Mexico - Extract CENACE node codes from a PML PDF.

This script reads a CENACE PML PDF report and extracts unique node codes such as:
01AAN-85, 01ACO-230, 08CHB-34.5, etc.

Run from the Optimus_Mexico root folder:

python scripts\extract_nodes_from_pml_pdf.py --pdf "PreciosMargLocales SIN MTR_Expost Dia 2026-05-14 v2026 05 17_08 13 25.pdf" --out outputs\excel\nodes_extracted.csv

or:

python -m scripts.extract_nodes_from_pml_pdf --pdf "PreciosMargLocales SIN MTR_Expost Dia 2026-05-14 v2026 05 17_08 13 25.pdf" --out outputs\excel\nodes_extracted.csv
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd


# ---------------------------------------------------------------------
# Make project root importable even when running:
# python scripts\extract_nodes_from_pml_pdf.py
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_pdf_text_with_pypdf(pdf_path: Path) -> str:
    """
    Read text from a PDF using pypdf.

    Returns a single string containing all extractable text.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError(
            "Missing package: pypdf. Install it with:\n"
            "python -m pip install pypdf"
        ) from exc

    reader = PdfReader(str(pdf_path))
    chunks: list[str] = []

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            print(f"Warning: could not read page {page_number}: {exc}")
            text = ""
        chunks.append(text)

    return "\n".join(chunks)


def normalize_node_code(raw_code: str) -> str:
    """
    Normalize CENACE node codes.

    Examples:
    01AAN-85      -> 01AAN-85
    01ACO-230     -> 01ACO-230
    08CHB-34.5    -> 08CHB-34.5
    03MRA1115     -> 03MRA1-115  possibly, for compact variants

    The normal rule in the PDF is already:
    two digits + letters/numbers + dash + voltage.
    """
    if raw_code is None:
        return ""

    code = str(raw_code).strip().upper()
    code = code.replace(" ", "")
    code = code.replace("–", "-").replace("—", "-")

    # Already normal: 01AAN-85, 01ACO-230, 08CHB-34.5
    if re.fullmatch(r"\d{2}[A-Z0-9]{3,6}-\d{2,3}(?:\.\d+)?", code):
        return code

    # Compact fallback: something like 03MRA1115 -> 03MRA1-115
    # This is intentionally conservative.
    compact = re.fullmatch(r"(\d{2}[A-Z0-9]{3,6})(\d{2,3})", code)
    if compact:
        return f"{compact.group(1)}-{compact.group(2)}"

    return code


def extract_node_codes_from_text(text: str) -> list[str]:
    """
    Extract unique CENACE node codes from raw text.

    Pattern targets:
    01AAN-85
    01ACO-230
    08CHB-34.5
    09LBR-230
    """
    if not text:
        return []

    # Main CENACE node pattern from PML PDFs.
    pattern = re.compile(r"\b\d{2}[A-Z0-9]{3,6}-\d{2,3}(?:\.\d+)?\b")

    found = pattern.findall(text.upper())
    normalized = [normalize_node_code(x) for x in found]

    # Keep unique sorted list.
    unique = sorted(set(x for x in normalized if x))
    return unique


def extract_nodes_from_pml_pdf(pdf_path: str | Path) -> pd.DataFrame:
    """
    Extract node codes from a CENACE PML PDF.

    Returns a DataFrame with:
    - clave_nodo
    - clave_nodo_norm
    - voltage_kv
    - node_prefix
    - node_base
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    print(f"Reading PDF: {pdf_path}")
    text = _read_pdf_text_with_pypdf(pdf_path)

    print("Extracting node codes...")
    node_codes = extract_node_codes_from_text(text)

    rows = []
    for code in node_codes:
        norm = normalize_node_code(code)

        voltage = None
        node_prefix = None
        node_base = None

        match = re.fullmatch(r"(\d{2})([A-Z0-9]{3,6})-(\d{2,3}(?:\.\d+)?)", norm)
        if match:
            node_prefix = match.group(1)
            node_base = match.group(2)
            voltage = match.group(3)

        rows.append(
            {
                "clave_nodo": code,
                "clave_nodo_norm": norm,
                "node_prefix": node_prefix,
                "node_base": node_base,
                "voltage_kv": voltage,
            }
        )

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values("clave_nodo_norm").reset_index(drop=True)

    return df


def save_nodes(df: pd.DataFrame, out_path: str | Path) -> Path:
    """
    Save extracted nodes to CSV or Excel depending on extension.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    suffix = out_path.suffix.lower()

    if suffix == ".xlsx":
        df.to_excel(out_path, index=False)
    elif suffix == ".csv":
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        raise ValueError("Output file must end with .csv or .xlsx")

    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract unique CENACE node codes from a PML PDF."
    )

    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the CENACE PML PDF file.",
    )

    parser.add_argument(
        "--out",
        required=True,
        help="Output file path. Use .csv or .xlsx.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    pdf_path = Path(args.pdf)
    out_path = Path(args.out)

    try:
        df = extract_nodes_from_pml_pdf(pdf_path)
        saved_path = save_nodes(df, out_path)

        print("")
        print("Done.")
        print(f"Nodes extracted: {len(df)}")
        print(f"Saved to: {saved_path}")

        if len(df) > 0:
            print("")
            print("First 10 nodes:")
            print(df.head(10).to_string(index=False))

    except Exception as exc:
        print("")
        print("ERROR")
        print(str(exc))
        raise


if __name__ == "__main__":
    main()