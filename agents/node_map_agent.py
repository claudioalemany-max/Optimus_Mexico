from __future__ import annotations

from pathlib import Path
import pandas as pd
import folium


def _load(path: str | Path, sheet_name: str | int | None = 0) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in [".xlsx", ".xls"]:
        return pd.read_excel(path, sheet_name=sheet_name)
    return pd.read_csv(path)


def build_node_map(resolved_path: str | Path, out_html: str | Path, sheet_name: str | int | None = 0) -> Path:
    """Build an HTML map from resolved node data.

    Requires lat/lon columns. If the official catalog does not include exact
    coordinates, join municipality/state centroids before calling this function.
    """
    df = _load(resolved_path, sheet_name)
    lower = {str(c).lower(): c for c in df.columns}
    lat_col = lower.get("lat") or lower.get("latitude") or lower.get("latitud")
    lon_col = lower.get("lon") or lower.get("lng") or lower.get("longitude") or lower.get("longitud")
    if not lat_col or not lon_col:
        raise ValueError("No lat/lon columns found. Add exact coordinates or municipality centroids first.")

    m = folium.Map(location=[23.6, -102.5], zoom_start=5, tiles="CartoDB positron")
    for _, row in df.dropna(subset=[lat_col, lon_col]).iterrows():
        node = row.get("clave_nodo_original", row.get("clave_nodo_p", "node"))
        popup = (
            f"<b>{node}</b><br>"
            f"Zona: {row.get('zona_de_carga', '')}<br>"
            f"Estado: {row.get('entidad_federativa', '')}<br>"
            f"Municipio: {row.get('municipio', '')}<br>"
            f"Voltaje: {row.get('voltaje', '')}"
        )
        folium.CircleMarker(
            location=[float(row[lat_col]), float(row[lon_col])],
            radius=4,
            popup=folium.Popup(popup, max_width=320),
            fill=True,
        ).add_to(m)

    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    m.save(out_html)
    return out_html
