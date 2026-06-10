class CascadeEngine:
    def __init__(self, ontology, graphify_engine=None):
        self.ontology = ontology
        self.graphify = graphify_engine

    def on_file_change(self, file_path: str):
        affected = self.ontology.get_affected(file_path, hops=2)
        updated = []
        for target in affected:
            if self.graphify and hasattr(self.graphify, "update"):
                self.graphify.update(target, source=file_path)
            updated.append(target)
        return updated
