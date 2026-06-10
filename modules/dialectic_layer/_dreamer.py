"""
Dialectic Layer — Dreamer (사용자 페르소나 & 선호도)
=====================================================
Honcho 스타일 사용자 모델링 — 크로스 세션 페르소나 누적 및
선호도 추적을 담당하는 제3계층.

Life-Harness Layer 4 — User Modeling & Preference Tracking
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from ._base import _init_db

log = logging.getLogger("dialectic")

# ── Honcho 스타일 사용자 모델링 ────────────────────────────
# Cross-session user persona accumulation + preference tracking.

TRAIT_CATEGORIES = {
    "communication_style": ["간결", "상세", "비유적", "직설적", "기술적", "감성적",
                            "격식", "친근", "명령형", "질문형", "은유적"],
    "work_style": ["체계적", "직관적", "실험적", "보수적", "자동화선호", "수동선호",
                   "문서강조", "코드중심", "반복회피", "탐구형", "결정형"],
    "expertise": ["법학", "공학", "Python", "자동화", "연구", "LLM", "시스템설계",
                  "DevOps", "데이터분석", "보안"],
    "value": ["효율성", "정확성", "속도", "완전성", "일관성", "혁신", "안정성",
              "확장성", "유지보수성", "보안성"],
}


def extract_traits_from_text(text: str) -> list:
    """텍스트에서 사용자 특성 후보 추출.

    Args:
        text: 사용자 메시지

    Returns:
        [(trait_type, trait_value, confidence), ...]
    """
    extracted = []

    traits_map = {
        "간결하게|짧게|요약|핵심만": ("communication_style", "간결"),
        "자세히|상세히|구체적으로|설명해": ("communication_style", "상세"),
        "자동화|스크립트|반복|batch": ("work_style", "자동화선호"),
        "문서|업데이트|기록|메모": ("work_style", "문서강조"),
        "버그|장애|에러|오류|테스트": ("work_style", "실험적"),
        "법학|법률|판례|조항": ("expertise", "법학"),
        "공학|엔지니어링|시스템|아키텍처": ("expertise", "공학"),
        "Python|스크립트|모듈|패키지": ("expertise", "Python"),
        "LLM|모델|토큰|프롬프트|어시스턴트": ("expertise", "LLM"),
        "연구|논문|실험|분석": ("expertise", "연구"),
        "효율|성능|최적화|빠르게": ("value", "효율성"),
        "정확|확인|검증|무결성": ("value", "정확성"),
        "안정|견고|에러방지|장애복구": ("value", "안정성"),
        "일관|통일|규칙|표준": ("value", "일관성"),
        "확장|모듈화|재사용": ("value", "확장성"),
    }

    text_lower = text.lower()
    for pattern, (ttype, tvalue) in traits_map.items():
        if re.search(pattern, text_lower):
            # 빈도 기반 자신감 (키워드 등장 횟수 정규화)
            matches = re.findall(pattern, text_lower)
            confidence = min(0.9, 0.3 + len(matches) * 0.15)
            extracted.append((ttype, tvalue, confidence))

    return extracted


def update_user_persona(text: str, source: str = "chat") -> int:
    """사용자 메시지에서 특성 추출 → persona DB 업데이트.

    Args:
        text: 사용자 입력 텍스트
        source: 소스 (chat, dream, command 등)

    Returns:
        업데이트된 특성 수
    """
    traits = extract_traits_from_text(text)
    if not traits:
        return 0

    conn = _init_db()
    now = datetime.now().isoformat()
    updated = 0

    for ttype, tvalue, confidence in traits:
        # 기존 레코드 확인
        cursor = conn.execute(
            "SELECT id, confidence FROM user_personas WHERE trait=? AND value=?",
            (ttype, tvalue),
        )
        row = cursor.fetchone()
        if row:
            # 기존 자신감과 평균
            new_conf = (row[1] + confidence) / 2
            conn.execute(
                "UPDATE user_personas SET confidence=?, source=?, updated_at=? WHERE id=?",
                (new_conf, source, now, row[0]),
            )
        else:
            conn.execute(
                "INSERT INTO user_personas (trait, value, confidence, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ttype, tvalue, confidence, source, now),
            )
        updated += 1

    conn.commit()
    conn.close()
    return updated


def get_user_persona(min_confidence: float = 0.4) -> dict:
    """현재까지 누적된 사용자 페르소나 조회.

    Args:
        min_confidence: 최소 신뢰도 필터

    Returns:
        {trait_category: [(value, confidence), ...], ...}
    """
    conn = _init_db()
    cursor = conn.execute(
        "SELECT trait, value, confidence, source, updated_at "
        "FROM user_personas WHERE confidence >= ? "
        "ORDER BY trait, confidence DESC",
        (min_confidence,),
    )
    persona = {}
    for row in cursor.fetchall():
        ttype, tvalue, conf, src, ts = row
        if ttype not in persona:
            persona[ttype] = []
        persona[ttype].append({
            "value": tvalue,
            "confidence": conf,
            "source": src,
            "updated_at": ts,
        })
    conn.close()
    return persona


def get_persona_summary() -> str:
    """사용자 페르소나 요약 문자열 생성."""
    persona = get_user_persona(min_confidence=0.3)
    if not persona:
        return "아직 충분한 사용자 데이터가 없습니다."

    lines = ["### 🧑 사용자 페르소나 (Honcho)"]
    for category, traits in persona.items():
        top = sorted(traits, key=lambda t: t["confidence"], reverse=True)[:3]
        vals = [f"{t['value']} ({t['confidence']:.1f})" for t in top]
        lines.append(f"- **{category}**: {', '.join(vals)}")

    return "\n".join(lines)


def record_preference(key: str, value: str, source: str = "chat") -> bool:
    """명시적 선호도 기록.

    Args:
        key: 선호도 키 (예: "response_style", "work_hours", "ui_language")
        value: 선호도 값
        source: 소스

    Returns:
        True if recorded
    """
    conn = _init_db()
    now = datetime.now().isoformat()
    cursor = conn.execute(
        "SELECT id, confidence FROM user_preferences WHERE preference_key=?",
        (key,),
    )
    row = cursor.fetchone()
    if row:
        new_conf = min(1.0, row[1] + 0.1)
        conn.execute(
            "UPDATE user_preferences SET preference_value=?, confidence=?, source=?, updated_at=? WHERE id=?",
            (value, new_conf, source, now, row[0]),
        )
    else:
        conn.execute(
            "INSERT INTO user_preferences (preference_key, preference_value, confidence, source, updated_at) "
            "VALUES (?, ?, 0.5, ?, ?)",
            (key, value, source, now),
        )
    conn.commit()
    conn.close()
    log.info(f"⭐ 선호도 기록: {key} = {value}")
    return True


def get_preference(key: str) -> str | None:
    """선호도 조회."""
    conn = _init_db()
    cursor = conn.execute(
        "SELECT preference_value FROM user_preferences WHERE preference_key=? ORDER BY confidence DESC LIMIT 1",
        (key,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def get_all_preferences() -> dict:
    """모든 선호도 조회."""
    conn = _init_db()
    cursor = conn.execute(
        "SELECT preference_key, preference_value, confidence, updated_at FROM user_preferences ORDER BY confidence DESC",
    )
    prefs = {}
    for row in cursor.fetchall():
        prefs[row[0]] = {
            "value": row[1],
            "confidence": row[2],
            "updated_at": row[3],
        }
    conn.close()
    return prefs


# ═══════════════════════════════════════════════════════
# Style Profile 동기화 (Dreamer 페르소나 → style_profile.md)
# ═══════════════════════════════════════════════════════

STYLE_PROFILE_PATH = "/Users/bluesea/Applications/Mjobsidian/wiki/00_Meta/style_profile.md"


def sync_style_profile(persona_path: str = STYLE_PROFILE_PATH) -> str:
    """DB 페르소나 데이터를 style_profile.md로 동기화.

    `## 🤖` 마커 기준 상단 수동 섹션은 보존, 하단만 자동 갱신.
    마커가 없으면 파일 끝에 추가.

    Returns:
        요약 메시지 (변경/오류/스킵)
    """
    try:
        persona = get_user_persona(min_confidence=0.3)
        prefs = get_all_preferences()
        if not persona and not prefs:
            return "⏭️ 동기화할 페르소나 데이터 없음"

        # auto-generated 섹션 생성
        auto_lines = [
            f"> **자동 동기화**: {datetime.now().strftime('%Y.%m.%d %H:%M')}",
            "",
        ]

        for category, traits in persona.items():
            cat_name = {
                "communication_style": "💬 커뮤니케이션 스타일",
                "work_style": "⚙️ 작업 스타일",
                "expertise": "🎓 전문 분야",
                "value": "💎 핵심 가치",
            }.get(category, f"📋 {category}")

            auto_lines.append(f"## {cat_name}")
            for t in sorted(traits, key=lambda x: x["confidence"], reverse=True):
                auto_lines.append(f"- {t['value']} (신뢰도: {t['confidence']:.2f})")
            auto_lines.append("")

        if prefs:
            auto_lines.append("## ⭐ 명시적 선호도")
            for key, val in sorted(prefs.items()):
                auto_lines.append(f"- **{key}**: {val['value']} (신뢰도: {val['confidence']:.2f})")
            auto_lines.append("")

        auto_content = "\n".join(auto_lines)

        # 기존 파일 읽기 → 마커 기준 분할
        ppt = Path(persona_path)
        existing = ppt.read_text(encoding="utf-8") if ppt.exists() else ""

        marker = "## 🤖"
        if marker in existing:
            head = existing[: existing.index(marker)]
        else:
            head = existing.rstrip() + "\n\n"

        new_body = (
            f"## 🤖 자동 동기화 (Dreamer 페르소나)\n\n"
            f"> 아래 항목은 `sync_style_profile()`이 DB 데이터로 자동 갱신합니다.\n\n"
            f"{auto_content}\n"
        )

        tmp_path = ppt.with_suffix('.tmp')
        tmp_path.write_text(head + new_body, encoding="utf-8")
        tmp_path.replace(ppt)

        count = sum(len(v) for v in persona.values()) + len(prefs)
        log.info(f"🎭 Style Profile 동기화 완료: {count}개 항목")
        return f"✅ Style Profile 동기화: {count}개 항목"

    except Exception as e:
        log.error(f"Style Profile 동기화 실패: {e}")
        return f"❌ Style Profile 동기화 실패: {e}"
