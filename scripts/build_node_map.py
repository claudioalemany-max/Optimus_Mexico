from __future__ import annotations
import argparse
from agents.node_map_agent import build_node_map

parser = argparse.ArgumentParser(description="Build an HTML map from resolved nodes with lat/lon.")
parser.add_argument("--resolved", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

out = build_node_map(args.resolved, args.out)
print(f"Map written -> {out}")
