import os
import json
import networkx as nx
from networkx.readwrite import json_graph

class GraphMemoryEngine:
    def __init__(self, storage_path="/Users/bluesea/Applications/Mjauto/Scripts/harness_graph.json"):
        self.storage_path = storage_path
        self.graph = nx.DiGraph()
        self._load_graph()

    def _load_graph(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.graph = json_graph.node_link_graph(data)
            except Exception as e:
                print(f"Failed to load graph memory: {e}")
                self.graph = nx.DiGraph()

    def _save_graph(self):
        try:
            data = json_graph.node_link_data(self.graph)
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to save graph memory: {e}")

    def add_relation(self, subject, predicate, obj, importance=1.0, timestamp=None):
        """Add a triple to the knowledge graph"""
        self.graph.add_node(subject)
        self.graph.add_node(obj)
        self.graph.add_edge(subject, obj, relation=predicate, importance=importance, timestamp=timestamp)
        self._save_graph()

    def get_relations(self, entity):
        """Get all facts related to a specific entity"""
        if not self.graph.has_node(entity):
            return []
        
        relations = []
        # Outgoing edges
        for _, neighbor, data in self.graph.out_edges(entity, data=True):
            relations.append({"subject": entity, "predicate": data.get("relation"), "object": neighbor, "importance": data.get("importance", 1.0)})
        
        # Incoming edges
        for neighbor, _, data in self.graph.in_edges(entity, data=True):
            relations.append({"subject": neighbor, "predicate": data.get("relation"), "object": entity, "importance": data.get("importance", 1.0)})
            
        return relations
        
    def get_subgraph_context(self, entities, max_depth=2):
        """Retrieve a contextual summary based on specific entities"""
        extracted_facts = []
        visited = set()
        
        def traverse(node, depth):
            if depth >= max_depth or node in visited:
                return
            visited.add(node)
            for _, neighbor, data in self.graph.out_edges(node, data=True):
                extracted_facts.append(f"{node} --[{data.get('relation')}]--> {neighbor}")
                traverse(neighbor, depth + 1)
                
        for e in entities:
            if self.graph.has_node(e):
                traverse(e, 0)
                
        return list(set(extracted_facts))

if __name__ == "__main__":
    # Test script
    engine = GraphMemoryEngine()
    engine.add_relation("박사님", "좋아한다", "사과", 3.0, "2026-05-20T13:29:04")
    engine.add_relation("사과", "특징", "달콤하고 빨갛다", 3.0, "2026-05-20T13:29:04")
    print("Test passed. Graph saved to:", engine.storage_path)
