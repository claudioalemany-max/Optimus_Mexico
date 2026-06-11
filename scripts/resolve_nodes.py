from __future__ import annotations
import argparse
from pathlib import Path
from agents.node_resolver_agent import extract_nodes_from_table, resolve_nodes, write_resolution_workbook

parser = argparse.ArgumentParser(description="Resolve node codes using CENACE Catálogo de NodosP.")
parser.add_argument("--nodes", required=True, help="CSV/XLSX containing Clave NodoP values")
parser.add_argument("--catalog", required=True, help="CENACE Catálogo de NodosP CSV/XLSX")
parser.add_argument("--out", required=True)
args = parser.parse_args()

nodes = extract_nodes_from_table(args.nodes)
resolved = resolve_nodes(nodes, args.catalog)
write_resolution_workbook(resolved, args.out)
print(f"Matched {(resolved['resolution_status']=='matched').sum()} / {len(resolved)} nodes -> {args.out}")
