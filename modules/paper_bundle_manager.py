# modules/paper_bundle_manager.py
# Hermes3 Phase 2 - 논문 번들 상태 관리
import os, sqlite3, json, zipfile
from datetime import datetime
from typing import List, Dict, Optional


class PaperBundleManager:
    DB_PATH = os.path.expanduser("~/.hermes/runtime/hermes_index.db")
    BUNDLE_DIR = os.path.expanduser("~/.hermes/runtime/bundles")

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.DB_PATH
        os.makedirs(self.BUNDLE_DIR, exist_ok=True)
        self._ensure_tables()

    def create_bundle(self, name: str, paper_ids: List[int]) -> int:
        """새 번들 생성"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "INSERT INTO bundles (name, created_at) VALUES (?, datetime('now'))", (name,)
        )
        bundle_id = cursor.lastrowid
        for pid in paper_ids:
            conn.execute(
                "INSERT INTO bundle_papers (bundle_id, paper_id) VALUES (?,?)",
                (bundle_id, pid),
            )
        conn.commit()
        conn.close()
        return bundle_id

    def list_bundles(self) -> List[Dict]:
        """전체 번들 목록"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT id, name, created_at FROM bundles ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows]

    def get_bundle_papers(self, bundle_id: int) -> List[Dict]:
        """번들에 포함된 논문 목록 (formal_date 포함)"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT p.id, p.title, p.authors, p.published, p.formal_date
               FROM papers p
               JOIN bundle_papers bp ON p.id = bp.paper_id
               WHERE bp.bundle_id = ?""",
            (bundle_id,),
        ).fetchall()
        conn.close()
        return [{"id": r[0], "title": r[1], "authors": r[2], "published": r[3],
                 "formal_date": r[4]} for r in rows]

    def compare_claims(self, bundle_id: int, llm_func=None) -> Dict:
        """
        번들 내 논문들의 abstract 비교.
        llm_func 있으면 LLM으로 클레임 분석, 없으면 abstract 텍스트만 반환.
        """
        papers = self.get_bundle_papers(bundle_id)
        if not papers:
            return {"error": "번들에 논문 없음"}

        conn = sqlite3.connect(self.db_path)
        result = {}
        for p in papers:
            row = conn.execute(
                "SELECT abstract FROM papers WHERE id=?", (p["id"],)
            ).fetchone()
            result[p["title"]] = row[0] if row else ""
        conn.close()

        if llm_func:
            combined = "\n\n".join(
                f"[{title}]\n{abstract}" for title, abstract in result.items()
            )
            prompt = f"다음 논문들의 핵심 주장을 비교 분석해줘:\n\n{combined}"
            analysis = llm_func(prompt)
            return {"papers": result, "analysis": analysis}

        return {"papers": result}

    def save_version(self, bundle_id: int, notes: str = "") -> str:
        """번들 스냅샷 저장 (메타데이터 + notes → zip)"""
        papers = self.get_bundle_papers(bundle_id)
        snapshot = {
            "bundle_id": bundle_id,
            "saved_at": datetime.now().isoformat(),
            "notes": notes,
            "papers": papers,
        }
        zip_path = os.path.join(
            self.BUNDLE_DIR,
            f"bundle_{bundle_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
        )
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("snapshot.json", json.dumps(snapshot, ensure_ascii=False, indent=2))
            # PDF 포함 (있을 경우)
            conn = sqlite3.connect(self.db_path)
            for p in papers:
                row = conn.execute(
                    "SELECT pdf_path FROM papers WHERE id=?", (p["id"],)
                ).fetchone()
                if row and row[0] and os.path.exists(row[0]):
                    zf.write(row[0], os.path.basename(row[0]))
            conn.close()
        return zip_path

    def add_paper_to_bundle(self, bundle_id: int, paper_id: int):
        """기존 번들에 논문 추가"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR IGNORE INTO bundle_papers (bundle_id, paper_id) VALUES (?,?)",
            (bundle_id, paper_id),
        )
        conn.commit()
        conn.close()

    def _ensure_tables(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bundles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bundle_papers (
                bundle_id INTEGER,
                paper_id INTEGER,
                PRIMARY KEY (bundle_id, paper_id)
            )
        """)
        conn.commit()
        conn.close()
