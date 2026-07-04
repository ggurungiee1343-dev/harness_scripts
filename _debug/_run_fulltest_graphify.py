#!/usr/bin/env python3
"""Full test: run graphify analysis on vault markdown files and generate HTML."""
import json
import sys

from pathlib import Path

import graphify

vault = Path('/Users/bluesea/Applications/Mjobsidian')

print('[1/4] Collecting markdown files...')
# collect_files는 _DISPATCH에 있는 확장자(.md 포함)만 수집
paths = graphify.collect_files(vault)
print(f'  Found {len(paths)} extractable files in vault')
# 필터: .md 파일만 (tree-sitter markdown 파서 필요하지만 일단 시도)
md_paths = [p for p in paths if p.suffix == '.md']
print(f'  Markdown files: {len(md_paths)}')

print('[2/4] Extracting nodes and edges...')
from graphify.extract import extract as _extract
result = _extract(md_paths, parallel=False)
nodes = result.get('nodes', [])
edges = result.get('edges', [])
print(f'  Extracted {len(nodes)} nodes, {len(edges)} edges')

print('[3/4] Building graph from JSON...')
G = graphify.build_from_json(result)
print(f'  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges')

print('[4/4] Clustering communities...')
communities = graphify.cluster(G)
print(f'  Found {len(communities)} communities')

print('[5/5] Generating HTML...')
html_path = str(vault / 'graph.html')
graphify.to_html(G, communities, html_path)
print(f'  HTML saved to {html_path}')

# Top hub nodes
god = graphify.god_nodes(G, top_n=10)
print()
print('Top 10 hub nodes:')
for n in god:
    print(f'  {n["label"]} (degree={n["degree"]})')

# Isolated nodes (degree <= 1)
isolated = [n for n in G.nodes() if G.degree(n) <= 1]
print(f'\nIsolated nodes (degree<=1): {len(isolated)}')

# Summary
summary = {
    'total_files': len(md_paths),
    'nodes': G.number_of_nodes(),
    'edges': G.number_of_edges(),
    'communities': len(communities),
    'isolated': len(isolated),
}
print()
print('=== GRAPHIFY ANALYSIS COMPLETE ===')
print(json.dumps(summary))
