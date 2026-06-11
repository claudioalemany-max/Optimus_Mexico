"""Download the official CENACE 'Catálogo de NodosP' Excel workbook.

Scrapes https://www.cenace.gob.mx/Paginas/SIM/NodosP.aspx for the newest
'Catálogo NodosP Sistema Eléctrico Nacional (vYYYY-MM-DD).xlsx' link and
saves it to data/catalogs/.

Run:
    python scripts/download_official_catalog.py
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

ROOT = Path(__file__).resolve().parents[1]
PAGE_URL = "https://www.cenace.gob.mx/Paginas/SIM/NodosP.aspx"
BASE = "https://www.cenace.gob.mx"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": PAGE_URL,
}


def find_latest_catalog_url(session: requests.Session) -> str:
    resp = session.get(PAGE_URL, timeout=120)
    resp.raise_for_status()
    links = re.findall(r'href="([^"]+\.xlsx)"', resp.text, re.I)
    catalogs = [l for l in links if re.search(r"NodosP", l, re.I)]
    if not catalogs:
        raise RuntimeError("No catalog .xlsx links found on NodosP page")

    def version_key(link: str):
        m = re.search(r"v(\d{4})-(\d{2})-(\d{2})", link)
        return tuple(map(int, m.groups())) if m else (0, 0, 0)

    latest = max(catalogs, key=version_key)
    return urljoin(BASE, quote(latest, safe="/:()-."))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(ROOT / "data" / "catalogs"))
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)
    url = find_latest_catalog_url(session)
    print(f"Latest catalog: {url}")

    resp = session.get(url, timeout=300)
    resp.raise_for_status()
    name = re.sub(r"[^\w.()\-]+", "_", url.rsplit("/", 1)[-1])
    out = Path(args.out_dir) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    print(f"Saved {out} ({len(resp.content):,} bytes)")


if __name__ == "__main__":
    main()
