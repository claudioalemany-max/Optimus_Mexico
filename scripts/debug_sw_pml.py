# scripts/debug_sw_pml.py
"""
Optimus_Mexico - Debug CENACE SW-PML endpoint

Purpose:
- Test likely official SW-PML endpoint patterns.
- Save all responses to outputs/debug/sw_pml/
- Identify the working service URL and parameter format.

Run:
    python scripts\\debug_sw_pml.py --system SIN --market MTR --node-id 01AAN-85 --date 2026-05-14
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlencode

import requests


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,text/html,application/xml,*/*",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Referer": "https://www.cenace.gob.mx/Paginas/SIM/Reportes/PreEnerServConMTR.aspx",
}


def build_candidates(system: str, market: str, node_id: str, date: str) -> list[str]:
    """
    Try multiple known/likely CENACE SW-PML service patterns.

    We test query-style and path-style endpoints because CENACE has changed
    public service URLs over time.
    """
    system = system.upper()
    market = market.upper()
    node_id = node_id.upper()

    params_sets = [
        {
            "sistema": system,
            "mercado": market,
            "nodo": node_id,
            "fecha": date,
        },
        {
            "Sistema": system,
            "Mercado": market,
            "Nodo": node_id,
            "Fecha": date,
        },
        {
            "sistema": system,
            "proceso": market,
            "nodo": node_id,
            "fecha": date,
        },
        {
            "sistema": system,
            "mercado": market,
            "nodos": node_id,
            "fecha_ini": date,
            "fecha_fin": date,
        },
        {
            "Sistema": system,
            "Mercado": market,
            "NodoP": node_id,
            "FechaIni": date,
            "FechaFin": date,
        },
    ]

    base_urls = [
        "https://www.cenace.gob.mx/SWPML/SIM/SW_PML",
        "https://www.cenace.gob.mx/SWPML/SIM/SWPML",
        "https://www.cenace.gob.mx/SWPML/SW_PML",
        "https://www.cenace.gob.mx/SWPML/SWPML",
        "https://www.cenace.gob.mx/SIM/SWPML",
        "https://www.cenace.gob.mx/SIM/SW_PML",
        "https://ws01.cenace.gob.mx/SWPML/SIM/SW_PML",
        "https://ws01.cenace.gob.mx/SWPML/SIM/SWPML",
    ]

    candidates = []

    for base in base_urls:
        for params in params_sets:
            candidates.append(f"{base}?{urlencode(params)}")

    # Path-style guesses.
    path_bases = [
        "https://www.cenace.gob.mx/SWPML/SIM",
        "https://ws01.cenace.gob.mx/SWPML/SIM",
    ]

    for base in path_bases:
        candidates.extend(
            [
                f"{base}/{system}/{market}/{node_id}/{date}",
                f"{base}/{market}/{system}/{node_id}/{date}",
                f"{base}/{system}/{market}/{date}/{node_id}",
            ]
        )

    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True)
    parser.add_argument("--market", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()

    out_dir = Path("outputs/debug/sw_pml")
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    candidates = build_candidates(
        args.system,
        args.market,
        args.node_id,
        args.date,
    )

    summary_rows = []

    print(f"Testing {len(candidates)} candidate SW-PML URLs...")

    for i, url in enumerate(candidates, start=1):
        print("")
        print(f"[{i}/{len(candidates)}] {url}")

        try:
            r = session.get(url, timeout=60)
            content_type = r.headers.get("content-type", "")
            content_len = len(r.content)

            print("HTTP:", r.status_code, "| type:", content_type, "| bytes:", content_len)

            suffix = "txt"
            if "json" in content_type.lower():
                suffix = "json"
            elif "xml" in content_type.lower():
                suffix = "xml"
            elif "csv" in content_type.lower():
                suffix = "csv"
            elif "html" in content_type.lower():
                suffix = "html"

            out_file = out_dir / f"response_{i:03d}_{r.status_code}.{suffix}"
            out_file.write_bytes(r.content)

            preview = r.text[:300].replace("\n", " ").replace("\r", " ")

            summary_rows.append(
                {
                    "i": i,
                    "status": r.status_code,
                    "content_type": content_type,
                    "bytes": content_len,
                    "url": url,
                    "file": str(out_file),
                    "preview": preview,
                }
            )

        except Exception as exc:
            print("ERROR:", exc)
            summary_rows.append(
                {
                    "i": i,
                    "status": "ERROR",
                    "content_type": "",
                    "bytes": 0,
                    "url": url,
                    "file": "",
                    "preview": str(exc),
                }
            )

    # Save summary without requiring pandas.
    summary_path = out_dir / "summary.csv"
    with summary_path.open("w", encoding="utf-8-sig") as f:
        f.write("i,status,content_type,bytes,url,file,preview\n")
        for row in summary_rows:
            def esc(x):
                return '"' + str(x).replace('"', '""') + '"'
            f.write(
                ",".join(
                    [
                        esc(row["i"]),
                        esc(row["status"]),
                        esc(row["content_type"]),
                        esc(row["bytes"]),
                        esc(row["url"]),
                        esc(row["file"]),
                        esc(row["preview"]),
                    ]
                )
                + "\n"
            )

    print("")
    print("Done.")
    print("Summary:", summary_path)


if __name__ == "__main__":
    main()