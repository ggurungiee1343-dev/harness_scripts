import os
import sqlite3
import datetime
from pathlib import Path

KANBAN_DB_PATH = os.path.expanduser('~/.hermes/kanban.db')
ZOMBIE_TIMEOUT_SECONDS = 300  # 5분 이상 heartbeat 없으면 좀비

class KanbanDB:
    """SQLite-backed Kanban manager with heartbeat, worker assignment, retry budget."""
    def __init__(self, db_path: str = KANBAN_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    column TEXT NOT NULL CHECK(column IN ('TODO','IN_PROGRESS','DONE')),
                    created_at TIMESTAMP NOT NULL,
                    worker_id TEXT DEFAULT NULL,
                    heartbeat_at TIMESTAMP DEFAULT NULL,
                    retry_budget INTEGER DEFAULT 3,
                    assigned_at TIMESTAMP DEFAULT NULL
                )
            """)
            conn.commit()

            # 마이그레이션: 기존 테이블에 컬럼 없으면 추가
            cursor = conn.execute("PRAGMA table_info(cards)")
            existing = {row[1] for row in cursor.fetchall()}
            for col, col_type in [("worker_id", "TEXT DEFAULT NULL"),
                                  ("heartbeat_at", "TIMESTAMP DEFAULT NULL"),
                                  ("retry_budget", "INTEGER DEFAULT 3"),
                                  ("assigned_at", "TIMESTAMP DEFAULT NULL")]:
                if col not in existing:
                    conn.execute(f"ALTER TABLE cards ADD COLUMN {col} {col_type}")
                    conn.commit()
        finally:
            conn.close()

    def add_card(self, title: str, column: str = 'TODO') -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO cards (title, column, created_at) VALUES (?, ?, ?)",
                (title, column, datetime.datetime.utcnow().isoformat())
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def list_cards(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, title, column, created_at, worker_id, heartbeat_at, retry_budget FROM cards ORDER BY id")
            rows = cur.fetchall()
            result = {'TODO': [], 'IN_PROGRESS': [], 'DONE': []}
            now = datetime.datetime.utcnow()
            for row in rows:
                card = {
                    'id': row[0], 'title': row[1], 'created_at': row[3],
                    'worker_id': row[4], 'heartbeat_at': row[5], 'retry_budget': row[6],
                }
                # 좀비 감지: IN_PROGRESS면서 heartbeat 없거나 5분 이상 경과
                if row[2] == 'IN_PROGRESS' and row[4]:
                    if row[5] is None:
                        card['zombie'] = True
                    else:
                        try:
                            hb = datetime.datetime.fromisoformat(row[5]) if isinstance(row[5], str) else row[5]
                            card['zombie'] = (now - hb).total_seconds() > ZOMBIE_TIMEOUT_SECONDS
                        except (ValueError, TypeError):
                            card['zombie'] = True
                else:
                    card['zombie'] = False
                result[row[2]].append(card)
            return result
        finally:
            conn.close()

    def move_card(self, card_id: int, new_column: str) -> bool:
        if new_column not in ('TODO', 'IN_PROGRESS', 'DONE'):
            raise ValueError('Invalid column')
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            now = datetime.datetime.utcnow().isoformat()
            if new_column == 'IN_PROGRESS':
                cur.execute("UPDATE cards SET column = ?, assigned_at = ?, heartbeat_at = ? WHERE id = ?",
                            (new_column, now, now, card_id))
            elif new_column == 'DONE':
                # 완료 시 worker/retry 초기화
                cur.execute("UPDATE cards SET column = ?, worker_id = NULL, retry_budget = 3, heartbeat_at = NULL, assigned_at = NULL WHERE id = ?",
                            (new_column, card_id))
            else:
                # TODO로 이동 시 worker 해제
                cur.execute("UPDATE cards SET column = ?, worker_id = NULL, retry_budget = 3, heartbeat_at = NULL, assigned_at = NULL WHERE id = ?",
                            (new_column, card_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_card(self, card_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM cards WHERE id = ?", (card_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def claim_card(self, card_id: int, worker_id: str) -> bool:
        """워커가 카드 할당 받음. retry_budget 설정."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            now = datetime.datetime.utcnow().isoformat()
            cur.execute(
                "UPDATE cards SET worker_id = ?, column = 'IN_PROGRESS', assigned_at = ?, heartbeat_at = ?, retry_budget = 3 WHERE id = ? AND column = 'IN_PROGRESS' AND worker_id IS NULL",
                (worker_id, now, now, card_id)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def heartbeat(self, card_id: int, worker_id: str) -> bool:
        """워커가 작업 진행 중임을 알림."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            now = datetime.datetime.utcnow().isoformat()
            cur.execute(
                "UPDATE cards SET heartbeat_at = ? WHERE id = ? AND worker_id = ?",
                (now, card_id, worker_id)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def release_card(self, card_id: int, worker_id: str, success: bool = True) -> bool:
        """워커가 작업 완료 또는 포기."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            if success:
                cur.execute(
                    "UPDATE cards SET column = 'DONE', worker_id = NULL, retry_budget = 3, heartbeat_at = NULL, assigned_at = NULL WHERE id = ? AND worker_id = ?",
                    (card_id, worker_id)
                )
            else:
                # 실패 시 retry_budget 차감, 예산 남으면 IN_PROGRESS 유지
                cur.execute(
                    "UPDATE cards SET retry_budget = retry_budget - 1, heartbeat_at = NULL WHERE id = ? AND worker_id = ?",
                    (card_id, worker_id)
                )
                # 예산 소진 시 TODO로
                cur.execute(
                    "UPDATE cards SET column = 'TODO', worker_id = NULL, assigned_at = NULL WHERE id = ? AND worker_id = ? AND retry_budget <= 0",
                    (card_id, worker_id)
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_zombies(self) -> list:
        """좀비 카드 목록 조회 (IN_PROGRESS but heartbeat timeout)."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            threshold = (datetime.datetime.utcnow() - datetime.timedelta(seconds=ZOMBIE_TIMEOUT_SECONDS)).isoformat()
            cur.execute(
                "SELECT id, title, worker_id, heartbeat_at, retry_budget FROM cards WHERE column = 'IN_PROGRESS' AND worker_id IS NOT NULL AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
                (threshold,)
            )
            rows = cur.fetchall()
            return [
                {'id': r[0], 'title': r[1], 'worker_id': r[2], 'heartbeat_at': r[3], 'retry_budget': r[4]}
                for r in rows
            ]
        finally:
            conn.close()

    def get_worker_stats(self) -> dict:
        """워커별 통계."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT worker_id, COUNT(*) FROM cards WHERE column = 'IN_PROGRESS' AND worker_id IS NOT NULL GROUP BY worker_id")
            active = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute("SELECT worker_id, COUNT(*) FROM cards WHERE column = 'DONE' AND worker_id IS NOT NULL GROUP BY worker_id")
            done = {r[0]: r[1] for r in cur.fetchall()}
            return {'active': active, 'done': done}
        finally:
            conn.close()
