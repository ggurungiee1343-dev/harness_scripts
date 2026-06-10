"""
Dialectic Layer — Shared Base
================================
Life-Harness Layer 4: 공통 데이터베이스 초기화 및 마이그레이션.

이 모듈은 _deriver, _dialectic, _dreamer 세 하위 레이어가 공유하는
DB 연결, 경로 상수, 스키마 마이그레이션 로직을 제공합니다.
"""

import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime

log = logging.getLogger("dialectic")

# ── 경로 설정 ──────────────────────────────────────────────
HERMES_HOME = Path("/Users/bluesea/.hermes")
MEMORY_DIR  = HERMES_HOME / "memories"
DIALECTIC_DB = HERMES_HOME / "dialectic.db"
SESSION_LOG  = HERMES_HOME / "session_events.jsonl"
DECISIONS_DIR = HERMES_HOME / "decisions"


def _init_db():
    """Dialectic SQLite DB 초기화."""
    DIALECTIC_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DIALECTIC_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            relation TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            last_accessed TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            context TEXT,
            rationale TEXT,
            alternatives TEXT,
            outcome TEXT,
            timestamp TEXT NOT NULL,
            tags TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            key_topics TEXT,
            decisions_made TEXT,
            timestamp TEXT NOT NULL,
            embedding_hash TEXT
        )
    """)
    conn.commit()
    return conn


def _migrate():
    """데이터베이스 스키마 마이그레이션 (필요시)."""
    conn = _init_db()

    # Hot patch: user_personas / preferences 테이블
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='user_personas'"
    )
    if not cursor.fetchone():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trait TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(trait, value)
            )
        """)
        log.info("🧑 user_personas 테이블 생성 완료")

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'"
    )
    if not cursor.fetchone():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preference_key TEXT NOT NULL UNIQUE,
                preference_value TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        log.info("⭐ user_preferences 테이블 생성 완료")

    conn.commit()
    conn.close()

    # ── 핸드오프 & FTS5 마이그레이션 ─────────────────────
    try:
        from . import _handoff
        _handoff.migrate()
    except Exception as e:
        log.warning(f"handoff 마이그레이션 실패: {e}")

    try:
        from . import _fts
        _fts.migrate()
    except Exception as e:
        log.warning(f"FTS5 마이그레이션 실패: {e}")
