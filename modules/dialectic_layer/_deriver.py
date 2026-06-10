"""
Dialectic Layer — Deriver (의사결정 관리)
===========================================
의사결정 기록, 회상, 충돌 감지를 담당하는 제1계층.

Life-Harness Layer 4 — Decision Justification & Priority Scheduling
"""

import hashlib
import json
import logging
from datetime import datetime

from ._base import _init_db, DECISIONS_DIR

log = logging.getLogger("dialectic")


def record_decision(
    title: str,
    context: str,
    rationale: str,
    alternatives: list,
    outcome: str,
    tags: list = None,
) -> str:
    """
    중요한 의사결정을 기록합니다.

    Args:
        title: 결정 제목
        context: 결정을 내린 상황/배경
        rationale: 결정 근거
        alternatives: 고려된 대안들
        outcome: 결정 결과
        tags: 태그 리스트

    Returns:
        decision_id
    """
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    conn = _init_db()

    decision_id = hashlib.md5(f"{title}:{datetime.now().isoformat()}".encode()).hexdigest()[:12]
    timestamp = datetime.now().isoformat()
    tags_str = json.dumps(tags or [], ensure_ascii=False)
    alternatives_str = json.dumps(alternatives, ensure_ascii=False)

    conn.execute(
        "INSERT OR REPLACE INTO decisions "
        "(decision_id, title, context, rationale, alternatives, outcome, timestamp, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (decision_id, title, context, rationale, alternatives_str, outcome, timestamp, tags_str),
    )
    conn.commit()

    # 파일로도 저장
    decision_file = DECISIONS_DIR / f"{decision_id}.md"
    decision_file.write_text(
        f"# 결정 기록: {title}\n\n"
        f"- **ID**: `{decision_id}`\n"
        f"- **일시**: {timestamp}\n"
        f"- **태그**: {tags_str}\n\n"
        f"## 배경\n{context}\n\n"
        f"## 근거\n{rationale}\n\n"
        f"## 고려한 대안\n"
        + "\n".join(f"- {a}" for a in alternatives) + "\n\n"
        f"## 결과\n{outcome}\n\n"
        f"---\n*Dialectic Layer 기록*",
        encoding="utf-8",
    )

    log.info(f"📝 결정 기록: {title} ({decision_id})")
    conn.close()
    return decision_id


def recall_decision(query: str, limit: int = 5) -> list:
    """키워드 기반 의사결정 회상."""
    conn = _init_db()
    cursor = conn.execute(
        "SELECT decision_id, title, context, rationale, outcome, timestamp, tags "
        "FROM decisions "
        "WHERE title LIKE ? OR context LIKE ? OR rationale LIKE ? "
        "ORDER BY timestamp DESC LIMIT ?",
        (f"%{query}%", f"%{query}%", f"%{query}%", limit),
    )
    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "title": row[1],
            "context": row[2][:200],
            "rationale": row[3][:200],
            "outcome": row[4],
            "timestamp": row[5],
            "tags": json.loads(row[6]) if row[6] else [],
        })
    conn.close()
    return results


def detect_memory_conflicts() -> list:
    """
    상충되는 메모리/결정 감지.
    같은 주제에 대해 서로 다른 결론을 내린 결정을 찾습니다.
    """
    conn = _init_db()
    cursor = conn.execute(
        "SELECT d1.title, d1.outcome, d1.timestamp, d2.title, d2.outcome, d2.timestamp "
        "FROM decisions d1, decisions d2 "
        "WHERE d1.decision_id < d2.decision_id "
        "AND (d1.title LIKE d2.title || '%' OR d2.title LIKE d1.title || '%') "
        "AND d1.outcome != d2.outcome"
    )
    conflicts = []
    for row in cursor.fetchall():
        conflicts.append({
            "title": row[0],
            "outcome_a": row[1],
            "time_a": row[2],
            "title_b": row[3],
            "outcome_b": row[4],
            "time_b": row[5],
        })
    conn.close()
    return conflicts
