# scripts/debug_nodos2_tables.py

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests


URL = "https://www.cenace.gob.mx/Nodos2.aspx"

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


def main() -> None:
    print("Downloading Nodos2.aspx...")
    out_dir = Path("outputs/debug")
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    response = session.get(URL, timeout=120)
    print("HTTP status:", response.status_code)
    response.raise_for_status()

    html_path = out_dir / "nodos2_page.html"
    html_path.write_text(response.text, encoding="utf-8")
    print("Saved HTML:", html_path)

    print("Reading HTML tables...")
    tables = pd.read_html(io.StringIO(response.text))
    print("Tables found:", len(tables))

    summary_rows = []

    for i, table in enumerate(tables):
        csv_path = out_dir / f"nodos2_table_{i}.csv"
        xlsx_path = out_dir / f"nodos2_table_{i}.xlsx"

        table.to_csv(csv_path, index=False, encoding="utf-8-sig")
        table.to_excel(xlsx_path, index=False)

        print("")
        print("=" * 80)
        print(f"TABLE {i}")
        print("Shape:", table.shape)
        print("Columns:", list(table.columns))
        print(table.head(10).to_string(index=False))

        summary_rows.append(
            {
                "table_number": i,
                "rows": table.shape[0],
                "columns": table.shape[1],
                "column_names": " | ".join([str(c) for c in table.columns]),
                "csv_file": str(csv_path),
                "xlsx_file": str(xlsx_path),
            }
        )

    summary = pd.DataFrame(summary_rows)
    summary_path = out_dir / "nodos2_tables_summary.xlsx"
    summary.to_excel(summary_path, index=False)

    print("")
    print("Done.")
    print("Summary saved:", summary_path)


if __name__ == "__main__":
    main()