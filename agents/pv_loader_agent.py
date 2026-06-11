from __future__ import annotations

from pathlib import Path
import pandas as pd


def load_pv_8760(path: str | Path, datetime_col: str | None = None, energy_col: str | None = None) -> pd.DataFrame:
    """Load a PV 8760 table from CSV/XLSX.

    Expected output columns: datetime, pv_mwh. If no datetime column exists,
    the file order is treated as hour 1..8760 for a non-leap year and can be
    aligned later to the PML table.
    """
    path = Path(path)
    df = pd.read_excel(path) if path.suffix.lower() in [".xlsx", ".xls"] else pd.read_csv(path)
    cols_lower = {str(c).lower(): c for c in df.columns}
    if energy_col is None:
        candidates = [c for k, c in cols_lower.items() if k in ["pv_mwh", "egrid", "energy", "generation", "generacion"] or "mwh" in k]
        if not candidates:
            raise ValueError("Could not find PV energy column. Pass energy_col explicitly.")
        energy_col = candidates[0]
    out = pd.DataFrame({"pv_mwh": pd.to_numeric(df[energy_col], errors="coerce").fillna(0).clip(lower=0)})
    if datetime_col is None:
        candidates = [c for k, c in cols_lower.items() if k in ["datetime", "fecha_hora", "timestamp"]]
        datetime_col = candidates[0] if candidates else None
    if datetime_col:
        out["datetime"] = pd.to_datetime(df[datetime_col], errors="coerce")
    else:
        out["hour_index"] = range(1, len(out) + 1)
    return out


def merge_pml_pv(pml: pd.DataFrame, pv: pd.DataFrame) -> pd.DataFrame:
    pml = pml.copy()
    pml["datetime"] = pd.to_datetime(pml["datetime"], errors="coerce")
    if "datetime" in pv.columns:
        pv2 = pv.copy()
        pv2["datetime"] = pd.to_datetime(pv2["datetime"], errors="coerce")
        return pml.merge(pv2[["datetime", "pv_mwh"]], on="datetime", how="left").fillna({"pv_mwh": 0})
    pv2 = pv.copy().reset_index(drop=True)
    pml = pml.sort_values("datetime").reset_index(drop=True)
    pml["pv_mwh"] = pv2["pv_mwh"].reindex(pml.index).fillna(0).values
    return pml
