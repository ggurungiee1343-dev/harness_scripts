# modules/tag_linker.py
# Hermes3 Phase 2 - 태그 관리 (v8.6 이벤트 기반 디커플링 아키텍처)
import os
import sqlite3
from typing import List, Dict, Optional, Callable


class TagLinker:
    DB_PATH = os.path.expanduser("~/.hermes/runtime/hermes_index.db")

    # 🏰 [Hermes3 v8.6] 외부 모듈 결합도를 제로로 만들기 위한 인메모리 이벤트 버스 프로토콜
    _subscribers: Dict[str, List[Callable]] = {}

    @classmethod
    def subscribe(cls, event_type: str, callback: Callable):
        """외부 에이전트 모듈이 데이터 백엔드 이벤트를 구독할 수 있는 창구"""
        if event_type not in cls._subscribers:
            cls._subscribers[event_type] = []
        cls._subscribers[event_type].append(callback)

    def _dispatch(self, event_type: str, *args, **kwargs):
        """이벤트 발생 시 상위 모듈로 시그널 전파 (자신은 상위 모듈을 몰라도 됨)"""
        listeners = self._subscribers.get(event_type, [])
        for listener in listeners:
            try:
                listener(*args, **kwargs)
            except Exception as e:
                print(f"⚠️ [v8.6 이벤트 버스] 리스너 구동 실패: {e}")

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = os.path.expanduser(db_path or self.DB_PATH)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._ensure_tables()

        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.commit()
        except Exception: pass
        finally: conn.close()

    def propose_tag(self, file_path: str, tag: str, confidence: float = 0.5, source: str = "system") -> int:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cur = conn.execute(
            "INSERT INTO pending_approvals (file_path, tag, confidence, source, status, proposed_at) VALUES (?, ?, ?, ?, 'pending', datetime('now'))",
            (file_path, tag, confidence, source),
        )
        approval_id = cur.lastrowid
        conn.commit()
        conn.close()
        return approval_id

    def list_pending(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        rows = conn.execute("SELECT id, file_path, tag, confidence, source, proposed_at FROM pending_approvals WHERE status = 'pending' ORDER BY proposed_at DESC").fetchall()
        conn.close()
        return [{"id": r[0], "file_path": r[1], "tag": r[2], "confidence": r[3], "source": r[4], "proposed_at": r[5]} for r in rows]

    def approve_tag(self, approval_id: int) -> bool:
        """승인 처리 후 유기적 이벤트 브로드캐스팅"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        row = conn.execute("SELECT file_path, tag, confidence, source FROM pending_approvals WHERE id = ? AND status = 'pending'", (approval_id,)).fetchone()

        if not row:
            conn.close()
            return False

        file_path, tag, confidence, source = row
        conn.execute("INSERT INTO tags (file_path, tag, confidence, source, created_at) VALUES (?, ?, ?, ?, datetime('now'))", (file_path, tag, confidence, source))
        conn.execute("UPDATE pending_approvals SET status='approved', reviewed_at=datetime('now') WHERE id=?", (approval_id,))
        conn.commit()
        conn.close()

        # 🏰 [Hermes3 v8.6] Pub-Sub 패턴: 승인 이벤트를 시스템 버스에 공표
        self._dispatch("tag_approved", file_path=file_path, tag=tag)

        return True

    def reject_tag(self, approval_id: int) -> bool:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cur = conn.execute("UPDATE pending_approvals SET status='rejected', reviewed_at=datetime('now') WHERE id=? AND status='pending'", (approval_id,))
        changed = cur.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    def get_tags_for_file(self, file_path: str) -> List[Dict]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        rows = conn.execute("SELECT id, file_path, tag, confidence, source, created_at FROM tags WHERE file_path = ? ORDER BY created_at DESC", (file_path,)).fetchall()
        conn.close()
        return [{"id": r[0], "file_path": r[1], "tag": r[2], "confidence": r[3], "source": r[4], "created_at": r[5]} for r in rows]

    def _ensure_tables(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("CREATE TABLE IF NOT EXISTS pending_approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT, tag TEXT, confidence REAL, source TEXT, status TEXT DEFAULT 'pending', proposed_at TEXT, reviewed_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT, tag TEXT, confidence REAL, source TEXT, created_at TEXT)")
        conn.commit()
        conn.close()
