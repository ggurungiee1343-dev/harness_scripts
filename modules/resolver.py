from typing import Dict, List, Optional


class Resolver:
    def resolve(self, candidates: List[Dict], priority: List[str] = None) -> Optional[str]:
        priority = priority or ["vault", "web", "llm"]
        for source in priority:
            for cand in candidates:
                if cand.get("source") == source:
                    return cand.get("content")
        return candidates[0].get("content") if candidates else None
