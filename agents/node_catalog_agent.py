from __future__ import annotations

from pathlib import Path
import pandas as pd
from core.node_utils import clean_column_name, normalize_node_code

CATALOG_COLUMN_ALIASES = {
    "sistema": ["sistema"],
    "num_zc": ["num_zc", "numero_zc", "num_zona_carga"],
    "zona_de_carga": ["zona_de_carga", "zona_carga", "zona"],
    "clave_nodo_p": ["clave_nodo_p", "clave", "clave_del_nodo", "clave_nodo", "nodo"],
    "voltaje": ["voltaje", "nivel_de_tension_kv", "nivel_tension_kv", "tension"],
    "tipo": ["tipo", "tipo_de_nodo", "directamente_modelada", "indirectamente_modelada"],
    "gerencia_regional": ["gerencia_regional", "centro_de_control_regional", "regional", "gerencia"],
    "entidad_federativa": ["entidad_federativa", "estado", "entidad"],
    "municipio": ["municipio", "alcaldia", "localidad"],
    "lat": ["lat", "latitude", "latitud"],
    "lon": ["lon", "lng", "longitude", "longitud"],
}


def _find_header_row(raw: pd.DataFrame) -> int:
    for i in range(min(40, len(raw))):
        vals = [clean_column_name(v) for v in raw.iloc[i].tolist()]
        if any(v in vals for v in ["clave_nodo_p", "clave_nodo", "clave_del_nodo", "nodo"]):
            return i
    return 0


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [clean_column_name(c) for c in df.columns]
    rename: dict[str, str] = {}
    for target, aliases in CATALOG_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in df.columns:
                rename[alias] = target
                break
    return df.rename(columns=rename)


def load_catalog(path: str | Path) -> pd.DataFrame:
    """Load a manually downloaded CENACE Catálogo de NodosP file.

    Supports CSV/XLSX. The function is intentionally flexible because CENACE
    workbook formats can change sheet names and header-row positions.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    if path.suffix.lower() in [".xlsx", ".xls"]:
        xls = pd.ExcelFile(path)
        chosen = None
        for sheet in xls.sheet_names:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_row = _find_header_row(raw)
            df = pd.read_excel(path, sheet_name=sheet, header=header_row)
            df = _standardize_columns(df)
            if "clave_nodo_p" in df.columns:
                chosen = df
                break
        if chosen is None:
            raise ValueError("Could not find a sheet with a recognizable Clave NodoP column.")
        df = chosen
    else:
        df = _standardize_columns(pd.read_csv(path))

    if "clave_nodo_p" not in df.columns:
        candidates = [c for c in df.columns if "clave" in c and "nodo" in c]
        if not candidates:
            raise ValueError("Catalog does not contain a recognizable node code column.")
        df = df.rename(columns={candidates[0]: "clave_nodo_p"})

    df = df.dropna(subset=["clave_nodo_p"]).copy()
    df["clave_nodo_p"] = df["clave_nodo_p"].astype(str).str.strip().str.upper()
    df["clave_nodo_norm"] = df["clave_nodo_p"].map(normalize_node_code)
    df = df.drop_duplicates(subset=["clave_nodo_norm"], keep="first")
    return df


def export_normalized_catalog(catalog_path: str | Path, out_path: str | Path) -> Path:
    df = load_catalog(catalog_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() == ".xlsx":
        df.to_excel(out_path, index=False)
    else:
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path
