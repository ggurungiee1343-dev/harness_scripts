"""
index_db.py — 통합 인덱스 DB 헬퍼 모듈 v1.0
=============================================
hermes_index.db에 대한 공통 연결/조회/수정 함수 제공.
모든 컴포넌트(tag_linker, _research, ingest_engine, dream_scheduler)가 이 모듈을 통해 DB 접근.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger('HermesOrchestrator')

DB_PATH = Path.home() / '.hermes' / 'runtime' / 'hermes_index.db'


def get_conn() -> sqlite3.Connection:
    """DB 연결 반환 (with 문에서 사용)"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Tags ──────────────────────────────────────────────────

def add_tag(tag: str, category: str = 'general') -> bool:
    """태그 추가. 성공 시 True, 중복 시 False."""
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO tags (tag, category, updated_at)
                   VALUES (?, ?, datetime('now'))""",
                (tag, category)
            )
            return conn.total_changes > 0
    except Exception as e:
        logger.error(f"[index_db] add_tag 실패: {e}")
        return False


def remove_tag(tag: str) -> bool:
    """태그 제거."""
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM tags WHERE tag = ?", (tag,))
            return conn.total_changes > 0
    except Exception as e:
        logger.error(f"[index_db] remove_tag 실패: {e}")
        return False


def list_tags(category: Optional[str] = None) -> list:
    """태그 목록 조회."""
    try:
        with get_conn() as conn:
            if category:
                rows = conn.execute(
                    "SELECT tag, category, created_at FROM tags WHERE category = ? ORDER BY tag",
                    (category,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tag, category, created_at FROM tags ORDER BY tag"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[index_db] list_tags 실패: {e}")
        return []


# ── Papers ────────────────────────────────────────────────

def add_paper(title: str, arxiv_id: str = '', authors: str = '',
              abstract: str = '', url: str = '', tags: str = '',
              bundle_id: Optional[int] = None) -> Optional[int]:
    """논문 추가. 성공 시 id 반환, 실패 시 None."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO papers (title, arxiv_id, authors, abstract, url, tags, bundle_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, arxiv_id or None, authors, abstract, url, tags, bundle_id)
            )
            return cur.lastrowid
    except Exception as e:
        logger.error(f"[index_db] add_paper 실패: {e}")
        return None


def get_paper(arxiv_id: str) -> Optional[dict]:
    """arXiv ID로 논문 조회."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[index_db] get_paper 실패: {e}")
        return None


def list_papers(bundle_id: Optional[int] = None) -> list:
    """논문 목록 조회. bundle_id 지정 시 해당 번들 내 논문만."""
    try:
        with get_conn() as conn:
            if bundle_id is not None:
                rows = conn.execute(
                    "SELECT * FROM papers WHERE bundle_id = ? ORDER BY title",
                    (bundle_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM papers ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[index_db] list_papers 실패: {e}")
        return []


def create_bundle(paper_ids: list) -> Optional[int]:
    """여러 논문을 번들로 묶기. 새 bundle_id 반환."""
    try:
        with get_conn() as conn:
            # 가장 큰 bundle_id + 1
            max_row = conn.execute("SELECT COALESCE(MAX(bundle_id), 0) + 1 AS new_id FROM papers").fetchone()
            new_bundle_id = max_row['new_id']
            placeholders = ','.join('?' for _ in paper_ids)
            conn.execute(
                f"UPDATE papers SET bundle_id = ? WHERE id IN ({placeholders})",
                (new_bundle_id, *paper_ids)
            )
            return new_bundle_id
    except Exception as e:
        logger.error(f"[index_db] create_bundle 실패: {e}")
        return None


# ── Intents ────────────────────────────────────────────────

def add_intent(keyword: str, intent: str, weight: float = 1.0) -> bool:
    """의도 패턴 추가."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO intents (keyword, intent, weight) VALUES (?, ?, ?)",
                (keyword, intent, weight)
            )
            return conn.total_changes > 0
    except Exception as e:
        logger.error(f"[index_db] add_intent 실패: {e}")
        return False


def list_intents(intent: Optional[str] = None) -> list:
    """의도 패턴 목록 조회."""
    try:
        with get_conn() as conn:
            if intent:
                rows = conn.execute(
                    "SELECT keyword, intent, weight FROM intents WHERE intent = ? ORDER BY weight DESC",
                    (intent,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT keyword, intent, weight FROM intents ORDER BY intent, weight DESC"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[index_db] list_intents 실패: {e}")
        return []


# ── Ontology ──────────────────────────────────────────────

def upsert_component(component: str, status: str = 'unknown', notes: str = '') -> bool:
    """컴포넌트 상태 갱신."""
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO ontology (component, status, last_checked, notes)
                   VALUES (?, ?, datetime('now'), ?)""",
                (component, status, notes)
            )
            return True
    except Exception as e:
        logger.error(f"[index_db] upsert_component 실패: {e}")
        return False


def list_components(status: Optional[str] = None) -> list:
    """컴포넌트 상태 목록 조회."""
    try:
        with get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT component, status, last_checked, notes FROM ontology WHERE status = ?",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT component, status, last_checked, notes FROM ontology ORDER BY component"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[index_db] list_components 실패: {e}")
        return []


def ensure_schema():
    """스키마가 없으면 생성 (초기화용)"""
    from migrate_to_index_db import create_schema
    with get_conn() as conn:
        create_schema(conn)
