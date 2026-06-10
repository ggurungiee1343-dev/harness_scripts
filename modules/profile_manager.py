"""
Harness V2.5 — 프로필 관리 모듈
==================================
MJ님의 작업 모드에 따른 프롬프트/행동 분기

- writer: 창의적/설명적/서술적 스타일
- analyst: 분석적/비판적/데이터 중심 스타일
- default: 기본 (분석+창작 균형)
"""

import os
from pathlib import Path

PROFILE_FILE = Path("/Users/bluesea/.hermes/active_profile.txt")
_DEFAULT_PROFILE = "default"


# ── 프로필 정의 ────────────────────────────────────────────
PROFILES = {
    "default": {
        "label": "기본",
        "style": "분석+창작 균형형",
        "prefix": "",
        "description": "MJ님 기본 스타일 — 간결하고 정확한 역피라미드 구조",
    },
    "writer": {
        "label": "작가",
        "style": "창의적/설명적 서술",
        "prefix": (
            "당신은 창의적인 작가 모드입니다. "
            "설명이 필요한 주제에 대해 풍부한 비유와 예시를 사용하세요. "
            "문장은 유려하고 읽기 쉬워야 합니다."
        ),
        "description": "풍부한 설명과 비유, 창의적 표현 중심",
    },
    "analyst": {
        "label": "분석가",
        "style": "분석적/비판적/데이터 중심",
        "prefix": (
            "당신은 분석가 모드입니다. "
            "데이터와 근거를 우선시하고, 가설을 검증하며, "
            "핵심 인사이트를 도출하세요. 결론에는 반드시 근거를 제시하세요."
        ),
        "description": "데이터 중심 분석, 근거 기반 결론",
    },
}


def set_profile(profile_name: str) -> str:
    """프로필 전환."""
    if profile_name not in PROFILES:
        return f"❌ 알 수 없는 프로필: {profile_name} (가능: {', '.join(PROFILES.keys())})"
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(profile_name, encoding="utf-8")
    return f"✅ 프로필 전환 완료: **{PROFILES[profile_name]['label']}** ({PROFILES[profile_name]['style']})"


def get_active_profile() -> str:
    """현재 활성 프로필 조회."""
    if PROFILE_FILE.exists():
        profile = PROFILE_FILE.read_text(encoding="utf-8").strip()
        if profile in PROFILES:
            return profile
    return _DEFAULT_PROFILE


def get_profile_prefix() -> str:
    """현재 프로필의 시스템 프롬프트 프리픽스 반환."""
    profile = get_active_profile()
    return PROFILES.get(profile, PROFILES[_DEFAULT_PROFILE]).get("prefix", "")


def get_profile_info() -> dict:
    """현재 프로필 정보 반환."""
    profile = get_active_profile()
    info = PROFILES.get(profile, PROFILES[_DEFAULT_PROFILE]).copy()
    info["name"] = profile
    return info


def list_profiles() -> str:
    """모든 프로필 목록 반환."""
    lines = ["📋 **사용 가능한 프로필**\n"]
    for name, info in PROFILES.items():
        marker = "👉" if get_active_profile() == name else "  "
        lines.append(f"{marker} `/profile {name}` — {info['label']} ({info['description']})")
    return "\n".join(lines)
