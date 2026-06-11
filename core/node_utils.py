from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

# CENACE node keys usually look like 01AAN-85, 01ACO-230, 08CHB-34.5
# Some catalog/report variants may omit the dash.
NODE_RE = re.compile(r"\b\d{2}[A-Z0-9]{3,6}(?:-?(?:400|230|161|138|115|85|69|34\.5|345|34))\b")
KNOWN_VOLTAGES = ["400", "230", "161", "138", "115", "85", "69", "34.5", "345", "34"]


def strip_accents(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def clean_column_name(name: object) -> str:
    text = strip_accents(name).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_node_code(code: object) -> str:
    """Normalize a CENACE Clave NodoP while preserving voltage.

    Examples:
    - 01AAN-85 -> 01AAN-85
    - 01ACO230 -> 01ACO-230
    - 03MRA1115 -> 03MRA1-115
    - 08CHB345 -> 08CHB-34.5 when intended as 34.5 kV
    """
    text = strip_accents(code).strip().upper().replace(" ", "")
    if not text:
        return ""
    text = text.replace("_", "-")
    if "-" in text:
        left, right = text.split("-", 1)
        if right == "345":
            right = "34.5"
        return f"{left}-{right}"

    # Prefer long/high voltage suffixes before low voltage suffixes.
    for suffix in ["400", "230", "161", "138", "115", "85", "69", "345", "34"]:
        if text.endswith(suffix) and len(text) > len(suffix) + 2:
            left = text[: -len(suffix)]
            volt = "34.5" if suffix == "345" else suffix
            return f"{left}-{volt}"
    return text


def node_hash(code: object, length: int = 10) -> str:
    return hashlib.sha256(normalize_node_code(code).encode("utf-8")).hexdigest()[:length]


def extract_codes_from_text(text: str) -> list[str]:
    return sorted({normalize_node_code(match.group(0)) for match in NODE_RE.finditer(text or "")})


def extract_voltage(code: object) -> str | None:
    norm = normalize_node_code(code)
    if "-" not in norm:
        return None
    return norm.rsplit("-", 1)[1]


def unique_normalized(codes: Iterable[object]) -> list[str]:
    return sorted({normalize_node_code(c) for c in codes if normalize_node_code(c)})
