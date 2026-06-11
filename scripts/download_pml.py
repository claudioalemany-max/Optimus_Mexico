# scripts/download_pml.py
"""
Optimus_Mexico - CENACE SW-PML Downloader

Uses the official SW-PML service pattern:

https://ws01.cenace.gob.mx:8082/SWPML/SIM/{SYSTEM}/{MARKET}/{NODE}/{YYYY}/{MM}/{DD}/{YYYY}/{MM}/{DD}/JSON

Example MDA test:
    python scripts\\download_pml.py --system SIN --market MDA --node-id 01AAN-85 --start 2024-03-23 --end 2024-03-23

Example MTR test:
    python scripts\\download_pml.py --system SIN --market MTR --node-id 01AAN-85 --start 2026-05-14 --end 2026-05-14

Outputs:
    outputs/pml/pml_clean.csv
    outputs/pml/pml_clean.xlsx
    outputs/pml/pml_raw_normalized.csv
    outputs/pml/pml_12x24.xlsx
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import urllib3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


BASE_URL = "https://ws01.cenace.gob.mx:8082/SWPML/SIM"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}


def strip_accents(value: str) -> str:
    value = "" if value is None else str(value)
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def clean_column_name(col: str) -> str:
    col = strip_accents(str(col)).strip().lower()
    col = col.replace("\n", " ").replace("\r", " ")
    col = re.sub(r"[^a-z0-9]+", "_", col)
    col = re.sub(r"_+", "_", col)
    return col.strip("_")


def date_parts(date_str: str) -> tuple[str, str, str]:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return d.strftime("%Y"), d.strftime("%m"), d.strftime("%d")


def build_sw_pml_url(
    system: str,
    market: str,
    node_id: str,
    start_date: str,
    end_date: str,
) -> str:
    sy = system.upper()
    mk = market.upper()
    node = node_id.upper()

    y1, m1, d1 = date_parts(start_date)
    y2, m2, d2 = date_parts(end_date)

    return f"{BASE_URL}/{sy}/{mk}/{node}/{y1}/{m1}/{d1}/{y2}/{m2}/{d2}/JSON"


def request_sw_pml(
    system: str,
    market: str,
    node_id: str,
    start_date: str,
    end_date: str,
) -> dict:
    url = build_sw_pml_url(system, market, node_id, start_date, end_date)

    print(f"Downloading SW-PML: {url}", flush=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    # verify=False is needed because ws01.cenace.gob.mx has a certificate hostname mismatch.
    response = session.get(url, timeout=120, verify=False)

    print(f"HTTP: {response.status_code} | bytes: {len(response.content)}", flush=True)

    debug_dir = Path("outputs/debug/pml")
    debug_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{system}_{market}_{node_id}_{start_date}_{end_date}"
    safe_name = safe_name.replace("/", "_").replace(":", "_")
    raw_path = debug_dir / f"sw_pml_raw_{safe_name}.json"
    raw_path.write_bytes(response.content)

    if response.status_code != 200:
        raise RuntimeError(
            f"SW-PML request failed with HTTP {response.status_code}. "
            f"Raw response saved to {raw_path}"
        )

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse JSON response. Raw saved to {raw_path}") from exc


def normalize_sw_pml_json(payload: dict) -> pd.DataFrame:
    """
    Convert SW-PML JSON into a flat DataFrame.

    CENACE SW-PML usually returns:
    {
      "nombre": "PML",
      "proceso": "MDA",
      "sistema": "SIN",
      "Resultados": [
        {
          "clv_nodo": "01AAN-85",
          "valores": [
             {"hora": 1, "pml": ..., ...},
             {"hora": 2, "pml": ..., ...}
          ]
        }
      ]
    }

    This function explodes the nested 'valores' field into one row per hour.
    """
    if not isinstance(payload, dict):
        raise ValueError("Payload is not a JSON object.")

    resultados = payload.get("Resultados") or payload.get("resultados") or []

    if not resultados:
        raise ValueError(
            "SW-PML JSON returned no Resultados. "
            f"Top-level keys: {list(payload.keys())}"
        )

    rows: list[dict[str, Any]] = []

    for item in resultados:
        if not isinstance(item, dict):
            continue

        node = (
            item.get("clv_nodo")
            or item.get("clave_nodo")
            or item.get("nodo")
            or item.get("Nodo")
            or ""
        )

        valores = item.get("valores") or item.get("Valores") or item.get("VALORES")

        if isinstance(valores, list):
            for value_row in valores:
                if isinstance(value_row, dict):
                    row = dict(value_row)
                    row["clv_nodo"] = node
                    rows.append(row)
                else:
                    rows.append(
                        {
                            "clv_nodo": node,
                            "valor": value_row,
                        }
                    )

        elif isinstance(valores, dict):
            for key, value in valores.items():
                if isinstance(value, dict):
                    row = dict(value)
                    row["hora"] = key
                    row["clv_nodo"] = node
                    rows.append(row)
                else:
                    rows.append(
                        {
                            "clv_nodo": node,
                            "hora": key,
                            "valor": value,
                        }
                    )

        else:
            # If no nested valores field exists, keep the item as-is.
            rows.append(dict(item))

    if not rows:
        raise ValueError("Could not flatten SW-PML Resultados/valores structure.")

    df = pd.DataFrame(rows)

    # Add metadata.
    df["sw_nombre"] = payload.get("nombre", "")
    df["sw_proceso"] = payload.get("proceso", "")
    df["sw_sistema"] = payload.get("sistema", "")
    df["sw_area"] = payload.get("area", "")

    print("")
    print("Normalized SW-PML columns:")
    print(list(df.columns))

    return df


def find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols = list(df.columns)
    for c in candidates:
        if c in cols:
            return c
    return None


def find_numeric_column_by_keywords(df: pd.DataFrame, keywords: list[str]) -> str | None:
    """
    Find a numeric-ish column where all keywords appear in the cleaned column name.
    """
    for c in df.columns:
        name = str(c).lower()
        if all(k in name for k in keywords):
            converted = pd.to_numeric(df[c], errors="coerce")
            if converted.notna().sum() > 0:
                return c
    return None


def clean_pml_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Convert normalized SW-PML output into dispatch-ready format.

    Expected useful columns after flattening may include variants like:
    - clv_nodo
    - hora
    - fecha
    - pml
    - pml_ene
    - pml_per
    - pml_cng
    or Spanish/long-name equivalents.
    """
    df = raw.copy()
    df.columns = [clean_column_name(c) for c in df.columns]

    print("")
    print("Cleaned SW-PML columns:")
    print(list(df.columns))

    node_col = find_column(
        df,
        [
            "clv_nodo",
            "clave_nodo",
            "clave_nodo_p",
            "nodo",
            "node",
        ],
    )

    hour_col = find_column(
        df,
        [
            "hora",
            "hr",
            "hour",
            "h",
        ],
    )

    date_col = find_column(
        df,
        [
            "fecha",
            "fecha_operacion",
            "dia",
            "date",
        ],
    )

    pml_col = find_column(
        df,
        [
            "pml",
            "precio_marginal_local",
            "precio_marginal",
            "precio",
            "valor",
        ],
    )

    energy_col = find_column(
        df,
        [
            "pml_ene",
            "pml_energia",
            "componente_energia",
            "energia",
            "comp_energia",
        ],
    )

    losses_col = find_column(
        df,
        [
            "pml_per",
            "pml_perdidas",
            "componente_perdidas",
            "perdidas",
            "comp_perdidas",
        ],
    )

    congestion_col = find_column(
        df,
        [
            "pml_cng",
            "pml_congestion",
            "componente_congestion",
            "congestion",
            "comp_congestion",
        ],
    )

    # More flexible fallbacks.
    if pml_col is None:
        pml_col = find_numeric_column_by_keywords(df, ["pml"])

    if energy_col is None:
        energy_col = find_numeric_column_by_keywords(df, ["ene"])

    if losses_col is None:
        losses_col = find_numeric_column_by_keywords(df, ["per"])

    if congestion_col is None:
        congestion_col = find_numeric_column_by_keywords(df, ["cng"])

    if node_col is None:
        raise ValueError(f"Could not detect node column. Columns: {list(df.columns)}")

    if hour_col is None:
        raise ValueError(f"Could not detect hour column. Columns: {list(df.columns)}")

    if pml_col is None:
        numeric_cols = []
        for c in df.columns:
            converted = pd.to_numeric(df[c], errors="coerce")
            if converted.notna().sum() > 0:
                numeric_cols.append(c)

        numeric_cols = [c for c in numeric_cols if c != hour_col]

        if not numeric_cols:
            raise ValueError(f"Could not detect PML column. Columns: {list(df.columns)}")

        pml_col = numeric_cols[0]

    out = pd.DataFrame()

    out["node_id"] = df[node_col].astype(str).str.strip().str.upper()
    out["hour"] = pd.to_numeric(df[hour_col], errors="coerce").astype("Int64")

    if date_col:
        out["date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date.astype(str)
        out["date"] = out["date"].replace("NaT", "")
    else:
        out["date"] = ""

    out["pml"] = pd.to_numeric(df[pml_col], errors="coerce")

    out["energy_component"] = (
        pd.to_numeric(df[energy_col], errors="coerce") if energy_col else pd.NA
    )
    out["losses_component"] = (
        pd.to_numeric(df[losses_col], errors="coerce") if losses_col else pd.NA
    )
    out["congestion_component"] = (
        pd.to_numeric(df[congestion_col], errors="coerce") if congestion_col else pd.NA
    )

    out["sw_proceso"] = df["sw_proceso"] if "sw_proceso" in df.columns else ""
    out["sw_sistema"] = df["sw_sistema"] if "sw_sistema" in df.columns else ""

    print("")
    print("Column mapping used:")
    print(f"node_col       = {node_col}")
    print(f"hour_col       = {hour_col}")
    print(f"date_col       = {date_col}")
    print(f"pml_col        = {pml_col}")
    print(f"energy_col     = {energy_col}")
    print(f"losses_col     = {losses_col}")
    print(f"congestion_col = {congestion_col}")

    return out


def apply_requested_dates(clean: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fill missing date column if SW-PML returns only one day without explicit date.
    """
    out = clean.copy()

    if (out["date"].astype(str).str.len() == 0).all():
        if start_date == end_date:
            out["date"] = start_date
        else:
            out["date"] = ""

    out["datetime"] = pd.to_datetime(out["date"], errors="coerce") + pd.to_timedelta(
        out["hour"].astype(float) - 1,
        unit="h",
    )

    out["month"] = pd.to_datetime(out["datetime"], errors="coerce").dt.month
    out["day"] = pd.to_datetime(out["datetime"], errors="coerce").dt.day

    ordered = [
        "datetime",
        "date",
        "hour",
        "month",
        "day",
        "node_id",
        "pml",
        "energy_component",
        "losses_component",
        "congestion_component",
        "sw_sistema",
        "sw_proceso",
    ]

    for c in ordered:
        if c not in out.columns:
            out[c] = pd.NA

    return out[ordered]


def export_outputs(clean: pd.DataFrame, raw: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    clean_csv = out_dir / "pml_clean.csv"
    clean_xlsx = out_dir / "pml_clean.xlsx"
    raw_csv = out_dir / "pml_raw_normalized.csv"
    matrix_xlsx = out_dir / "pml_12x24.xlsx"

    clean.to_csv(clean_csv, index=False, encoding="utf-8-sig")
    raw.to_csv(raw_csv, index=False, encoding="utf-8-sig")

    clean.to_excel(clean_xlsx, index=False)

    matrix = (
        clean.dropna(subset=["month", "hour"])
        .pivot_table(index="month", columns="hour", values="pml", aggfunc="mean")
        .reset_index()
    )

    with pd.ExcelWriter(matrix_xlsx, engine="openpyxl") as writer:
        clean.to_excel(writer, sheet_name="pml_clean", index=False)
        matrix.to_excel(writer, sheet_name="12x24_avg", index=False)

    print("")
    print("Files created:")
    print(clean_csv)
    print(clean_xlsx)
    print(raw_csv)
    print(matrix_xlsx)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download CENACE PML using SW-PML.")
    parser.add_argument("--system", required=True, choices=["SIN", "BCA", "BCS"])
    parser.add_argument("--market", required=True, choices=["MDA", "MTR"])
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", default="outputs/pml")

    args = parser.parse_args()

    payload = request_sw_pml(
        args.system,
        args.market,
        args.node_id,
        args.start,
        args.end,
    )

    raw_df = normalize_sw_pml_json(payload)
    clean = clean_pml_dataframe(raw_df)
    clean = apply_requested_dates(clean, args.start, args.end)

    export_outputs(clean, raw_df, Path(args.out))

    print("")
    print("Done.")
    print(f"Rows: {len(clean)}")
    print("")
    print(clean.head(30).to_string(index=False))


if __name__ == "__main__":
    main()