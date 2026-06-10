# modules/context_manager.py
# Hermes3 Phase 2 - 컨텍스트 스냅샷 + 동적 로딩
import os, sqlite3
from datetime import datetime
from typing import List, Optional


class ContextManager:
    SNAPSHOT_PATH = os.path.expanduser("~/.hermes/context_snapshot.md")
    DB_PATH = os.path.expanduser("~/.hermes/runtime/hermes_index.db")

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.DB_PATH

    def build_snapshot(
        self,
        current_task: str,
        relevant_entities: Optional[List[str]] = None
    ) -> str:
        """현재 작업 기반 컨텍스트 스냅샷 생성 → context_snapshot.md"""
        snapshot = f"# Context Snapshot\n_생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n"
        snapshot += f"## Current Task\n{current_task}\n\n"

        # 관련 엔티티 (온톨로지 미구현 시 단순 키워드 나열)
        if relevant_entities:
            snapshot += "## Related Concepts\n"
            for ent in relevant_entities:
                snapshot += f"- {ent}\n"
            snapshot += "\n"

        # 최근 메모리 (DB에서 로드)
        memories = self._load_recent_memories(current_task, top_k=5)
        if memories:
            snapshot += "## Recent Memories\n"
            for mem in memories:
                snapshot += f"- {mem}\n"
            snapshot += "\n"

        # 최근 논문 태그 (papers 테이블)
        papers = self._load_related_papers(current_task, top_k=3)
        if papers:
            snapshot += "## Related Papers\n"
            for p in papers:
                snapshot += f"- {p}\n"

        # 파일 저장
        os.makedirs(os.path.dirname(self.SNAPSHOT_PATH), exist_ok=True)
        with open(self.SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            f.write(snapshot)

        return snapshot

    def lazy_load(self, max_chars: int = 3000) -> str:
        """
        컨텍스트를 우선순위 점수 기반으로 max_chars 이내로 동적 로드.
        우선순위 점수 = relevance * recency * importance
        """
        if not os.path.exists(self.SNAPSHOT_PATH):
            return ""

        with open(self.SNAPSHOT_PATH, encoding="utf-8") as f:
            content = f.read()

        if len(content) <= max_chars:
            return content

        # 섹션별 분리 후 우선순위 순으로 잘라냄
        sections = content.split("## ")
        priority_order = ["Current Task", "Recent Memories", "Related Concepts", "Related Papers"]
        ordered = []
        for prio in priority_order:
            for sec in sections:
                if sec.startswith(prio):
                    ordered.append("## " + sec)
                    break

        result = ""
        for sec in ordered:
            if len(result) + len(sec) <= max_chars:
                result += sec
            else:
                # 잘라서 추가
                remaining = max_chars - len(result)
                if remaining > 100:
                    result += sec[:remaining] + "\n...(생략)\n"
                break
        return result

    def _load_recent_memories(self, query: str, top_k: int = 5) -> List[str]:
        """DB memories 테이블에서 최근 기억 로드"""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT content FROM memories ORDER BY timestamp DESC LIMIT ?", (top_k,)
            ).fetchall()
            conn.close()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []

    def _load_related_papers(self, query: str, top_k: int = 3) -> List[str]:
        """DB papers 테이블에서 관련 논문 로드"""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT title FROM papers ORDER BY rowid DESC LIMIT ?", (top_k,)
            ).fetchall()
            conn.close()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []
