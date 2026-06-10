import json
import os
from collections import deque
from typing import Dict, List, Tuple


class OntologyGraph:
    def __init__(self, path: str = "~/.hermes/runtime/ontology_graph.json"):
        self.path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.graph: Dict[str, List[Tuple[str, str]]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.graph = {k: [tuple(x) for x in v] for k, v in raw.items()}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.graph, f, indent=2, ensure_ascii=False)

    def add_dependency(self, from_file: str, to_file: str, relation: str = "depends_on"):
        self.graph.setdefault(from_file, []).append((to_file, relation))
        self._save()

    def get_neighbors(self, node: str, hops: int = 1) -> List[str]:
        affected = []
        seen = {node}
        q = deque([(node, 0)])
        while q:
            cur, depth = q.popleft()
            if depth >= hops:
                continue
            for neighbor, _ in self.graph.get(cur, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    affected.append(neighbor)
                    q.append((neighbor, depth + 1))
        return affected

    def get_affected(self, changed_file: str, hops: int = 2) -> List[str]:
        return self.get_neighbors(changed_file, hops=hops)
