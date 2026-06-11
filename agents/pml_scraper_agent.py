from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import os
import time
import requests
import pandas as pd
from core.node_utils import normalize_node_code, node_hash

# CENACE SW-PML public web service endpoint pattern can change.
# Keep endpoint configurable; default below follows the common SW-PML service URL convention.
DEFAULT_SW_PML_URL = "https://ws01.cenace.gob.mx:8082/SWPML/SIM/{system}/{market}/{node}/{start}/{end}/JSON"


@dataclass
class PMLRequest:
    system: str
    market: str
    node_id: str
    start: date
    end: date
    anonymize_outputs: bool = True
    endpoint_template: str = DEFAULT_SW_PML_URL

    @property
    def node_norm(self) -> str:
        return normalize_node_code(self.node_id)

    @property
    def output_id(self) -> str:
        return node_hash(self.node_norm) if self.anonymize_outputs else self.node_norm.replace("/", "_")


def _daterange_chunks(start: date, end: date, days: int = 7):
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def _build_url(req: PMLRequest, start: date, end: date) -> str:
    return req.endpoint_template.format(
        system=req.system.upper(),
        market=req.market.upper(),
        node=req.node_norm,
        start=start.strftime("%Y/%m/%d"),
        end=end.strftime("%Y/%m/%d"),
    )


def _flatten_json(payload: object) -> pd.DataFrame:
    """Flatten typical CENACE JSON responses.

    The service has used different wrappers over time. This function searches
    for the first list of records with PML-like keys.
    """
    if isinstance(payload, list):
        return pd.json_normalize(payload)
    if not isinstance(payload, dict):
        raise ValueError("Unexpected JSON payload type")

    candidates = []
    for key, value in payload.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            candidates.append(value)
        elif isinstance(value, dict):
            for _, sub in value.items():
                if isinstance(sub, list) and sub and isinstance(sub[0], dict):
                    candidates.append(sub)
    if not candidates:
        # Empty or metadata-only response
        return pd.DataFrame()
    return pd.json_normalize(candidates[0])


def _standardize_pml_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    normalized = {c.lower().replace(" ", "_").replace(".", "_"): c for c in out.columns}

    def pick(*names: str) -> str | None:
        for name in names:
            key = name.lower().replace(" ", "_").replace(".", "_")
            if key in normalized:
                return normalized[key]
        return None

    mapping = {
        "date": pick("fecha", "date"),
        "hour": pick("hora", "hour"),
        "node": pick("clave_nodo", "clv_nodo", "clave_del_nodo", "nodo", "clavenodo"),
        "pml": pick("pml", "precio_marginal_local", "precio_marginal_local_$/mwh"),
        "energy_component": pick("pml_energia", "componente_energia", "componente_de_energia"),
        "losses_component": pick("pml_perdidas", "componente_perdidas", "componente_de_perdidas"),
        "congestion_component": pick("pml_congestion", "componente_congestion", "componente_de_congestion"),
    }
    clean = pd.DataFrame()
    for new, old in mapping.items():
        if old and old in out.columns:
            clean[new] = out[old]
        else:
            clean[new] = pd.NA

    if clean["date"].notna().any():
        clean["date"] = pd.to_datetime(clean["date"], errors="coerce").dt.date
    clean["hour"] = pd.to_numeric(clean["hour"], errors="coerce").astype("Int64")
    for c in ["pml", "energy_component", "losses_component", "congestion_component"]:
        clean[c] = pd.to_numeric(clean[c], errors="coerce")
    clean["node"] = clean["node"].fillna("").map(normalize_node_code)
    clean["datetime"] = pd.to_datetime(clean["date"].astype(str), errors="coerce") + pd.to_timedelta(clean["hour"].fillna(1).astype(int) - 1, unit="h")
    return clean[["datetime", "date", "hour", "node", "pml", "energy_component", "losses_component", "congestion_component"]]


def download_pml(req: PMLRequest, out_dir: str | Path = "outputs/pml", timeout: int = 60, pause_s: float = 0.2) -> dict[str, Path]:
    """Download CENACE PML data in chunks and write raw/clean outputs.

    If the endpoint changes, pass a new endpoint_template in PMLRequest.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_frames = []
    log_rows = []
    session = requests.Session()

    for start, end in _daterange_chunks(req.start, req.end, days=7):
        url = _build_url(req, start, end)
        status = "ok"
        error = ""
        rows = 0
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            df = _flatten_json(payload)
            rows = len(df)
            if rows:
                df["_source_url"] = url
                raw_frames.append(df)
        except Exception as exc:  # keep log and continue
            status = "error"
            error = str(exc)
        log_rows.append({"start": start, "end": end, "url": url, "status": status, "rows": rows, "error": error})
        time.sleep(pause_s)

    raw = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
    clean = _standardize_pml_columns(raw)
    clean["system"] = req.system.upper()
    clean["market"] = req.market.upper()

    ident = req.output_id
    raw_path = out_dir / f"pml_raw_{ident}.csv"
    clean_csv = out_dir / f"pml_clean_{ident}.csv"
    clean_xlsx = out_dir / f"pml_clean_{ident}.xlsx"
    matrix_xlsx = out_dir / f"pml_12x24_{ident}.xlsx"
    log_csv = out_dir / f"download_log_{ident}.csv"

    raw.to_csv(raw_path, index=False, encoding="utf-8-sig")
    clean.to_csv(clean_csv, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(clean_xlsx, engine="openpyxl") as writer:
        clean.to_excel(writer, sheet_name="PML_8760", index=False)
    if not clean.empty and clean["datetime"].notna().any():
        mat_df = clean.copy()
        mat_df["month"] = mat_df["datetime"].dt.month
        matrix = mat_df.pivot_table(index="month", columns="hour", values="pml", aggfunc="mean")
        matrix.to_excel(matrix_xlsx, sheet_name="PML_12x24")
    pd.DataFrame(log_rows).to_csv(log_csv, index=False, encoding="utf-8-sig")

    return {"raw_csv": raw_path, "clean_csv": clean_csv, "clean_xlsx": clean_xlsx, "matrix_xlsx": matrix_xlsx, "log_csv": log_csv}


def request_from_env(system: str, market: str, start: str, end: str, node_id: str | None = None) -> PMLRequest:
    node = node_id or os.getenv("CENACE_NODE_ID")
    if not node:
        raise ValueError("Node missing. Pass --node-id or set CENACE_NODE_ID in the environment.")
    return PMLRequest(
        system=system,
        market=market,
        node_id=node,
        start=datetime.strptime(start, "%Y-%m-%d").date(),
        end=datetime.strptime(end, "%Y-%m-%d").date(),
    )
