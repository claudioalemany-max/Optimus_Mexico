# scripts/debug_sw_pml_8082.py
"""
Optimus_Mexico - Debug CENACE SW-PML on port 8082

Purpose:
- Test multiple likely CENACE SW-PML URL patterns.
- Include node-specific and all-node variants.
- Test HTTPS with SSL verification disabled for ws01 certificate mismatch.
- Test HTTP as fallback.
- Save every response and a summary CSV.

Run:
    python scripts\\debug_sw_pml_8082.py --system SIN --market MTR --node-id 01AAN-85 --date 2026-05-14

Also test MDA:
    python scripts\\debug_sw_pml_8082.py --system SIN --market MDA --node-id 01AAN-85 --date 2024-03-23

Inspect successful responses:
    python -c "import pandas as pd; df=pd.read_csv('outputs/debug/sw_pml_8082/summary.csv'); print(df[(df['status'].astype(str)=='200')][['i','status','bytes','url','preview']].to_string(index=False))"
"""

from __future__ import annotations

import argparse
import urllib3
from pathlib import Path

import requests


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/xml,text/xml,application/json,text/plain,text/html,*/*",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


def date_parts(date: str) -> tuple[str, str, str]:
    """
    Convert YYYY-MM-DD into year, month, day strings.
    """
    parts = date.split("-")
    if len(parts) != 3:
        raise ValueError("Date must use YYYY-MM-DD format.")
    y, m, d = parts
    return y, m, d


def build_candidates(system: str, market: str, node_id: str, date: str) -> list[str]:
    """
    Build a broad set of likely SW-PML URL candidates.

    We test:
    - HTTPS and HTTP
    - node-specific variants
    - all-node variants: ALL, TODOS, SISTEMA, system name
    - date as yyyy/mm/dd and compact yyyy-mm-dd
    - XML, CSV, JSON outputs
    """
    system = system.upper()
    market = market.upper()
    node_id = node_id.upper()
    y, m, d = date_parts(date)

    bases = [
        "https://ws01.cenace.gob.mx:8082/SWPML/SIM",
        "http://ws01.cenace.gob.mx:8082/SWPML/SIM",
        "https://www.cenace.gob.mx:8082/SWPML/SIM",
        "http://www.cenace.gob.mx:8082/SWPML/SIM",
    ]

    node_params = [
        node_id,
        "ALL",
        "TODOS",
        "SISTEMA",
        system,
    ]

    output_formats = ["XML", "CSV", "JSON"]

    candidates: list[str] = []

    for base in bases:
        for node_param in node_params:
            for fmt in output_formats:
                candidates.extend(
                    [
                        # Most likely CENACE service style:
                        # /Sistema/Mercado/Nodos/FechaIni/FechaFin/Formato
                        f"{base}/{system}/{market}/{node_param}/{y}/{m}/{d}/{y}/{m}/{d}/{fmt}",

                        # Without node parameter:
                        f"{base}/{system}/{market}/{y}/{m}/{d}/{y}/{m}/{d}/{fmt}",

                        # Node parameter after dates:
                        f"{base}/{system}/{market}/{y}/{m}/{d}/{y}/{m}/{d}/{node_param}/{fmt}",

                        # Compact date variants:
                        f"{base}/{system}/{market}/{node_param}/{date}/{date}/{fmt}",
                        f"{base}/{system}/{market}/{date}/{date}/{node_param}/{fmt}",

                        # Alternative process/system order:
                        f"{base}/{market}/{system}/{node_param}/{y}/{m}/{d}/{y}/{m}/{d}/{fmt}",
                        f"{base}/{market}/{system}/{y}/{m}/{d}/{y}/{m}/{d}/{node_param}/{fmt}",

                        # Possible service method keywords:
                        f"{base}/PML/{system}/{market}/{node_param}/{y}/{m}/{d}/{y}/{m}/{d}/{fmt}",
                        f"{base}/PML/{system}/{market}/{y}/{m}/{d}/{y}/{m}/{d}/{node_param}/{fmt}",
                        f"{base}/PrecioMarginalLocal/{system}/{market}/{node_param}/{y}/{m}/{d}/{y}/{m}/{d}/{fmt}",
                    ]
                )

    # Query-string variants as fallback.
    query_bases = [
        "https://ws01.cenace.gob.mx:8082/SWPML/SIM",
        "http://ws01.cenace.gob.mx:8082/SWPML/SIM",
        "https://ws01.cenace.gob.mx:8082/SWPML/SIM/SWPML",
        "http://ws01.cenace.gob.mx:8082/SWPML/SIM/SWPML",
        "https://ws01.cenace.gob.mx:8082/SWPML/SIM/SW_PML",
        "http://ws01.cenace.gob.mx:8082/SWPML/SIM/SW_PML",
    ]

    query_variants = [
        f"Sistema={system}&Mercado={market}&NodoP={node_id}&FechaIni={date}&FechaFin={date}&Formato=XML",
        f"sistema={system}&mercado={market}&nodo={node_id}&fecha_ini={date}&fecha_fin={date}&formato=XML",
        f"Sistema={system}&Mercado={market}&Nodos={node_id}&FechaInicio={date}&FechaFin={date}&Formato=XML",
        f"sistema={system}&proceso={market}&nodos={node_id}&fechaIni={date}&fechaFin={date}&formato=XML",
        f"Sistema={system}&Mercado={market}&NodoP={node_id}&Anio={y}&Mes={m}&Dia={d}&Formato=XML",
    ]

    for base in query_bases:
        for q in query_variants:
            candidates.append(f"{base}?{q}")

    # Remove duplicates while preserving order.
    seen = set()
    unique = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    return unique


def response_suffix(content_type: str, text_preview: str) -> str:
    ctype = content_type.lower()
    preview = text_preview.strip().lower()

    if "xml" in ctype or preview.startswith("<"):
        return "xml"
    if "json" in ctype or preview.startswith("{") or preview.startswith("["):
        return "json"
    if "csv" in ctype:
        return "csv"
    if "html" in ctype or "<html" in preview:
        return "html"
    if "," in text_preview[:500] or ";" in text_preview[:500]:
        return "csv"
    return "txt"


def request_url(session: requests.Session, url: str) -> requests.Response:
    """
    Request URL. Disable SSL verification for ws01 HTTPS due to known
    certificate hostname mismatch during debugging.
    """
    verify_ssl = True

    if url.startswith("https://ws01.cenace.gob.mx"):
        verify_ssl = False

    return session.get(url, timeout=90, verify=verify_ssl)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, help="SIN, BCA, or BCS")
    parser.add_argument("--market", required=True, help="MDA or MTR")
    parser.add_argument("--node-id", required=True, help="Example: 01AAN-85")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    out_dir = Path("outputs/debug/sw_pml_8082")
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    urls = build_candidates(args.system, args.market, args.node_id, args.date)

    rows = []

    print(f"Testing {len(urls)} SW-PML candidates...", flush=True)

    for i, url in enumerate(urls, start=1):
        print("")
        print(f"[{i}/{len(urls)}] {url}", flush=True)

        try:
            r = request_url(session, url)
            ctype = r.headers.get("content-type", "")
            size = len(r.content)

            try:
                preview = r.text[:500].replace("\n", " ").replace("\r", " ")
            except Exception:
                preview = str(r.content[:500])

            print("HTTP:", r.status_code, "| bytes:", size, "| type:", ctype, flush=True)
            print("Preview:", preview[:250], flush=True)

            suffix = response_suffix(ctype, preview)
            file_path = out_dir / f"response_{i:03d}_{r.status_code}.{suffix}"
            file_path.write_bytes(r.content)

            rows.append(
                {
                    "i": i,
                    "status": r.status_code,
                    "bytes": size,
                    "content_type": ctype,
                    "url": url,
                    "file": str(file_path),
                    "preview": preview,
                }
            )

        except Exception as exc:
            print("ERROR:", exc, flush=True)

            rows.append(
                {
                    "i": i,
                    "status": "ERROR",
                    "bytes": 0,
                    "content_type": "",
                    "url": url,
                    "file": "",
                    "preview": str(exc),
                }
            )

    summary_path = out_dir / "summary.csv"

    with summary_path.open("w", encoding="utf-8-sig") as f:
        f.write("i,status,bytes,content_type,url,file,preview\n")

        for row in rows:
            def esc(x):
                return '"' + str(x).replace('"', '""') + '"'

            f.write(
                ",".join(
                    [
                        esc(row["i"]),
                        esc(row["status"]),
                        esc(row["bytes"]),
                        esc(row["content_type"]),
                        esc(row["url"]),
                        esc(row["file"]),
                        esc(row["preview"]),
                    ]
                )
                + "\n"
            )

    print("")
    print("Done.", flush=True)
    print("Summary:", summary_path, flush=True)

    # Print useful hits immediately.
    useful = [
        row
        for row in rows
        if str(row["status"]) == "200" and int(row["bytes"]) > 1000
    ]

    if useful:
        print("")
        print("Potential useful responses:", flush=True)
        for row in useful[:20]:
            print(
                f'i={row["i"]} | bytes={row["bytes"]} | url={row["url"]} | preview={row["preview"][:150]}',
                flush=True,
            )
    else:
        print("")
        print("No 200 responses larger than 1000 bytes found.", flush=True)


if __name__ == "__main__":
    main()