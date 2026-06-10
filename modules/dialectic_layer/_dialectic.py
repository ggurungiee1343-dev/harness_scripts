"""
Dialectic Layer — Dialectic (메모리 연결 & 컨텍스트)
====================================================
메모리 간 의미론적 연결, 컨텍스트 요약 저장/회상,
세션 로그 기반 자동 연결을 담당하는 제2계층.

Life-Harness Layer 4 — Cross-session Context & Semantic Linking
"""

import hashlib
import json
import logging
from datetime import datetime

from ._base import _init_db, SESSION_LOG

log = logging.getLogger("dialectic")


def link_memories(
    source_id: str,
    source_type: str,
    target_id: str,
    target_type: str,
    relation: str,
    confidence: float = 1.0,
):
    """
    두 메모리/결정/스킬 간 의미론적 연결을 생성합니다.

    Args:
        source_id:   원본 ID (skill_id, decision_id, memory_id 등)
        source_type: 원본 타입 (skill, decision, bio_memory, session 등)
        target_id:   대상 ID
        target_type: 대상 타입
        relation:    관계 설명 (예: "caused", "related_to", "preceded_by")
    """
    conn = _init_db()
    conn.execute(
        "INSERT INTO memory_links "
        "(source_id, source_type, target_id, target_type, relation, confidence, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source_id, source_type, target_id, target_type, relation, confidence,
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    log.info(f"🔗 메모리 연결: {source_type}:{source_id[:8]} → {target_type}:{target_id[:8]} ({relation})")


def get_linked_memories(entity_id: str, entity_type: str = None, limit: int = 10) -> list:
    """특정 엔티티와 연결된 모든 메모리 조회."""
    conn = _init_db()
    if entity_type:
        cursor = conn.execute(
            "SELECT source_id, source_type, target_id, target_type, relation, confidence, created_at "
            "FROM memory_links "
            "WHERE (source_id=? AND source_type=?) OR (target_id=? AND target_type=?) "
            "ORDER BY created_at DESC LIMIT ?",
            (entity_id, entity_type, entity_id, entity_type, limit),
        )
    else:
        cursor = conn.execute(
            "SELECT source_id, source_type, target_id, target_type, relation, confidence, created_at "
            "FROM memory_links "
            "WHERE source_id=? OR target_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (entity_id, entity_id, limit),
        )
    results = []
    for row in cursor.fetchall():
        results.append({
            "source": {"id": row[0], "type": row[1]},
            "target": {"id": row[2], "type": row[3]},
            "relation": row[4],
            "confidence": row[5],
            "created_at": row[6],
        })
    conn.close()
    return results


def save_context_summary(
    session_id: str,
    summary: str,
    key_topics: list,
    decisions_made: list,
):
    """세션 컨텍스트 요약 저장."""
    conn = _init_db()
    timestamp = datetime.now().isoformat()
    embedding_hash = hashlib.md5(summary.encode()).hexdigest()[:16]
    topics_json = json.dumps(key_topics, ensure_ascii=False)
    decisions_json = json.dumps(decisions_made, ensure_ascii=False)

    conn.execute(
        "INSERT INTO context_summaries "
        "(session_id, summary, key_topics, decisions_made, timestamp, embedding_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, summary, topics_json, decisions_json, timestamp, embedding_hash),
    )
    conn.commit()
    conn.close()


def recall_relevant_context(query: str, limit: int = 3) -> list:
    """쿼리와 관련된 이전 세션 컨텍스트 회상."""
    conn = _init_db()
    cursor = conn.execute(
        "SELECT session_id, summary, key_topics, decisions_made, timestamp "
        "FROM context_summaries "
        "WHERE summary LIKE ? OR key_topics LIKE ? "
        "ORDER BY timestamp DESC LIMIT ?",
        (f"%{query}%", f"%{query}%", limit),
    )
    results = []
    for row in cursor.fetchall():
        results.append({
            "session_id": row[0],
            "summary": row[1][:300],
            "topics": json.loads(row[2]) if row[2] else [],
            "decisions": json.loads(row[3]) if row[3] else [],
            "timestamp": row[4],
        })
    conn.close()
    return results


def auto_link_from_session_logs(min_confidence: float = 0.5) -> int:
    """
    session_events.jsonl 을 분석해 스킬-결정-메모리 간 자동 연결을 생성합니다.
    같은 카테고리/타임스탬프 근처의 이벤트를 연결합니다.

    Returns:
        생성된 연결 수
    """
    if not SESSION_LOG.exists():
        return 0

    conn = _init_db()
    links_created = 0
    events = []

    with open(SESSION_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # 시간순 정렬 후 근접 이벤트 연결
    events.sort(key=lambda e: e.get("timestamp", ""))
    for i in range(len(events) - 1):
        e1, e2 = events[i], events[i + 1]
        t1 = e1.get("timestamp", "")
        t2 = e2.get("timestamp", "")

        # 5분 이내 이벤트만 연결
        try:
            dt1 = datetime.fromisoformat(t1)
            dt2 = datetime.fromisoformat(t2)
            if abs((dt2 - dt1).total_seconds()) > 300:
                continue
        except (ValueError, TypeError):
            continue

        # 같은 카테고리인 경우 연결
        if e1.get("category") == e2.get("category"):
            source_id = e1.get("skill_path", "") or e1.get("trigger", "")
            target_id = e2.get("skill_path", "") or e2.get("trigger", "")
            if source_id and target_id:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_links "
                    "(source_id, source_type, target_id, target_type, relation, confidence, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (source_id, "session_event", target_id, "session_event",
                     "temporal_proximity", min_confidence, datetime.now().isoformat()),
                )
                links_created += 1

    conn.commit()
    conn.close()
    log.info(f"🔗 자동 연결 {links_created}개 생성")
    return links_created
