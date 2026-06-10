from typing import Any, Dict, List


class HyDESearch:
    def __init__(self, llm_interface, vector_db):
        self.llm = llm_interface
        self.vector_db = vector_db

    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        hypo_prompt = f"Write a hypothetical document that would answer the following query:\n{query}"
        hypo_doc = await self.llm.complete(hypo_prompt, max_tokens=300)
        if hasattr(self.vector_db, "similarity_search"):
            return self.vector_db.similarity_search(hypo_doc, top_k)
        raise AttributeError("vector_db.similarity_search is required")
