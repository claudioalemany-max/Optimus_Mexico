# scripts/debug_pml_page.py

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup


URL = "https://www.cenace.gob.mx/Paginas/SIM/Reportes/PreEnerServConMTR.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}


def main() -> None:
    out_dir = Path("outputs/debug")
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    print("Downloading:", URL)
    r = session.get(URL, timeout=120)
    print("HTTP:", r.status_code)
    r.raise_for_status()

    html_path = out_dir / "pml_page.html"
    html_path.write_text(r.text, encoding="utf-8")
    print("Saved:", html_path)

    soup = BeautifulSoup(r.text, "html.parser")

    links = []
    for a in soup.find_all("a"):
        href = a.get("href")
        text = " ".join(a.get_text(" ", strip=True).split())
        if not href:
            continue
        full = urljoin(URL, href)
        if any(x in full.lower() for x in ["csv", "xls", "xlsx", "pdf", "precios", "marg"]):
            links.append({"text": text, "href": full})

    df_links = pd.DataFrame(links).drop_duplicates()
    links_path = out_dir / "pml_page_links.csv"
    df_links.to_csv(links_path, index=False, encoding="utf-8-sig")
    print("Links saved:", links_path)
    print(df_links.head(50).to_string(index=False))

    # Also save scripts because ASP.NET pages sometimes hide endpoints in JS.
    scripts = []
    for script in soup.find_all("script"):
        txt = script.get_text("\n", strip=True)
        src = script.get("src")
        if src:
            scripts.append({"type": "src", "content": urljoin(URL, src)})
        elif txt:
            scripts.append({"type": "inline", "content": txt[:5000]})

    scripts_df = pd.DataFrame(scripts)
    scripts_path = out_dir / "pml_page_scripts.csv"
    scripts_df.to_csv(scripts_path, index=False, encoding="utf-8-sig")
    print("Scripts saved:", scripts_path)

    # Search raw HTML for useful paths.
    patterns = [
        r"DocsMEM[^\"']+",
        r"PreciosMargLocales[^\"']+",
        r"SWPML[^\"']+",
        r"ws[^\"']+PML[^\"']+",
        r"ashx[^\"']+",
        r"asmx[^\"']+",
    ]

    hits = []
    for pat in patterns:
        for m in re.finditer(pat, r.text, flags=re.IGNORECASE):
            hits.append({"pattern": pat, "hit": m.group(0)[:500]})

    hits_df = pd.DataFrame(hits)
    hits_path = out_dir / "pml_page_path_hits.csv"
    hits_df.to_csv(hits_path, index=False, encoding="utf-8-sig")
    print("Path hits saved:", hits_path)

    print("")
    print("Done. Check outputs/debug.")


if __name__ == "__main__":
    main()