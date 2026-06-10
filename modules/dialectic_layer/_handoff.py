"""
Dialectic Layer — Handoff (세션 핸드오프)
============================================
Life-Harness Layer 4: 세션 간 상태 전달 및 복원.

세션 요약 → DB 저장 → 다른 인스턴스/세션에서 로드하여
연속성 있는 대화 흐름을 제공합니다.
"""

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from ._base import HERMES_HOME, DIALECTIC_DB

log = logging.getLogger("dialectic.handoff")


# ── 내부 헬퍼 ──────────────────────────────────────────

def _get_conn():
    return sqlite3.connect(str(DIALECTIC_DB))


def _table_exists(cursor, name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    )
    return cursor.fetchone() is not None


# ── 스키마 초기화 ──────────────────────────────────────

def _init_handoff_table():
    """handoffs 테이블 및 handoff_messages 테이블 생성 (멱등)."""
    conn = _get_conn()
    cursor = conn.cursor()

    if not _table_exists(cursor, "handoffs"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS handoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                title TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                key_decisions TEXT DEFAULT '[]',
                active_context TEXT DEFAULT '',
                persona_snapshot TEXT DEFAULT '{}',
                preference_snapshot TEXT DEFAULT '{}',
                system_mode TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        log.info("✅ handoffs 테이블 생성 완료")

    if not _table_exists(cursor, "handoff_messages"):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS handoff_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                handoff_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (handoff_id) REFERENCES handoffs(id) ON DELETE CASCADE
            )
        """)
        log.info("✅ handoff_messages 테이블 생성 완료")

    conn.commit()
    conn.close()


# ── 핵심 API ───────────────────────────────────────────

def save_handoff(
    session_id: str,
    title: str = "",
    summary: str = "",
    key_decisions: Optional[List[str]] = None,
    active_context: str = "",
    persona_snapshot: Optional[Dict[str, Any]] = None,
    preference_snapshot: Optional[Dict[str, Any]] = None,
    system_mode: str = "",
    messages: Optional[List[Dict[str, str]]] = None,
) -> int:
    """핸드오프 상태 저장 (UPSERT).

    Args:
        session_id: 고유 세션 식별자
        title: 세션 제목
        summary: 세션 요약 (LLM 생성)
        key_decisions: 주요 의사결정 목록
        active_context: 현재 활성 컨텍스트
        persona_snapshot: 사용자 페르소나 스냅샷
        preference_snapshot: 사용자 선호도 스냅샷
        system_mode: 현재 시스템 모드 (Gemma4/DeepSeek/NVIDIA)
        messages: 최근 메시지 목록

    Returns:
        handoff 테이블의 id
    """
    now = datetime.now().isoformat(timespec="seconds")
    if key_decisions is None:
        key_decisions = []
    if persona_snapshot is None:
        persona_snapshot = {}
    if preference_snapshot is None:
        preference_snapshot = {}

    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO handoffs
            (session_id, title, summary, key_decisions, active_context,
             persona_snapshot, preference_snapshot, system_mode,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            title = excluded.title,
            summary = excluded.summary,
            key_decisions = excluded.key_decisions,
            active_context = excluded.active_context,
            persona_snapshot = excluded.persona_snapshot,
            preference_snapshot = excluded.preference_snapshot,
            system_mode = excluded.system_mode,
            updated_at = excluded.updated_at
    """, (
        session_id, title, summary, json.dumps(key_decisions, ensure_ascii=False),
        active_context, json.dumps(persona_snapshot, ensure_ascii=False),
        json.dumps(preference_snapshot, ensure_ascii=False),
        system_mode, now, now
    ))

    handoff_id = cursor.lastrowid

    # 메시지 저장
    if messages:
        cursor.execute("DELETE FROM handoff_messages WHERE handoff_id = ?", (handoff_id,))
        for msg in messages:
            cursor.execute(
                "INSERT INTO handoff_messages (handoff_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (handoff_id, msg.get("role", "user"), msg.get("content", ""), now)
            )

    conn.commit()
    conn.close()
    log.info(f"💾 핸드오프 저장: session_id={session_id}, id={handoff_id}")
    return handoff_id


def load_handoff(session_id: str = "", handoff_id: int = 0) -> Optional[Dict[str, Any]]:
    """핸드오프 상태 로드.

    Args:
        session_id: 세션 ID로 조회
        handoff_id: DB ID로 조회 (session_id보다 우선)

    Returns:
        핸드오프 데이터 dict, 없으면 None
    """
    conn = _get_conn()
    cursor = conn.cursor()

    if handoff_id:
        cursor.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,))
    elif session_id:
        cursor.execute("SELECT * FROM handoffs WHERE session_id = ?", (session_id,))
    else:
        conn.close()
        return None

    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    columns = [desc[0] for desc in cursor.description]
    handoff = dict(zip(columns, row))

    # JSON 필드 역직렬화
    for field in ("key_decisions", "persona_snapshot", "preference_snapshot"):
        if isinstance(handoff.get(field), str):
            try:
                handoff[field] = json.loads(handoff[field])
            except (json.JSONDecodeError, TypeError):
                handoff[field] = {} if "snapshot" in field else []

    # 메시지 로드
    cursor.execute(
        "SELECT role, content FROM handoff_messages WHERE handoff_id = ? ORDER BY id ASC",
        (handoff["id"],)
    )
    handoff["messages"] = [{"role": r, "content": c} for r, c in cursor.fetchall()]

    conn.close()
    return handoff


def get_latest_handoff() -> Optional[Dict[str, Any]]:
    """가장 최근 핸드오프 로드."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM handoffs ORDER BY updated_at DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return load_handoff(handoff_id=row[0])
    return None


def list_handoffs(limit: int = 10) -> List[Dict[str, Any]]:
    """핸드오프 목록 조회."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, session_id, title, summary, system_mode, updated_at
        FROM handoffs ORDER BY updated_at DESC LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0], "session_id": r[1], "title": r[2],
            "summary": r[3][:200] if r[3] else "",
            "system_mode": r[4], "updated_at": r[5]
        }
        for r in rows
    ]


def delete_handoff(session_id: str = "", handoff_id: int = 0) -> bool:
    """핸드오프 삭제."""
    conn = _get_conn()
    cursor = conn.cursor()
    if handoff_id:
        cursor.execute("DELETE FROM handoff_messages WHERE handoff_id = ?", (handoff_id,))
        cursor.execute("DELETE FROM handoffs WHERE id = ?", (handoff_id,))
    elif session_id:
        cursor.execute(
            "DELETE FROM handoff_messages WHERE handoff_id IN (SELECT id FROM handoffs WHERE session_id = ?)",
            (session_id,)
        )
        cursor.execute("DELETE FROM handoffs WHERE session_id = ?", (session_id,))
    else:
        conn.close()
        return False
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


# ── 마이그레이션 훅 ────────────────────────────────────

def migrate():
    """_migrate()에서 호출할 스키마 마이그레이션."""
    _init_handoff_table()
