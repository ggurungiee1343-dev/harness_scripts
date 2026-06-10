"""
Dialectic Layer — FTS5 Full-Text Search (전문 검색)
======================================================
Life-Harness Layer 4: SQLite FTS5 가상 테이블을 통한
세션 요약, 의사결정, 핸드오프 전문 검색.

사용법:
  from modules.dialectic_layer._fts import search_all, rebuild_index

  results = search_all("모델 전환", limit=5)
  # → [{"table": "context_summaries", "rank": 0, "snippet": "...", ...}, ...]
"""

import json
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from ._base import DIALECTIC_DB

log = logging.getLogger("dialectic.fts")

# FTS5 토큰화 옵션: 유니코드 토큰나이저 (한국어+영문)
_TOKENIZER = "unicode61"


def _get_conn():
    return sqlite3.connect(str(DIALECTIC_DB))


def _table_exists(cursor, name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    )
    return cursor.fetchone() is not None


def _content_table_exists(cursor, name: str) -> bool:
    """content= 테이블 존재 확인 (FTS5 content sync용)."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    )
    return cursor.fetchone() is not None


# ── 스키마 초기화 ───────────────────────────────────────

def _init_fts_tables():
    """FTS5 가상 테이블 생성 (멱등)."""
    conn = _get_conn()
    cursor = conn.cursor()

    # 1. context_summaries FTS5
    if not _table_exists(cursor, "ctx_summary_fts"):
        # content= 없이 독립 FTS5 (데이터는 직접 INSERT)
        cursor.execute("""
            CREATE VIRTUAL TABLE ctx_summary_fts USING fts5(
                summary,
                key_topics,
                decisions_made,
                session_id UNINDEXED,
                content='',
                tokenize='unicode61'
            )
        """)
        log.info("✅ ctx_summary_fts FTS5 테이블 생성 완료")

    # 2. decisions FTS5
    if not _table_exists(cursor, "decisions_fts"):
        cursor.execute("""
            CREATE VIRTUAL TABLE decisions_fts USING fts5(
                title,
                context,
                rationale,
                alternatives,
                outcome,
                decision_id UNINDEXED,
                content='',
                tokenize='unicode61'
            )
        """)
        log.info("✅ decisions_fts FTS5 테이블 생성 완료")

    # 3. handoffs FTS5
    if not _table_exists(cursor, "handoffs_fts"):
        cursor.execute("""
            CREATE VIRTUAL TABLE handoffs_fts USING fts5(
                title,
                summary,
                active_context,
                session_id UNINDEXED,
                content='',
                tokenize='unicode61'
            )
        """)
        log.info("✅ handoffs_fts FTS5 테이블 생성 완료")

    conn.commit()
    conn.close()


# ── 데이터 동기화 ───────────────────────────────────────

def rebuild_index():
    """모든 FTS5 인덱스 재구축 (소스 테이블에서 다시 읽어옴)."""
    conn = _get_conn()
    cursor = conn.cursor()

    # --- ctx_summary_fts ---
    if _table_exists(cursor, "ctx_summary_fts") and _content_table_exists(cursor, "context_summaries"):
        cursor.execute("DELETE FROM ctx_summary_fts")
        cursor.execute("""
            SELECT session_id, COALESCE(summary,''), COALESCE(key_topics,''), COALESCE(decisions_made,'')
            FROM context_summaries
        """)
        rows = cursor.fetchall()
        for sid, summary, topics, decisions in rows:
            try:
                cursor.execute(
                    "INSERT INTO ctx_summary_fts (session_id, summary, key_topics, decisions_made) VALUES (?,?,?,?)",
                    (sid, summary, topics, decisions)
                )
            except Exception as e:
                log.warning(f"ctx_summary_fts 삽입 실패 (session={sid}): {e}")
        log.info(f"📊 ctx_summary_fts: {len(rows)}개 문서 인덱싱 완료")

    # --- decisions_fts ---
    if _table_exists(cursor, "decisions_fts") and _content_table_exists(cursor, "decisions"):
        cursor.execute("DELETE FROM decisions_fts")
        cursor.execute("""
            SELECT decision_id, COALESCE(title,''), COALESCE(context,''),
                   COALESCE(rationale,''), COALESCE(alternatives,''), COALESCE(outcome,'')
            FROM decisions
        """)
        rows = cursor.fetchall()
        for did, title, ctx, rationale, alt, outcome in rows:
            try:
                cursor.execute(
                    "INSERT INTO decisions_fts (decision_id, title, context, rationale, alternatives, outcome) VALUES (?,?,?,?,?,?)",
                    (did, title, ctx, rationale, alt, outcome)
                )
            except Exception as e:
                log.warning(f"decisions_fts 삽입 실패 (decision={did}): {e}")
        log.info(f"📊 decisions_fts: {len(rows)}개 문서 인덱싱 완료")

    # --- handoffs_fts ---
    if _table_exists(cursor, "handoffs_fts") and _table_exists(cursor, "handoffs"):
        cursor.execute("DELETE FROM handoffs_fts")
        cursor.execute("""
            SELECT session_id, COALESCE(title,''), COALESCE(summary,''), COALESCE(active_context,'')
            FROM handoffs
        """)
        rows = cursor.fetchall()
        for sid, title, summary, ctx in rows:
            try:
                cursor.execute(
                    "INSERT INTO handoffs_fts (session_id, title, summary, active_context) VALUES (?,?,?,?)",
                    (sid, title, summary, ctx)
                )
            except Exception as e:
                log.warning(f"handoffs_fts 삽입 실패 (session={sid}): {e}")
        log.info(f"📊 handoffs_fts: {len(rows)}개 문서 인덱싱 완료")

    conn.commit()
    conn.close()
    return True


# ── 검색 API ────────────────────────────────────────────

def search_all(
    query: str,
    limit: int = 10,
    sources: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """모든 FTS5 인덱스 통합 검색.

    Args:
        query: 검색어 (FTS5 쿼리 문법 지원: AND, OR, "phrase", prefix*)
        limit: 소스당 최대 결과 수
        sources: 검색 대상 ('summaries', 'decisions', 'handoffs'). None=전체

    Returns:
        [{"source": "summaries|decisions|handoffs", "rank": 0, "snippet": "...", "row": {...}}, ...]
    """
    if sources is None:
        sources = ["summaries", "decisions", "handoffs"]

    conn = _get_conn()
    cursor = conn.cursor()
    results = []

    fts_tables = {
        "summaries": ("ctx_summary_fts", "session_id", ["summary", "key_topics", "decisions_made"],
                      "SELECT session_id, summary, key_topics, decisions_made FROM ctx_summary_fts WHERE ctx_summary_fts MATCH ? ORDER BY rank LIMIT ?"),
        "decisions": ("decisions_fts", "decision_id", ["title", "context", "rationale", "alternatives", "outcome"],
                      "SELECT decision_id, title, context, rationale, alternatives, outcome FROM decisions_fts WHERE decisions_fts MATCH ? ORDER BY rank LIMIT ?"),
        "handoffs": ("handoffs_fts", "session_id", ["title", "summary", "active_context"],
                     "SELECT session_id, title, summary, active_context FROM handoffs_fts WHERE handoffs_fts MATCH ? ORDER BY rank LIMIT ?"),
    }

    for src in sources:
        if src not in fts_tables:
            continue
        tbl, id_col, cols, sql = fts_tables[src]

        if not _table_exists(cursor, tbl):
            continue

        try:
            cursor.execute(sql, (query, limit))
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            for row in rows:
                row_dict = dict(zip(columns, row))

                # FTS5 rank (0=best match)
                rank = 0

                # snippet 생성: 검색어 근처 텍스트 추출
                snippets = []
                for col in cols:
                    val = row_dict.get(col, "")
                    if val and isinstance(val, str):
                        try:
                            cursor.execute(
                                f"SELECT snippet({tbl}, 1, '<b>', '</b>', '...', 32) FROM {tbl} WHERE {tbl} MATCH ? AND {id_col}=?",
                                (query, row_dict[id_col])
                            )
                            snip = cursor.fetchone()
                            if snip and snip[0]:
                                snippets.append(snip[0])
                        except Exception:
                            pass

                result = {
                    "source": src,
                    "rank": rank,
                    "id_field": id_col,
                    "id_value": row_dict[id_col],
                    "snippet": snippets[0] if snippets else "",
                    "row": row_dict,
                }
                results.append(result)
        except Exception as e:
            log.warning(f"FTS5 검색 오류 ({tbl}): {e}")

    conn.close()

    # rank 정렬 (소스 순서 유지하면서 rank 내부 정렬)
    return results


def search_summaries(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """context_summaries 검색 전용."""
    return search_all(query, limit, sources=["summaries"])


def search_decisions(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """decisions 검색 전용."""
    return search_all(query, limit, sources=["decisions"])


def search_handoffs(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """handoffs 검색 전용."""
    return search_all(query, limit, sources=["handoffs"])


# ── 마이그레이션 훅 ─────────────────────────────────────

def migrate():
    """_migrate()에서 호출할 스키마 마이그레이션 + 인덱스 재구축."""
    _init_fts_tables()
    try:
        rebuild_index()
        log.info("✅ FTS5 인덱스 초기 구축 완료")
    except Exception as e:
        log.warning(f"FTS5 초기 인덱스 구축 실패 (스키마만 생성): {e}")
        # 스키마는 생성됐으므로 무시 가능
