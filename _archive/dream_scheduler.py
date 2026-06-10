#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import asyncio
import time
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

# 파이썬 검색 경로 설정 (모듈 로딩 보장)
sys.path.append("/Users/bluesea/.hermes/plugins")
sys.path.append("/Users/bluesea/Applications/Mjauto/Scripts/modules")

from history_manager import HistoryManager
from memory_engine import MemoryEngine
from git_manager import GitManager
from hybrid_router import router
from skill_evolver import SkillEvolver

# 로깅 설정
LOG_FILE = "/Users/bluesea/.hermes/dreamer.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("HermesDreamer")


# ── ExecPlan ────────────────────────────────────────────

class PhaseStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ExecPhase:
    """단일 실행 단계."""
    name: str
    status: PhaseStatus = PhaseStatus.PENDING
    error: Optional[str] = None
    duration: float = 0.0
    _start: float = 0.0

    @property
    def icon(self) -> str:
        return {
            PhaseStatus.PASSED: "✅",
            PhaseStatus.FAILED: "❌",
            PhaseStatus.SKIPPED: "⏭️",
            PhaseStatus.RUNNING: "🔄",
            PhaseStatus.PENDING: "⬜",
        }[self.status]


class ExecPlan:
    """구조화된 실행 계획 — 단계별 상태 추적 및 오류 격리.

    각 phase를 개별적으로 실행하여 하나의 실패가 전체를 중단시키지 않음.
    """
    def __init__(self, phases: list[ExecPhase]):
        self._phases = list(phases)
        self._phase_map = {p.name: p for p in self._phases}

    def start(self, name: str) -> None:
        p = self._phase_map[name]
        p.status = PhaseStatus.RUNNING
        p._start = time.time()
        logger.info(f"▶️ [{name}] 시작")

    def pass_(self, name: str) -> None:
        p = self._phase_map[name]
        p.status = PhaseStatus.PASSED
        p.duration = time.time() - p._start
        logger.info(f"✅ [{name}] 완료 ({p.duration:.1f}s)")

    def fail(self, name: str, error: str, exc: bool = False) -> None:
        p = self._phase_map[name]
        p.status = PhaseStatus.FAILED
        p.error = error
        p.duration = time.time() - p._start
        if exc:
            logger.error(f"❌ [{name}] 실패: {error}", exc_info=True)
        else:
            logger.warning(f"⚠️ [{name}] 실패: {error}")

    def skip(self, name: str, reason: str = "") -> None:
        p = self._phase_map[name]
        p.status = PhaseStatus.SKIPPED
        reason = f" — {reason}" if reason else ""
        logger.info(f"⏭️ [{name}] 스킵{reason}")

    def run(self, name: str, coro, exc_info: bool = False) -> None:
        """문맥 관리자처럼 phase 실행. 실패해도 다음 phase 진행."""
        self.start(name)
        try:
            asyncio.get_event_loop().run_until_complete(coro) if asyncio.iscoroutine(coro) else coro
        except Exception as e:
            self.fail(name, str(e), exc=exc_info)
        else:
            self.pass_(name)

    async def arun(self, name: str, coro, exc_info: bool = False):
        """비동기 phase 실행."""
        self.start(name)
        try:
            await coro
        except Exception as e:
            self.fail(name, str(e), exc=exc_info)
        else:
            self.pass_(name)

    def get_summary(self) -> str:
        """실행 요약 문자열 생성."""
        passed = sum(1 for p in self._phases if p.status == PhaseStatus.PASSED)
        failed = sum(1 for p in self._phases if p.status == PhaseStatus.FAILED)
        skipped = sum(1 for p in self._phases if p.status == PhaseStatus.SKIPPED)
        total = len(self._phases)
        lines = [
            f"\n📊 ExecPlan 요약 ({passed}/{total} 통과, {failed} 실패, {skipped} 스킵)",
        ]
        for p in self._phases:
            dur = f" ({p.duration:.1f}s)" if p.duration else ""
            err = f" — {p.error}" if p.error else ""
            lines.append(f"  {p.icon} {p.name}{dur}{err}")
        return "\n".join(lines)

    def get_overall_status(self) -> PhaseStatus:
        """전체 성공 여부: 모든 phase가 passed/skipped면 PASSED."""
        for p in self._phases:
            if p.status == PhaseStatus.FAILED:
                return PhaseStatus.FAILED
        return PhaseStatus.PASSED


# ── 상수 ────────────────────────────────────────────────

VAULT_PATH = "/Users/bluesea/Applications/Mjobsidian"
HOT_FILE = f"{VAULT_PATH}/wiki/00_Meta/hot.md"
GOAL_FILE = "/Users/bluesea/.hermes/active_goal.txt"
CONST_FILE = "/Users/bluesea/.hermes/constitution.md"
CHAT_HISTORY = "/Users/bluesea/.hermes/chat_history.json"
REPO_PATH = VAULT_PATH

AUDIT_SYSTEM_PROMPT = (
    "당신은 헤르메스 AI 시스템의 일일 감사관(Auditor)입니다. "
    "주어진 정보를 바탕으로 '목표 진척도'와 '헌법 준수 감사' 두 항목을 각각 2~3문장으로 간결하게 평가하십시오."
)


# ── 헬퍼 ─────────────────────────────────────────────────

def _read_file_safe(path: str) -> str:
    """파일 읽기 (없으면 빈 문자열)."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _read_file_full(path: str) -> str:
    """파일 전체 읽기 (없으면 빈 문자열)."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def _write_file_safe(path: str, content: str) -> None:
    """파일 쓰기 (디렉토리 자동 생성)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _update_hot_file(section_header: str, content: str) -> None:
    """hot.md의 특정 섹션을 업데이트하거나 추가."""
    hot_content = _read_file_full(HOT_FILE)
    if not hot_content:
        logger.warning(f"⚠️ hot.md 없음: {HOT_FILE}")
        return

    insert = f"{section_header}\n{content}\n\n"
    if section_header in hot_content:
        hot_content = re.sub(
            rf'{re.escape(section_header)}\n.*?(?=## |\Z)',
            insert, hot_content, flags=re.DOTALL
        )
    else:
        # 맨 앞에 삽입
        if hot_content.startswith("---"):
            # frontmatter 뒤에 삽입
            parts = hot_content.split("---\n", 2)
            if len(parts) >= 3:
                hot_content = parts[0] + "---\n" + parts[1] + "---\n" + insert + parts[2]
            else:
                hot_content = insert + hot_content
        else:
            hot_content = insert + hot_content

    _write_file_safe(HOT_FILE, hot_content)


def _htmlize(text: str) -> str:
    """Markdown bold → HTML bold 변환."""
    return re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)


def _send_telegram(msg: str) -> None:
    """텔레그램 알림 발송 (백그라운드)."""
    try:
        subprocess.Popen(
            ["python3", "/Users/bluesea/Applications/Mjauto/Scripts/send_telegram_msg.py", msg]
        )
    except Exception as e:
        logger.warning(f"텔레그램 발송 실패: {e}")


# ── Ingest/MemoryEngine용 비동기 LLM 래퍼 ─────────────────

def _make_llm_func():
    async def async_llm_func(prompt):
        res, name = router.send_completion(prompt)
        return res, name
    return async_llm_func


# ── 핵심 Dreaming 실행 ──────────────────────────────────

async def run_daily_dream():
    logger.info("🌙 새벽 3시 지능형 Dreaming 스케줄러 기동 완료.")

    plan = ExecPlan([
        ExecPhase("메모리 로드"),
        ExecPhase("Dreaming 요약"),
        ExecPhase("목표/헌법 감사"),
        ExecPhase("Git 백업"),
        ExecPhase("패턴 감지/스킬 생성"),
        ExecPhase("주간 Curator"),
    ])

    # ── Phase 1: 메모리 로드 ──
    plan.start("메모리 로드")
    try:
        history_mgr = HistoryManager(CHAT_HISTORY)
        history_data = history_mgr.get_history_for_llm()
        if not history_data:
            logger.warning("⚠️ 최근 대화 로그가 존재하지 않습니다. 기본형 요약을 진행합니다.")
        engine = MemoryEngine(vault_path=VAULT_PATH)
        llm_func = _make_llm_func()

        # style_profile 로드 상태 기록
        style_profile_path = os.path.join(VAULT_PATH, "wiki", "00_Meta", "style_profile.md")
        if os.path.exists(style_profile_path):
            sp_size = os.path.getsize(style_profile_path)
            with open(style_profile_path, "r", encoding="utf-8") as _spf:
                sp_lines = len(_spf.readlines())
            logger.info(f"📜 style_profile 로드 완료 ({sp_size}bytes / {sp_lines}줄) — Dreaming 주입 대상")
        else:
            logger.warning("⚠️ style_profile.md 없음 — 스타일 가이드 미적용")

        plan.pass_("메모리 로드")
    except Exception as e:
        plan.fail("메모리 로드", str(e))
        # 메모리 로드 실패 시 이후 단계 진행 불가
        logger.error(f"❌ 메모리 로드 실패 — Dreaming 중단: {e}")
        print(plan.get_summary())
        return

    # ── Phase 2: Dreaming 요약 ──
    try:
        res_msg = await engine.dream(history_data=history_data, llm_func=llm_func)
        logger.info(f"✨ {res_msg}")

        # engine.dream() 내부에서 sync_style_profile() 자동 호출 완료
        style_profile_path = os.path.join(VAULT_PATH, "wiki", "00_Meta", "style_profile.md")
        if os.path.exists(style_profile_path):
            sp_mtime = datetime.fromtimestamp(os.path.getmtime(style_profile_path))
            logger.info(f"📝 style_profile 동기화 완료 ({sp_mtime.strftime('%Y.%m.%d %H:%M')})")
        else:
            logger.warning("⚠️ style_profile.md 없음 — sync_style_profile 스킵")

        plan.pass_("Dreaming 요약")
    except Exception as e:
        plan.fail("Dreaming 요약", str(e), exc=True)
        logger.info("⏭️ Dreaming 실패 — 이후 단계는 선택적 실행")

    # ── Phase 3: 목표/헌법 감사 ──
    plan.start("목표/헌법 감사")
    try:
        goal_text = _read_file_safe(GOAL_FILE)
        const_text = _read_file_safe(CONST_FILE)

        if goal_text or const_text:
            audit_user = ""
            if goal_text:
                audit_user += f"[장기 목표]\n{goal_text}\n\n"
            if const_text:
                audit_user += f"[헌법 핵심 조항 요약]\n{const_text[:500]}\n\n"

            # MJ님 문체 스타일 (Obsidian Codex)
            codex_style_path = os.path.join(VAULT_PATH, "wiki", "Obsidian Codex", "style_profile.md")
            if os.path.exists(codex_style_path):
                try:
                    with open(codex_style_path, "r", encoding="utf-8") as _sf:
                        style_text = _sf.read().strip()
                    if style_text:
                        audit_user += f"[MJ님 문체 스타일]\n{style_text[:1000]}\n\n"
                        logger.info(f"📜 Obsidian Codex style_profile 반영 ({len(style_text)}자 → 1000자 트렁케이트)")
                except Exception as _se:
                    logger.warning(f"⚠️ Obsidian Codex style_profile 읽기 실패: {_se}")
            else:
                logger.info("⏭️ Obsidian Codex/style_profile.md 없음 — 문체 스타일 미반영")

            # 최근 5개 히스토리, 각 100자
            history_text = ""
            if history_data and isinstance(history_data, list):
                history_text = "\n".join(
                    f"{m.get('role', 'user')}: {str(m.get('content', ''))[:100]}"
                    for m in history_data[-5:]
                )
            else:
                history_text = "히스토리 없음"
            audit_user += f"[오늘의 대화 요약]\n{history_text}\n\n평가를 작성해 주세요."

            audit_messages = [
                {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
                {"role": "user", "content": audit_user},
            ]
            audit_res, _ = await llm_func(audit_messages)
            logger.info(f"📋 감사 결과: {audit_res}")

            # hot.md 기록
            _update_hot_file("## ⚖️ 최근 감사 결과 (Dreaming)", audit_res)

            # 텔레그램 알림
            html_msg = _htmlize(audit_res)
            _send_telegram(f"⚖️ <b>일일 목표 & 헌법 감사 리포트</b>\n\n{html_msg}")
        else:
            logger.info("⏭️ 목표 및 헌법 파일 없음 — 감사 스킵")

        plan.pass_("목표/헌법 감사")
    except Exception as e:
        plan.fail("목표/헌법 감사", str(e))

    # ── Phase 4: Git 백업 ──
    plan.start("Git 백업")
    try:
        if os.path.exists(os.path.join(REPO_PATH, ".git")):
            git_mgr = GitManager(repo_path=REPO_PATH)
            sync_res = git_mgr.sync(message="Harness Daily Dreaming (03:00) Auto-Sync")
            logger.info(f"💾 {sync_res}")
        else:
            logger.info("⚠️ Git 저장소가 초기화되지 않음 — 동기화 스킵")
        plan.pass_("Git 백업")
    except Exception as e:
        plan.fail("Git 백업", str(e))

    # ── Phase 5: 패턴 감지/스킬 생성 ──
    plan.start("패턴 감지/스킬 생성")
    try:
        from bio_memory_engine import BioMemoryEngine
        bio_engine = BioMemoryEngine(vault_path=VAULT_PATH)
        repeated = bio_engine.get_repeated_patterns(threshold=3)
        if repeated:
            evolver = SkillEvolver()
            for pattern in repeated:
                evolver.maybe_evolve(
                    task_name=pattern.get("summary", "unknown")[:80],
                    evidence=[pattern.get("summary", "")],
                    frequency=pattern.get("frequency", 1),
                )
            logger.info(f"🎯 Dreaming 스킬 진화 완료: {len(repeated)}개 패턴 처리")
        else:
            logger.info("✅ 반복 패턴 없음 (3회 미만)")
        plan.pass_("패턴 감지/스킬 생성")
    except Exception as e:
        plan.fail("패턴 감지/스킬 생성", str(e))

    # ── Phase 6: 주간 Curator (일요일만) ──
    if datetime.now().weekday() == 6:  # Sunday
        plan.start("주간 Curator")
        try:
            from skill_evolver import curate_skills, get_curator_report
            result = curate_skills(dry_run=False)
            report = get_curator_report()
            logger.info(f"📊 Curator 완료: {result['stats']}")

            _update_hot_file("## 📦 주간 Curator 보고서", report)
            plan.pass_("주간 Curator")
        except Exception as e:
            plan.fail("주간 Curator", str(e))
    else:
        plan.skip("주간 Curator", "일요일 아님")

    # ── 결과 요약 ──
    summary = plan.get_summary()
    logger.info(summary)
    print(summary)


if __name__ == "__main__":
    asyncio.run(run_daily_dream())
